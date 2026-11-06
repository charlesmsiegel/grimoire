# Effective definitions and frozen world-optional snapshots

**Status:** first slice of [Effective World Snapshots](roadmap/sprints/effective-world-snapshots.md) (Sprint 022, Wave 3). It is implemented as:
- `grimoire.core.portrayal` (pure): composes a derived portrayal over its pinned base;
- `grimoire.core.snapshot` (pure): a frozen view of one selection;
- `grimoire.effective`: an adapter that joins the [native store](STORAGE.md), [entities](ENTITIES.md) and [chats](CHATS.md).

There is no prompt renderer, UI or model call. Nothing here writes. The fixtures are synthetic.

## What it does

A reusable base, such as `catalog:elderfen-cast/character/mara@r1`, and a world portrayal derived from it, such as `world:elderfen-vampire/character/mara@r1`, are separate, immutable revisions. Composition returns a disposable **effective definition** with field-level provenance:
- the base is not copied or rewritten;
- the portrayal is not copied or rewritten;
- nothing is matched by name.

A **snapshot** freezes one chat's or one selection's view of its sources and captured actors for a whole operation. It works the same with no world.

## Authored sections and derivation (`grimoire.entity/0` payload keys)

This slice gives meaning to two top-level payload keys. The 009 codec already keeps both verbatim in `extras`.

