# World-optional chats: play scopes, bindings and navigation

**Status:** first slice of [World-Optional Chat Scopes](roadmap/sprints/world-optional-chat-scopes.md) (Sprint 107, Wave 3). It is implemented as:
- `grimoire.chats`: pure values, payload codec and navigation index;
- `grimoire.chatstore`: a store-backed service;
- an additive record family in [the native store](STORAGE.md#play-scopes-and-chats-107).

There is **no app and no playable chat yet**. There are no messages, scenes or model calls. The first world-less text chat is Sprint 035 (Wave 5). The design decisions are in [CHAT_MODEL](roadmap/CHAT_MODEL.md).

## What exists

- A chat can begin from:
  - one standalone **character**;
  - a **scenario**;
  - a **lorebook**;
  - or a bare **premise**.
- It needs **no world**. `world: null` is the default, not an error.
- Starting a chat writes, in one guarded store transaction:
  - a **play scope**: an automatically created campaign namespace `campaign:<id>`;
  - a **chat**: `campaign:<id>#chat/<chat_id>`;
  - one actor instance per character or persona binding.
- Sources can later be attached or detached, the persona replaced, and the chat relabelled. The scope can be named as a campaign, given a world, or given Session Zero data. None of these changes the chat ID, its launch provenance, its actor IDs or its creation time.
- Navigation finds the same canonical chat from:
  - Chats/Recent;
  - each character, scenario or lorebook it references;
  - its campaign;
  - its world.

## Identity

| Value | Text | Meaning |
|---|---|---|
| `ScopeRef(campaign_id)` | `campaign:<campaign_id>` | the play scope, which is the campaign namespace that gives actors their `InstanceRef`s |
| `ChatRef(campaign_id, chat_id)` | `campaign:<campaign_id>#chat/<chat_id>` | one conversation thread, homed in exactly one scope |

- These are separate families from definitions, instances and V1 ids. `parse_ref` never returns them; each has its own strict `parse`. See [IDENTITY](IDENTITY.md#reference-families).
- A chat is never reparented.
- Starting a second chat with the same card creates a **new scope**, so it gets new actors with independent histories. The exception is when the caller deliberately continues an existing scope.

## Model (`grimoire.chats.model`)

### Roles

| Role | Binds definitions of kind | Creates an actor |
|---|---|---|
| `character` | `character` | yes (instance kind `character`) |
| `persona` | `character` | yes: the player's body. **No control assignment is created**; that belongs to 003/035. |
| `scenario` | `scenario` | no. A scenario is never a fake `Agent` NPC. |
| `lorebook` | `lorebook` | no |

### Launch

`Launch(kind, ref | premise)` is immutable.
- A launch ref is bound at sequence 0 under the role of the same name.
- A premise is bounded text (≤ 4000 chars) with no source.

### `SourceBinding`

A binding stores:
- the reference exactly as chosen: a pinned revision, or unpinned to follow the head explicitly;
- `resolved_revision`: what it meant when introduced;
- `introduced_seq` / `introduced_at`;
- its actor, if any;
- for a detached binding, `detached_seq` / `detached_at`.

Rules:
- Detaching writes a **tombstone**; the binding is never deleted.
- A chat has at most one *active* binding per role and qualified entity, and at most one active persona.
- Re-attaching adds a new binding.

### Roster

The roster lists every actor that has taken part in the chat. **It only grows.** Detaching a character keeps its actor and history.

A new actor is `prior_history: not_witnessed`: it saw none of the chat's earlier history. A deliberately reused campaign actor is `campaign_continuity`. Nothing in this slice grants knowledge of anything.

### `ChatRecord`

- `label` (≤ 200 chars) is display text only. It is never identity, a lookup key or a credential.
- `seq` is a local change counter. It is **not comparable across devices**; merging belongs to 019.
- `last_activity` is advanced by every chat write. 035 will also advance it per post.

### `PlayScope`

- `title`: `None` means an unnamed quick scope, which is shown as its chat rather than as a campaign. Naming changes presentation only.
- `world`: a `WorldBinding(source, observed_manifest_etag, attached_at)`.
  - The etag is a **drift check only, not a pin**: the store keeps no old manifest content.
  - Pinning to a release (a frozen heads map) belongs to 042/056.
- `session_zero`: 016's configuration, kept **verbatim and uninterpreted**. 035 executes it, and it **must authenticate the setter** (016's player-input channel) before calling `set_session_zero`. The service does not check who set it.

### Bounds

- At most 256 bindings, tombstones included. Exceeding the bound raises `BindingLimit`.
- At most 256 roster actors.
- Opaque JSON is nested at most 64 levels.
- Unknown payload fields are preserved in `extras`.

### Codec

Schemas are `grimoire.chat/1` and `grimoire.play-scope/1`. The envelope owns the address and the schema id, so a payload repeating `schema`, `address`, `campaign_id`, `chat_id` or `ref` is refused (`identity_in_payload`).

Unknown fields inside a known structure are refused with a reason. An unknown schema raises `unsupported_schema`, and such a chat is listed by navigation but never rewritten.

## Navigation (`grimoire.chats.navigation`)

`NavigationIndex.build(chats, scopes)` is a pure function of canonical records. It holds only IDs, refs, labels, titles, timestamps and problem codes: never premise text, extras, Session Zero data or transcript content.

Each query returns `ChatRef`s, each chat at most once:

| Query | Returns |
|---|---|
| `all_chats()` / `recent(limit ≤ 1000)` | every chat file, newest activity first. Chats this version cannot decode (`undecodable`) and chats without a scope (`orphan_chat`) are included and flagged, so nothing is lost from Chats. |
| `by_source(ref, role=None, include_detached=False)` | chats whose launch or a binding is the same qualified entity (`entity_key`). A roster actor counts through its binding: only while that binding is active, or with `include_detached`. A detached character or replaced persona is therefore not found by default, although its actor stays on the roster. A roster entry without any binding (a hand edit) always counts. A pinned query also requires that revision. Names are never matched. Definition links (`alternate_portrayal`, `derived_variant`, `same_individual`) are never followed: equivalence filters belong to 022/065. The launch always counts as provenance. Detached bindings count only on request. |
| `by_scope(id)`, `campaigns()`, `by_world(source)` | chats in a scope; the scopes the player named; chats in scopes with that world |

**No index is persisted in this slice.** `ChatLibrary.navigation()` rebuilds it from `store.load()` on every call, so "deleting the cache" is equivalent to rebuilding. `to_json()` is the deterministic form any later persisted cache (065) must equal. The index is never built from an LLM call; neither package imports a provider.

## Service (`grimoire.chatstore.ChatLibrary`)

`ChatLibrary(store, handle, clock=..., new_id=..., owner=...)` is **trusted application code**; it is never handed to a worker. Clock and id generator are required and injectable, so tests are deterministic.

### Two-phase writes

Every mutating method returns a `ChatChange`:
1. `preview()` returns the exact bytes per logical path, for a human to approve.
2. `commit(approvals=None)` performs one store transaction with CAS on every touched record. Under an approval-gated policy, register an approval for each previewed path and pass them in.

`commit` re-checks under the store lock that a newly allocated campaign is still absent and that each new actor id is still unused by any kind. A conflict raises `StaleWrite` and never reallocates, because the approvals pinned those bytes.

### Methods

- `start_chat(launch, attach=…, persona=…, label=…, scope=None, reuse_actors=None)`:
  - **New scope:** the campaign id is drawn until `campaign_presence` is `absent`; a look-alike folder counts as taken.
  - **Continuing a scope:** pass `scope=`. A campaign without `scope.json` (one from before 107, or from 026) gets one lazily. To reuse existing campaign actors instead of creating new ones, pass `reuse_actors={(role, entity_key): InstanceRef}`. A reused actor must be a character instance of the same entity.
- `read_chat(chat)` / `read_scope(scope)` return the decoded record and its etag, the `expected` value for an edit.
- `attach_source(chat, ref, role, expected=…, actor=None)`: `actor=` explicitly reuses an existing campaign actor of the same entity, for example when re-attaching a detached character. The default is a new actor.
- `detach_source(…)`: works on a broken reference; it is the repair path.
- `set_persona(chat, ref | None, expected=…, actor=None)`: detaches the current persona and binds the new one. Choosing the same persona entity again keeps its actor while that actor's instance exists; if the file has vanished, a new actor is minted.
- `set_label(…)`.
- Edits keep the stored envelope's unknown top-level fields and original `owner`.
- `name_scope`, `attach_world`, `detach_world`, `set_session_zero`.
- `open_chat(chat)` returns the record, its scope and `broken`: every launch, active binding, roster actor and world reference that does not resolve now. Reasons are `missing_source`, `missing_entity`, `missing_revision`, `excluded_source`, `entity_dropped`, `missing_actor` and `world_drift`. Opening never fails because a source is missing.
  - `ChatView.problems` reports `orphan_chat` when the scope record is missing, and `undecodable_scope` when it exists but cannot be read.
  - Opening a chat file whose envelope the store cannot read raises that store error (`UnsupportedFormat`, `MalformedRecord`, …). Navigation lists such a file as `undecodable`.
- `navigation()`.

### Validation

New bindings are validated with `store.check_definition`. It applies `load()`'s rules to **that one source only**:
- an implicit single-revision head resolves;
- excluded sources and dropped entities are refused.

Its cost does not grow with the library, and no mutating method loads the whole library (tested). Limits:
- An `identity_conflict` that only a whole-library build can see is not caught at write time. `open_chat` reports it.
- Validation happens when a change is created. A source deleted before `commit` becomes a broken binding that `open_chat` reports.

### Actor ids

Actor ids are `<entity_id>-<n>`, the smallest number used neither by an instance file of any kind in the campaign nor by this chat's roster, even when an actor's file has vanished. **Limit:** an instance file deleted by hand can free its id for a *different* chat in the same campaign. A look-alike instance file (`Mara-1.json`) is invisible to that listing, so the commit fails with `NameCollision` rather than reallocating.

### Actor instances

Actor instances are written as `grimoire.record/1` with:
- the 009 payload;
- the preserved top-level `"schema": "grimoire.entity/0"`;
- `derived_from` set to the binding ref;
- `base` / `base_etag` set to the resolved revision.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Messages, scene segments, events, evidence and what an added actor learns | 020/021/027 |
| Control assignment for the persona's actor | 003/035 |
| Effective portrayal composition and derivation-aware navigation filters | 022/065 |
| Card import | 037 |
| World promotion and release pinning | 042/056 |
| Cross-device merge of chat metadata | 019/028 |
| A persisted navigation cache and the richer library browser | 065 |

Binding a source creates no event, belief or evidence record and never makes anyone aware of its content.
