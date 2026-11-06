# World-optional chats: play scopes, bindings and navigation

**Status:** first slice of [World-Optional Chat Scopes](roadmap/sprints/world-optional-chat-scopes.md) (Sprint 107, Wave 3). It is implemented as:
- `grimoire.chats`: pure values, payload codec and navigation index;
- `grimoire.chatstore`: a store-backed service;
- an additive record family in [the native store](STORAGE.md#play-scopes-and-chats-107).

There is **no app yet**. Chats are played headlessly by Sprint 035's runner, which stores each turn beside the chat ([SCENES](SCENES.md)). The design decisions are in [CHAT_MODEL](roadmap/CHAT_MODEL.md).

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
- A launch ref is bound at sequence 0 under the role of the same name. `start_chat` records the launch with the ref its binding holds, so a default launch names the pinned revision it began with ([pin by default](REBASE.md#pin-by-default)).
- A premise is bounded text (≤ 4000 chars) with no source.
- A bound imported scenario (`grimoire.scenario/1`) is not followed by a `lorebook` binding: its chat's snapshot selects the lorebook that the scenario's selected revision names, so its lore follows the scenario through updates, re-pins and detaching ([CARDS](CARDS.md#chats)). `detach_source` on such an implied lorebook is the player's opt-out: it records a binding of it, already detached, and a chat with a `lorebook` tombstone and no active binding for that entity never selects it again until it is attached.

### `SourceBinding`

A binding stores:
- its reference: a pinned revision (the default: `ChatLibrary` pins an unpinned ref to the revision it resolves to when it is bound, see [pin by default](REBASE.md#pin-by-default)), or unpinned to follow the head, only when the caller asks with `follow=True`. A chat stored unpinned by an earlier build keeps reading and following, and is never rewritten;
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
- `last_activity` is advanced by every chat write. 035's runner does not advance it per post yet (a later 035 milestone).

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

`commit` re-checks under the store lock that a newly allocated campaign is still absent (its folder and its sovereignty ledger) and named by no security-ledger grant or approval, that each new actor id is still outside [`campaign_taken_ids`](#taken-instance-ids), that each reused actor's instance file still has the etag it was checked at (a file replaced by hand may hold a different individual under the same id: `changed`, or `absent` if deleted), and that no reused actor's id has since been taken by another kind (`instance_id_conflict`). The approvals passed to `commit` for its own writes do not count against these checks: an approval whose id is supplied for an address this change writes, and whose digest is of exactly the bytes it writes there, names the new campaign and actor because that is how a gated change creates them. Any other approval, of the same address or not, still reserves. A conflict raises `StaleWrite` and never reallocates, because the approvals pinned those bytes.

### Methods

- **Pinning.** `start_chat`, `attach_source` and `set_persona` pin every unpinned ref to the revision `check_definition` resolves it to then (the revision whose bytes were checked and that a new actor is based on), the launch included. A reused actor (`actor=`, `reuse_actors`, or the same persona chosen again) given an unpinned ref is pinned to the revision it plays, by 042's ancestor rule: the one revision its active pinned bindings in the campaign's chats agree on (so an explicit rebind counts), else its instance's pinned `source`, else, with no `source`, its `base`; the same persona chosen again keeps its current pinned revision. When the chats disagree, a chat of the campaign cannot be read, or the instance's `source` follows its head, the ref is pinned to the revision it resolves to now. The played revision is read from the same instance file whose etag the commit re-checks. It is never a newer head nobody reviewed for that actor. A pinned ref is bound as given. `follow=True` (keyword) binds unpinned refs as given instead, following their heads; no product path passes it. A later revision reaches a pinned chat only through 042's reviewed rebase ([REBASE](REBASE.md#pin-by-default)).
- `start_chat(launch, attach=…, persona=…, label=…, scope=None, reuse_actors=None, follow=False)`:
  - **New scope:** the campaign id is drawn until both `campaign_presence` and `campaign_authority_presence` are `absent` and no grant or approval in the security ledger names it (`grimoire.ledgers.security_campaign_ids`); a look-alike folder counts as taken. Both ledgers outlive a hand-deleted `campaigns/<id>` folder: a new campaign given that id would inherit the sovereignty ledger's (`authority/campaigns/<id>`) controls, and a grant scoped to `campaigns/<id>`, or to anything under it, would cover the new campaign. So the id stays taken while either names it. The security ledger is scanned as the sovereignty ledger is for [taken instance ids](#taken-instance-ids): raw, revoked grants included, failing closed with `TakenIdsUnlisted`. It is read once when planning a new scope, and only once a drawn id is otherwise free, and once more at commit; a campaign id it was just found not to name cannot have its individuals named either, so the new scope's actor numbering does not read it again. New actors in the new scope are then numbered outside `campaign_taken_ids` as in an existing one.
  - **Continuing a scope:** pass `scope=`. A campaign without `scope.json` (one from before 107, or from 026) gets one lazily. To reuse existing campaign actors instead of creating new ones, pass `reuse_actors={(role, entity_key): InstanceRef}`. A reused actor must be a character instance of the same entity.
- `read_chat(chat)` / `read_scope(scope)` return the decoded record and its etag, the `expected` value for an edit.
- `attach_source(chat, ref, role, expected=…, actor=None, follow=False)`: `actor=` explicitly reuses an existing campaign actor of the same entity, for example when re-attaching a detached character. The default is a new actor.
- `detach_source(…)`: works on a broken reference; it is the repair path.
- `set_persona(chat, ref | None, expected=…, actor=None, follow=False)`: detaches the current persona and binds the new one. Choosing the same persona entity again keeps its actor while that actor's instance exists; if the file has vanished, a new actor is minted.
- `set_label(…)`.
- Edits keep the stored envelope's unknown top-level fields and original `owner`, including an absent one. Only new records get the library's `owner`.
- `name_scope`, `attach_world`, `detach_world`, `set_session_zero`.
- `open_chat(chat)` returns the record, its scope and `broken`: every launch, active binding, roster actor and world reference that does not resolve now. Reasons are `missing_source`, `missing_entity`, `missing_revision`, `excluded_source`, `entity_dropped`, `missing_actor` and `world_drift`. Opening never fails because a source is missing.
  - `ChatView.problems` reports `orphan_chat` when the scope record is missing, and `undecodable_scope` when it exists but cannot be read.
  - Opening a chat file whose envelope the store cannot read raises that store error (`UnsupportedFormat`, `MalformedRecord`, …). Navigation lists such a file as `undecodable`.
- `navigation()`.

### Validation

New bindings are validated with `store.check_definition`. It applies `load()`'s rules to the bound source, and consults the store's shared, cached whole-library exclusions ([`source_facts`](STORAGE.md#read-only-helpers-107)). `start_chat` verifies them once for all its bindings; `attach_source`, `set_persona` and `attach_world` verify once each:
- an implicit single-revision head resolves;
- excluded sources, including one excluded only by a cross-source `identity_conflict`, and dropped entities are refused.

No mutating method calls `load()` (tested). A changed source is scanned again; an unchanged library costs one `stat` per manifest and revision entry, once per `start_chat`, plus one manifest read (an etag check) and one revision read per bound definition (see [STORAGE](STORAGE.md#read-only-helpers-107)). Limits:
- Validation happens when a change is created. A source deleted before `commit` becomes a broken binding that `open_chat` reports.

### Actor ids

Actor ids are `<entity_id>-<n>` (`<entity_id>-<tag>-<n>` in a library with a device identity, so two devices never draw the same one offline: [DEVICE_SYNC](DEVICE_SYNC.md#per-device-ids)), the smallest number not in `campaign_taken_ids(store, campaign_id)` (below) and not in this chat's roster. So an id stays taken when an actor's file has vanished, whichever chat's roster or record names it. A look-alike instance file (`Mara-1.json`) is invisible to the instance listing, so the commit fails with `NameCollision` rather than reallocating. An actor to reuse whose id another kind also uses is refused (`instance_id_conflict`): `load()` leaves both out.

### Taken instance ids

`grimoire.chatstore.campaign_taken_ids(store, campaign_id)` is the one reservation set: every instance id a new individual in the campaign must not take, so a hand-deleted individual's id never goes to a different one who would inherit its past. 107 checks it at allocation and again under the store lock at commit (a clash raises `StaleWrite`). `spent=` names approvals (id, address, digest) that the commit itself spends, so a writer's own approvals do not reserve the ids they approve. Every instance allocator (024, 026, 037) must use it the same way. It reserves:

- every instance file's id, of any kind;
- every instance id of the campaign named anywhere in a relation file (017), a chat file's roster or bindings (107), an accepted-event file (021), an events, evidence or knowledge-log file (020), or a transcript page (036). These are scanned raw, so a record that fails decoding still protects its ids. 020 persists no bare control ids;
- every holder with a holdings file (023) and every subject with a campaign placement folder (`placements/<kind>/<id>/`, 018), from file names only. A look-alike (`Holdings/`, `Character/`, `Mara-2.json`, `Mara-2/`) keeps the id it folds to taken;
- every instance id named inside a holder file (023: a move's holder, target and `peers`), scanned raw, so a counterparty whose own files were deleted keeps its id;
- every instance id named inside a unique item's `possession` record (034: owners, holders and wearers, current and logged), scanned raw. An instance file the store cannot read as a record could be an item, so every `<kind>/<id>` in its bytes is reserved; only one whose bytes cannot be read fails allocation closed ([POSSESSIONS](POSSESSIONS.md#instance-ids-stay-taken));
- every `actor_id` of the campaign in its sovereignty ledger (010b, `authority/campaigns/<id>/`: control changes and delegations), scanned raw with no replay or chain check, so a hand-deleted PC's id never makes a new actor that player's PC;
- every actor of the campaign named in its adjudication ledger (029, `authority/adjudication/<id>/`: frames, rulings and decisions) and every actor of an obstacle in its obstacle ledger (040, `authority/obstacles/<id>/`), scanned raw in the same way ([ADJUDICATION](ADJUDICATION.md#persistence), [OBSTACLES](OBSTACLES.md#persistence));
- every instance id of the campaign that a grant's `scope` or an approval's `resource` in the security ledger (010b, `authority/security/`) names: an instance file (`instances/<kind>/<id>.json`), a holder file (`holdings/<kind>/<id>.json`), a placement folder (`placements/<kind>/<id>`), or anything finer under one (`grimoire.ledgers.security_instance_ids`). A grant outlives a hand delete of the individual it was scoped to and would cover a new one given its id. The ledger is scanned raw, with no replay or chain check. Revoked grants still reserve, because repairing a broken ledger tail (`repair_ledger`) can undo a revocation. Segments are folded as look-alike names are. A decision's `resource` reserves nothing, since an audit record covers nothing later. A grant on a whole campaign, family or kind folder names no individual and reserves no instance id. A grant scoped to a `kept/` address (a [sync kept version](SYNC.md#keep-both), named by a hash of the record's path) is not reserved: it can only write `.conflicts` sidecar copies, never the record, so revoke it by hand when deleting the record it was for.

A file or folder that could name ids but cannot be read or listed (malformed, a link, a look-alike family folder or campaign folder, an unreadable folder, a ledger batch or ledger folder) makes the allocation fail closed with `TakenIdsUnlisted` (`grimoire.store`; `.paths` names what could not be read). `HoldingsUnlisted` is one case. Private 020 knowledge is never guessed past. Two exceptions: a look-alike holder file is not read, so only its own id is taken, not the ids its log names; and an instance file the store cannot read as a record (malformed, a newer format) reserves every `<kind>/<id>` in its bytes instead of failing closed (034), and a link or folder at an instance path takes only its own id, since the store never reads it as a record (034), so only an instance file whose bytes cannot be read for another reason (an I/O error) blocks. Taken ids are computed only when a new actor id is needed, so binding a scenario or lorebook, or reusing an actor, never lists them. The cost grows with play history: each allocation reads every instance, relation, chat, accepted-event, knowledge, transcript and holder file and every sovereignty batch of the campaign, and every security batch of the library. Decision entries, most of that ledger, are skipped rather than walked, but every batch is still read to fail closed, so this cost grows with all play, not only this campaign's.

### Actor instances

Actor instances are written as `grimoire.record/1` with:
- the 009 payload;
- the preserved top-level `"schema": "grimoire.entity/0"`;
- its `source` and `derived_from` set to the binding ref (pinned by default; unpinned only with `follow=True`);
- `base` / `base_etag` set to the resolved revision.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Messages and scene segments, and what an added actor is told | 027/032; accepted turn effects are 021's ([EFFECTS](EFFECTS.md)), and a chat is their provenance and must exist. Events and evidence are stored by 020 ([EVIDENCE](EVIDENCE.md)); records accept a chat as provenance, and a late actor starts with none |
| Control assignment for the persona's actor | 003/035 |
| Effective portrayal composition (first slice implemented: [EFFECTIVE_VIEWS](EFFECTIVE_VIEWS.md), including `chat_snapshot`) and derivation-aware navigation filters | 022/065 |
| Card import | 037 |
| World promotion and release pinning | 042 (first slice: a chat's actors proposed as a new world, and reviewed re-pinning of an actor and its bindings, [REBASE](REBASE.md)); release pins 056 |
| Cross-device merge of chat metadata | [028](roadmap/sprints/app-managed-device-sync.md) |
| A persisted navigation cache and the richer library browser | 065 |

Binding a source creates no event, belief or evidence record and never makes anyone aware of its content.