| Key | Shape | Rules |
|---|---|---|
| `sections` | `{name: text}` | Names follow the identity segment grammar (`validate_segment`). Text is nonblank, ≤ 8000 characters, and has no control, surrogate or line/paragraph-separator characters (Unicode `Cc`, `Cs`, `Zl`, `Zp`) except `\n` and `\t`. CRLF and a lone CR are read as `\n` (the stored text is unchanged). At most 64 sections per revision, and at most 64 **composed** (see [the composed cap](#composition-rules)). |
| `derivation` | `{"base": "<pinned ref>", "clear_sections": [...], "clear_facets": [...], "clear_description": bool}` | `base` is required, must be pinned, and must be another entity: the index never links an entity to itself, so a revision whose base is a revision of its own entity is `invalid_derivation`, not `unlinked_derivation`. The lists default to empty and may not repeat a name. Unknown keys are refused. |

- A revision **with** a `derivation` key is a derived delta, even when its value is `null`: a non-object value is `invalid_derivation`, never ignored. A revision without the key is a complete, standalone definition. `sections: null` is likewise `invalid_sections`.
- Sections are composed as **opaque whole-section text**. A `voice-evidence` section is just a section. Item-level voice override and suppress belong to 027 ([VOICE](VOICE.md)).
- `species` is an authored text section here, not the 017/024 species relation.
- Other unknown extras are **not composed**. They are listed in `uncomposed`.
- A malformed `sections` or `derivation` in a **derivation chain** fails only the composition of that portrayal (`invalid_sections`, `invalid_derivation`); a chain never silently drops a layer's authored content. The library still loads, and no data is dropped.
- A **standalone** card (no `derivation`, a one-revision chain) with unusable `sections` still composes, because 009 accepts it: its sections are left out, `sections` is listed in `uncomposed`, and an `invalid_sections` notice says why.

### Which link means inheritance

The base lives **inside the derived revision**, so a pinned portrayal cannot change meaning when a manifest is edited. The index link must also agree. The link is a `derived_variant` whose subject is the exact pinned revision, whose object is the payload base, and which is declared by the portrayal's own source.

| Situation | Result |
|---|---|
| Payload base, no such link (removed, or dropped by the loader) | `unlinked_derivation`. Never a standalone fallback. |
| A qualifying link to a different object | `link_mismatch` |
| A qualifying link but no payload `derivation` | Standalone, with an `ancestry_only` notice. A link alone never injects a base. |
| A `derived_variant` declared by another source | Ignored and listed in `ignored_links`. The store already excludes such a manifest (`ownership`). |
| `alternate_portrayal`, `same_individual`, `overrides`, or an equal name | Never inheritance. `overrides` has no meaning in this slice. |

A base source that declares a reverse cross-source link closing a cycle makes the loader drop the later declarer's link ([link order](STORAGE.md)). That shows up as `unlinked_derivation`: visible, never silent.

A payload derivation cycle is unreachable: every payload base must equal an accepted link, and the index rejects link cycles. The chain bound (8 revisions; a 9th is `chain_too_deep`) is the guard.

### Pinning

- The base is exactly the payload's pinned ref. There is no follow-head derivation in this slice.
- A base whose head is a different revision is still used pinned, with a `base_not_head` notice. Revisions are opaque, so the notice never claims "later".
- A missing base is `missing_base`, with the reason in `.detail`: the index reason (`missing_source`, `missing_entity` or `missing_revision`), or, through `grimoire.effective`, the store's `excluded_source` or `entity_dropped` when the base exists on disk but its source or entity was left out of the load, or `unreadable_revision` when its file exists but could not be read. The most specific reason wins: `excluded_source` first, then `unreadable_revision`, then `entity_dropped`. An unreadable base whose entity the loader also dropped is `unreadable_revision` only when the drop is that file's own consequence (it is the declared head, or, with no head declared, the entity's only revision), so repairing the file is the whole fix. When the entity was dropped for another reason too (`kind_reuse`, `missing_head`, a head naming another revision), repairing the file would leave it dropped, and the reason is `entity_dropped`. A `missing_head` reported for another entity of the same source is read conservatively the same way. It never falls back by name.
- A base that is not an entity definition is `not_entity_definition`. One that cannot be decoded is `undecodable`, with the codec reason in `.detail`.

### Composition rules

Composition runs from the root base down to the requested revision. Values at the root are `local`. At each layer, P is the composed parent and C is the child revision.

| Field | Rule |
|---|---|
| kind | must equal the base's (`kind_mismatch`) |
| nature | must equal P's (`nature_mismatch`) |
| name | always C's: `replaced` if it differs, `restated` if equal |
| description | If C's is non-blank, it is `replaced` (`restated` if equal, `local` if P has none). If C's is empty or whitespace only, it is inherited: blank text is never an implicit clear. `clear_description` clears it. The description is 009's field, so composition takes it **verbatim**: it is never refused or rewritten (line endings included), and a snapshot and `effective_entity` agree on it. |
| sections | In C and P: `replaced` (`restated` if equal). In C only: `local`. In P only: inherited. Named in `clear_sections`: removed, recorded `cleared`. |
| facets | Keyed by facet name, the same as sections; `UnknownFacet`s included. The composed set must be a valid `FacetSet`; otherwise that `FacetError` reason applies (for example `missing_requirement`). |

- **Inheritance is the default.** Absence always means inherit.
- **Clearing is explicit.** A clear is typed and recorded, never inferred from prose, `null` or empty text.
- **Refusals.** Clearing what P lacks is `stale_clear`. Clearing and setting the same name is `clear_and_set`.
- **The composed cap.** Each revision holds at most 64 sections, but a chain of 8 could compose 512, and prompt builders (027, 031) need bounded input. So the requested revision's composed result may hold at most 64 sections (`MAX_SECTIONS`); more is `too_many_sections`, with `.ref` the requested revision. It is refused, never truncated. The cap applies to the result, not to each step: a leaf that clears an over-full parent back to 64 or fewer composes, while that parent, requested on its own, is refused. Each section is already ≤ 8000 characters, so the composed text is bounded too. Ancestry does not read fields, so `derivation_chain` and a snapshot's raw ancestry still reach the pinned base for repair.
- **Provenance rule.** An unmentioned value becomes `inherited`, keeping the node that authored it. Only `cleared` carries through unchanged, attributed to the layer that cleared it. `replaced` and `restated` are attributed to C: the child pins its own value, so a later base change would not flow into it.
- **First-slice limit.** A child's own `traits` must be a valid `FacetSet` (009 constructor). So a derived portrayal that adds `durable` must restate `physical`, which is recorded as `restated`.

### `EffectiveDefinition`

| Field | Meaning |
|---|---|
| `requested` | the ref as asked |
| `ref` | the composed pinned revision |
| `chain` | `ChainNode(ref, etag)`, leaf first. The etag is that of the exact stored bytes, or `None` in memory. |
| `name`, `nature`, `description`, `traits`, `sections` | the composed values |
| `provenance` | `FieldOrigin(field, origin, source)` for every present or cleared field (an absent description has none). Fields are `name`, `description`, `section:<n>` and `facet:<n>`. Origins are `local`, `inherited`, `replaced`, `restated` and `cleared`. |
| `uncomposed`, `notices`, `ignored_links` | see above |

It never contains a cleared value, campaign memory, relationships, observations or control.

`PortrayalError(ValueError)` carries `.reason`, `.ref` (the revision that failed) and `.detail`, and it pickles.

## Instances

`compose_instance(instance, base)` (in `grimoire.core.entity`) composes an instance's `replaced` and `added` facets and its state over an `EntityDefinition` or an `EffectiveDefinition`. The 009 reasons are unchanged. The base must be the instance's own source entity, at any revision (a selection may follow a newer head): a base for a campaign original, no base for a sourced instance, or another entity's base, such as a namesake, is `source_mismatch`.

A **raw derived definition is refused** (`derived_definition`), both here and through `effective_entity(instance, index)`. A value that is not an authored base (a legacy `CharacterDefinition`, text) is `not_entity_definition`.

Two consumers take the composed form:

- **Perspective.** `snapshot_actor_view(instance, snapshot, knowledge=None)` (in `grimoire.core.resolve`) is `actor_view` for an instance the snapshot captured, derived portrayals included. `instance` must equal the captured value (`PortrayalError` `instance_mismatch`). The actor is composed by `snapshot.entity`, so it follows the actor's binding and the snapshot's refusals propagate. The view is the same `ActorView`, with the same agent and knowledge rules as the index path. It shows the composed name and description, and `resolved_source` is the leaf pinned revision. Sections, ancestry, provenance and notices stay in the snapshot. For a standalone actor both paths give an equal view. The index-only `actor_view(instance, index)` keeps refusing a derived portrayal with `derived_definition`, so no caller skips composition silently.
- **Promotion (024).** `is_generic` and `individualize` accept the template's `EffectiveDefinition` in place of the index (`snapshot.effective(source)`); see [Individualizing a template instance](ENTITIES.md#individualizing-a-template-instance). An unpinned or missing base has no effective definition, so it still refuses with this page's reasons (`invalid_derivation`, `missing_base`).

**Named limitation, owned by 035:**
- `CampaignRoster.assign_control`, `check_controls` and `propose_damage` still resolve through `effective_entity(index)`.
- So a derived-portrayal actor cannot yet be given a controller or a damage proposal.
- They fail visibly with `derived_definition`; `check_controls` reports it as `unresolved`.
- For a **standalone** source those paths (and `actor_view`) follow the instance's own `source`, while a snapshot (and `snapshot_actor_view`) follows the actor's binding. So when 107 rebinds a reused actor to another revision, the two can name different revisions in the same turn. This is pinned by a test, and joining the two paths is 035's.

## Snapshots (`grimoire.core.snapshot`)

### `SourceSelection(world=None, sources=())`

Each source is a `SelectedSource(ref, role, resolved_revision=None, actor=None)`.

- `world=None` is valid. A world is provenance only: **nothing in its catalog resolves unless it is selected**.
- The same entity may be selected under two roles at two revisions.
- Selecting one `(role, entity)` twice, or one actor in two selections, is `conflicting_selection`.

### `EffectiveSnapshot(index, selection, decode=None, etag_of=None, explain=None, instances=())`

`explain(base)` lets an adapter give `missing_base` the store's own reason; `grimoire.effective` passes `explainer(view)`. Without it, the index reason is used.

**Revision boundary.** Every selected source is resolved once, against one immutable index, at construction.
- An unpinned source follows the head *as of that index*. If the head differs from the source's `resolved_revision`, a `binding_drift` notice is recorded.
- Captured `instances` are frozen too.
- Later store writes are invisible; a new load is a new snapshot.
- The boundary is one `LibraryView`. `load()` itself is not a point-in-time read: the index is built before campaigns and chats are read. A binding written during a load can therefore name a revision missing from that index, which is reported as broken or as `binding_drift`.

| Call | Answers |
|---|---|
| `sources()` | each selection with its pinned resolution or `BrokenReference`, plus notices |
| `resolve(ref, role=None)` | the raw definition of a selected entity, or of a pinned **ancestor** in the chain of a selection (of that `role`, when given). **Raw ancestry is for merge owners** (027 voice, 038 art): it still holds content the portrayal cleared. |
| `etag(ref, role=None)` | the etag of the bytes `resolve` reads (the snapshot's `etag_of`, by default the stored definition's `etag`), or `None` when none is stored. A hand edit in place changes it while the reference stays the same; 031's pack basis records it. |
| `definition(ref, role=None)` | `resolve`, decoded with the snapshot's `decode`: the raw `EntityDefinition`, own `derivation` included. 027's voice walk reads each layer's `clear_sections` through it. |
| `effective(ref, role=None)` | the `EffectiveDefinition` of a **selected** revision, memoized; `requested` is the ref as asked. Ancestors are reachable only raw, so content a portrayal cleared never reaches prompt assembly through this call. |
| `entity(instance_ref)` | the captured instance composed over its selection's effective definition |
| `instance`, `instance_etag` | the captured instance value and its etag |
| `instance_notices(instance_ref)` | for a captured instance pinned to a revision other than its selection's resolution: `instance_source_drift` when that revision is **missing** from this index (how a torn load is reported), or `actor_rebound` when it exists (an ordinary 107 rebind; composition follows the selection) |
| `find_by_name(name, kind=None)` | candidates among **selected** entities whose *selected revision's* authored name matches: the decoded entity name, not a stored envelope's display name (a non-entity record uses its record name; an entity this version cannot decode matches no name; a blank query matches nothing). Not ancestors, not other revisions; never a selection. |

Refusals:
- `not_selected`: an unselected entity, or an actor no selection names.
- `role_not_selected`: the entity is selected, but not under the requested role (and, for a pinned request, it is not a pinned ancestor of a selection of that role either).
- `ancestry_unavailable`: a pinned request is not a known ancestor, but a selection's ancestry could not be walked; `.detail` is the walk's reason (and its own detail, as `reason/detail`), and `.ref` is the revision whose walk failed. A lorebook or scenario selection has no ancestry, so it never causes this.
- `ambiguous_selection`: an unpinned request for an entity selected under several roles; name the role.
- `revision_not_selected`: a pinned request that matches no selection or chain member.
- `MissingDefinition`: a broken selection. That includes a pinned request for any revision of a broken head-following selection: a head always names a registered revision, so such a selection is broken only because its source or entity is absent, and then it selected no revision at all (never `revision_not_selected`).
- From `effective` and `entity`: every composition refusal on this page (`missing_base`, `not_entity_definition`, `too_many_sections`, ...), propagated unchanged.
- `unknown_instance`: the instance was not captured.
- `actor_source_mismatch`: the actor's source names another entity.
- `invalid_selection`, `conflicting_selection`: a malformed or duplicated selection, when it is built (`sources` that are not a collection, such as one bare `SelectedSource`, are malformed too).
- `conflicting_instance`: one instance captured twice.

Ancestry is walked separately from field composition (`portrayal.derivation_chain`), and reads no field, so a portrayal that fails a field rule (`invalid_sections`, `stale_clear`, ...) still lets merge owners reach its pinned base to repair it.

**How an actor is matched.** An actor is matched to its selection by `SelectedSource.actor`, never by source text, because 107 reuses an actor across re-bindings without rewriting its payload `source`. A campaign original (no source) composes with no base. `EntityError` from the instance's own composition propagates.

## Store and chat join (`grimoire.effective`)

- `entity_decoder(stored)` decodes a `StoredDefinition` whose envelope's top-level `schema` is `grimoire.entity/0`. Any other record (a lorebook, a scenario) is `not_entity_definition`; `resolve` still returns it raw. Another `grimoire.entity/<n>` version (written by a newer client) is an entity this version cannot read, so it is `undecodable` (`unsupported_schema`), never ancestry-free.
- `selection_snapshot(view, selection)` is a snapshot over `view.index`.
- `explainer(view)` gives `missing_base` the store's `excluded_source`, `unreadable_revision` or `entity_dropped`, in that order of precedence; both snapshot helpers use it. It explains only bases: a broken *selection* keeps the index reason in its `BrokenReference` and `MissingDefinition`.
- `chat_snapshot(view, chat_ref)` reads, **from the same `LibraryView`**, the chat, its scope and every roster instance, and returns a `ChatSnapshot(chat, chat_etag, scope, scope_etag, snapshot, problems)`. `scope_etag` is `None` when the scope could not be interpreted, so it is never a precondition for a record this version did not understand.
  - **Selection.** It is built from the chat's **active** bindings: `(ref, role, resolved_revision, actor)`, and, right after each bound `grimoire.scenario/1`, the `lorebook` its selected revision names, pinned as named and with no actor, unless the chat has ever bound that lorebook itself: an active binding is kept as bound, and a tombstone with none active is the player's opt-out ([CARDS](CARDS.md#chats)). That lorebook is read from the scenario each time, so it follows the scenario through updates and re-pins; one that no longer resolves is still selected, so it shows as broken rather than blocking play.
  - **Launch.** The launch is provenance only, so a detached launch binding is not selected.
  - **World.** The world is the scope's world, or `None`.
  - **Problems** (never failures): `orphan_chat`, `undecodable_scope`, `missing_actor:<ref>`, `undecodable_actor:<ref>` and `invalid_scenario_lorebook:<ref>` (a scenario whose `lorebook` is not a pinned lorebook ref: no lorebook is selected for it).
  - **Raises** `RecordNotFound` when the view has no readable chat at that ref, and `ChatCodecError` when this version cannot interpret the chat.
  - `chat_selection(chat, scope)` is the bindings' selection alone (without scenario lorebooks), exposed on its own for 035.

## Not in this slice

| Not here | Owner |
|---|---|
| A compiled or persisted projection, and the virtual-vs-compiled comparison (milestone 3) | 022 later milestones |
| Follow-head derivation, multiple bases, `overrides` | 022 later, 046 |
| Reviewed rebase and inheritance UI | 042, 046 |
| Relationship remapping between portrayals | 017 |
| Voice item composition, and cross-source art inheritance and placement merging | 027, 038 |
| Snapshot-aware control and damage, and prompt assembly | 035, 027, 031 |
| Derivation-aware navigation filters | 065 |
