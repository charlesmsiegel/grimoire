# Reviewed source rebase and chat canon

**Status:** first slice of [Source Rebase And Semantic Merge](roadmap/sprints/source-rebase-and-semantic-merge.md) (Sprint 042, Wave 5). It is implemented as:
- `grimoire.rebase`: the pure three-way field review, the decided rebase of one instance and the definition a promoted chat actor becomes;
- `grimoire.rebasestore`: a store-backed service (`SourceRebase`, `CanonProposer`).

There is no UI yet, and no model is called. Every write is one guarded store transaction with a `preview` of its exact bytes before `commit`.

## What exists

- **Reviewed rebase.** A campaign actor based on revision r1 of a definition is reviewed against another revision r2 of the *same* entity, usually a newer one (an older one is a reviewed revert). A human decides each changed field (`take` or `keep`). One commit re-pins the actor's instance file and every chat binding that pins it.
  - The actor keeps its id. Its relations, accepted events, knowledge, holdings, placements and roster entries are not rewritten, and no other campaign is read or written.
  - A campaign that does not rebase keeps r1.
- **Chat canon.** From one chat, a human picks roster actors and proposes a new reusable world source `world:<id>`, one definition per pick.
  - Only new files under `sources/<world>/` are written. The chat, its scope, its actors and every history record are byte-identical.
  - Attaching the new world to the chat's scope is 107's `ChatLibrary.attach_world`, which changes no id.

## Rebase review (`SourceRebase.review(actor, target=None)`)

`target` defaults to the head of the actor's source. Both sides are 022 effective definitions (`compose`), so a derived revision is compared by its composed values, never by its raw delta.

**The common ancestor** is the revision the campaign has been playing:
- the one revision every active pinned chat binding of the actor resolves to;
- otherwise (no pinned binding) the instance's recorded `base`, or its pinned `source`.

107 rebinding reuses an actor at another revision without rewriting its file. The binding then decides, and the review reports `actor_rebound`.

The recorded `base_etag` must still match the ancestor's bytes. If it doesn't, the review refuses with `ancestor_edited`: a hand edit means the ancestor is no longer what was adopted. When no adoption etag exists for the ancestor (a rebound actor, or an instance written before `base_etag`), the review reports `ancestor_unverified`.

**Fields** are named as 022's provenance names them: `name`, `description`, `section:<n>` and `facet:<n>`. A row exists only for a field upstream changed or the instance overrides. The instance's overrides are its `local_name` and its `replaced` and `added` facets.

| status | meaning | decision |
|---|---|---|
| `upstream` | upstream changed it (added, removed or replaced); the instance does not override it | required |
| `conflict` | upstream changed it; the instance overrides it with another value | required |
| `converged` | upstream changed it to exactly the instance's override | none: the override stays |
| `local` | the instance overrides it; upstream did not change it | none: the override stays |
| `individual` | upstream renamed the template of an individualized (024) instance | none: its given name stays |

- **`take`** adopts the upstream value and drops the instance's override of that field.
- **`keep`** keeps the campaign's value as an instance override: the ancestor value on an `upstream` row, the override on a `conflict` row.
  - A name goes to `local_name`.
  - A facet goes to `replaced` if upstream has that trait, otherwise to `added`. A converged or kept `added` facet that upstream now has moves to `replaced`, because 009 refuses an `added` facet that shadows a trait.
  - Description and sections have no instance-level override in this slice. An instance also cannot suppress a trait upstream adds. `keep` on those is `not_overridable`: take the change, or stay pinned.

The rebased instance must compose over the upstream (009 `compose_instance`), and its reasons propagate. For example, taking the removal of `durable` while the instance holds `Integrity` raises `state_without_facet`.

**Review also reports** (and never changes):
- `repinned`: chats whose active binding pins the ancestor; the commit re-pins them.
- `following`: chats whose binding follows the head. Those chats already show the new head; they are not rewritten.
- `history`: chats, relations and accepted events that name the actor (a raw scan), all kept.
- `history_review`: upstream changes the description or a section and the actor has history. Established events may contradict the new authored text, so a human reads both. This is a notice listing ids, not a contradiction check.

**Refusals** (`RebaseError.reason`):

| reason | when |
|---|---|
| `missing_actor`, `undecodable_actor` | the instance file is absent or cannot be decoded |
| `campaign_original` | the instance has no source to rebase |
| `no_ancestor` | no pinned binding, no recorded base and an unpinned source; or a `base` that is not one revision |
| `ancestor_mismatch` | the recorded base names another entity |
| `missing_ancestor` | the ancestor does not resolve |
| `ancestor_edited` | the ancestor's bytes no longer match `base_etag` |
| `other_entity` | the target is another entity: a namesake or any other definition is never a rebase target |
| `missing_target`, `up_to_date` | the target does not resolve, or is the ancestor |
| `follows_head` | an instance that follows its head, and a target that is not the head |
| `chats_unreadable` | a chat file of the campaign cannot be read or interpreted, so its bindings cannot all be found |
| `binding_disagrees` | the campaign's chats pin the actor to different revisions; rebind or detach one first |
| `binding_limit` | a chat to re-pin already holds 107's maximum number of bindings |
| `nature_changed` | an individual became a template or back: a different kind of thing, not a field change |

Composition refusals of either side (`PortrayalError`) propagate.

## Rebase apply (`SourceRebase.apply(review, decisions)`)

Decision refusals: `undecided` (a changed field without a decision), `unknown_decision` (a decision for a field that needs none or does not exist), `invalid_decision` (not `take`/`keep`) and `not_overridable`.

The returned `RebaseChange` writes:
- **the instance**, guarded by CAS on its reviewed etag.
  - A pinned `source` is re-pinned to the target; a following source stays unpinned. `derived_from` is re-pinned likewise if it named a pinned revision of the entity.
  - `base` becomes the target and `base_etag` the target's etag.
  - Every other envelope field, the payload's unknown keys, state and individuality are kept.
- **each chat with an active pinned binding of the actor.** The binding is tombstoned and a new pinned binding of the target, for the same actor, is added. The roster is unchanged.

`commit(approvals=None)` runs under the store lock. It re-checks:
- the bytes of every layer the review composed, on both sides (a derived revision's base chain included);
- the source's head, when the instance follows it: a new head since the review would change what the actor composes to;
- every chat of the campaign, at the etags the review read: a chat added, removed or edited since could pin the actor to the old revision.

Any mismatch is `StaleWrite` and nothing is written. The store's authorization (and its `approvals`) applies as for any transaction.

## Canon proposal (`CanonProposer.propose(chat, world, picks, title=None)`)

`CanonPick(actor, entity_id)`; 1 to 64 picks, each actor and entity id once.

Each definition is `world:<id>/<actor kind>/<entity_id>@r1`:
- **A sourced actor** becomes a 022 **derived portrayal** over the exact pinned revision its chat binding resolves to, with a `derived_variant` link declared by the new world.
  - It states the actor's effective name and only the facets the campaign overrode (with the facets those require, so its own traits are a valid set).
  - Everything else, the description and sections included, is inherited, so a later fix to the base still reaches it.
- **A campaign original**, or an individualized (024) template instance, becomes a standalone individual with its effective name, facets and description. An individualized instance also restates the sections it no longer inherits from its template. A portrayal must keep its base's nature, and an individual is no longer a template.

Each definition records `promoted_from: {chat, actor}` (an uncomposed extra). Lived state (integrity), relations, events, knowledge and transcript are never copied.

A same-named definition elsewhere is reported in `CanonEntry.same_name`. It is never merged, linked or replaced.

**Refusals:**

| reason | when |
|---|---|
| `no_picks`, `too_many_picks`, `duplicate_pick` | the pick list is empty, over 64, or repeats an actor or entity id |
| `not_a_world` | the id does not parse, or is not exactly `world:<id>` (a longer id would nest its folder inside another world's) |
| `world_exists` | `check_source` finds anything there, or a security-ledger grant or approval still names that source id, its folder or a folder under it (a grant outlives a hand-deleted folder and would cover the new world) |
| `definition_exists` | a stray revision file already sits where a new one would go |
| `missing_chat`, `undecodable_chat` | the chat is absent or cannot be interpreted |
| `not_in_chat` | a picked actor is not on the chat's roster |
| `chain_too_deep` | a portrayal of the actor's base would be deeper than 022's chain limit |

What the chat's snapshot refuses for an actor (missing, undecodable, broken binding, or `not_selected` for a detached one) propagates.

`commit(approvals=None)` re-checks under the store lock (`StaleWrite`):
- that the world is still absent and unnamed by a grant. The approvals this commit supplies for its own new files name the new world by design, so they do not count;
- that the chat and each actor file still have the etags the proposal read;
- that every layer of each portrayal's base chain still has the bytes the proposal composed.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Rebasing a derived portrayal onto its base's new revision (source-level rebase) | 042 later |
| Campaign-local overrides of description and sections, so one of two changed sections can be kept | 042 later (a 009 payload change) |
| Adopting a *different* entity for an existing actor (a world portrayal of its card), which needs a 107 roster change | 042 later |
| A reviewed way to drop lived state when upstream removes the facet it needs | 042 later |
| Checking at review that every layer of a derived ancestor is still what was adopted (`base_etag` covers the leaf; commit re-checks every layer against the review) | 042 later |
| Review before a following chat sees a new head; pin, follow and detach modes beyond what bindings store | 042 later |
| Semantic contradiction checks between new authored text and established events | 042 later (`history_review` only lists ids) |
| Promoting relations, lore, places and organizations | 042 later |
| Redacting `promoted_from` when a world is released or shared | 056 |
| Provenance previews and review UI | 052/056 |
| Undo | not planned for this slice |
