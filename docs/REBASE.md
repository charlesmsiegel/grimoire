# Reviewed source rebase and chat canon

**Status:** first slice of [Source Rebase And Semantic Merge](roadmap/sprints/source-rebase-and-semantic-merge.md) (Sprint 042, Wave 5). It is implemented as:
- `grimoire.rebase`: the pure three-way field review, the decided rebase of one instance and the definition a promoted chat actor becomes;
- `grimoire.rebasestore`: a store-backed service (`SourceRebase`, `CanonProposer`).

There is no UI yet, and no model is called. Every write is one guarded store transaction with a `preview` of its exact bytes before `commit`.

## What exists

- **Reviewed rebase.** A campaign actor based on revision r1 of a definition is reviewed against another revision r2 of the *same* entity, usually a newer one (an older one is a reviewed revert). A human decides each changed field (`take` or `keep`). One commit re-pins the actor's instance file and every chat binding that pins it.
  - The actor keeps its id. Its relations, accepted events, played turns, transcript pages, knowledge, holdings, placements and roster entries are not rewritten, and no other campaign is read or written.
  - A campaign that does not rebase keeps r1.
- **Chat canon.** From one chat, a human picks roster actors and proposes a new reusable world source `world:<id>`, one definition per pick.
  - Only new files under `sources/<world>/` are written. The chat, its scope, its actors and every history record are byte-identical.
  - Attaching the new world to the chat's scope is 107's `ChatLibrary.attach_world`, which changes no id.

## Pin by default

**Decision (owner, Wave 5 audit, Nov 6): pin by default.** New chats and imported scenes pin their card or world revision; updates arrive only through this reviewed take-or-keep rebase. Existing chats are never rewritten: a chat an earlier build stored following its head still follows.

- **Every product path pins.** 107's `start_chat`, `attach_source` and `set_persona` bind an unpinned ref pinned to the revision it resolves to at that moment: the revision whose bytes were checked, and the one a new actor is based on. The launch names the same pinned ref, and a new actor's instance `source` and `derived_from` are pinned too. A **reused** actor (`actor=`, `reuse_actors`, or the same persona chosen again) given an unpinned ref is pinned to the revision it plays, by the review's own ancestor rule: the one revision its active pinned bindings in the campaign's chats agree on (an explicit rebind counts); otherwise (none, a disagreement, or a chat that cannot be read) its instance's pinned `source`, else, with no `source`, its `base`. The same persona chosen again keeps its current pinned revision. When the instance follows its head (an earlier build's), or the revision it plays no longer resolves (a broken reference being repaired), the ref is pinned to the revision it resolves to now, so a following actor stays on the head it already plays. Nothing picks one chat's revision in silence or an unreviewed head, and a disagreement or an unreadable chat still refuses the review (`binding_disagrees`, `chats_unreadable`). Naming another revision explicitly is 107's rebind (`actor_rebound`). That covers 035's card chats (the card, a lorebook, the persona, a second card attached mid-chat), 036's SillyTavern import (it writes through `start_chat`), and 026's V0.2.1 scene chats, whose bindings, launch and actors pin the imported revision ([IMPORT_V1](IMPORT_V1.md)). A scene a rerun imports later binds an existing actor at the revision it plays by the same rule, so a rebase or a rebind taken in the meantime is kept.
- **Following is explicit.** `follow=True` on those three methods binds an unpinned ref as given, following its head (022's `binding_drift` notice shows when the head moves). No product path passes it.
- **Back-compat.** A chat an earlier build stored with unpinned bindings is not rewritten. It still reads and still follows, and this review lists it under `following`. A rerun of 026 reports each following binding (`binding_follows_head`) and of 036 lists them (`StImport.following`), whether an earlier build wrote it or someone chose `follow=True` since; neither re-pins, and this rebase re-pins only chats that are already pinned. To pin one, detach the source and attach it again with `actor=` (107), which pins the revision it resolves to then.
- **What this covers.** Only actors are rebased here. A pinned lorebook, scenario or other non-actor binding has no rebase review yet: it stays on its revision until it is detached and attached again. A scenario's own lorebook is the one its pinned revision names ([CARDS](CARDS.md#chats)).

So a card update or an applied 043 world change set leaves every default chat on its revision, and `review` + `apply` with `take` is how the new revision reaches one chat's campaign. The scenarios are in `tests/test_pin_by_default.py`.

## Rebase review (`SourceRebase.review(actor, target=None)`)

`target` defaults to the head of the actor's source. Both sides are 022 effective definitions (`compose`), so a derived revision is compared by its composed values, never by its raw delta.

**The common ancestor** is the revision the campaign has been playing:
- the one revision every active pinned chat binding of the actor resolves to;
- otherwise (no pinned binding) the instance's recorded `base`, or its pinned `source`.

107 rebinding reuses an actor at another revision without rewriting its file. The binding then decides, and the review reports `actor_rebound`.

The recorded `base_etag` must still match the ancestor's bytes. If it doesn't, the review refuses with `ancestor_edited`: a hand edit means the ancestor is no longer what was adopted. When no adoption etag exists for the ancestor (a rebound actor, or an instance written before `base_etag`), the review reports `ancestor_unverified`.

**Fields** are named as 022's provenance names them: `name`, `description`, `section:<n>` and `facet:<n>`, plus `extra:<key>` for an uncomposed payload key (below). A row exists only for a field upstream changed or the instance overrides. The instance's overrides are its `local_name` and its `replaced` and `added` facets.

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
  - Description, sections and payload extras have no instance-level override in this slice. An instance also cannot suppress a trait upstream adds. `keep` on those is `not_overridable`: take the change, or stay pinned. A campaign keeping its own voice or greeting is not built yet ([INHERITANCE](INHERITANCE.md#limits)); a world portrayal can already override, clear or reset one field at a time.

**Uncomposed payload keys** get an `extra:<key>` row too. 022 does not compose them (`EffectiveDefinition.uncomposed`), but other sprints read them. The main one is 037's `card`, which holds a card's example dialogue, greetings and system prompt; 035 reads it for the actor's voice and its opening line.
- The row compares the key's value in every layer of the chain, leaf first, because 035's voice reads a base's card too. `ancestor` and `upstream` hold those values (`None` where a layer lacks the key).
- `FieldReview.changed` lists the paths that differ, such as `card.texts.example-messages` or `card.instructions.system_prompt`. A path in a base layer starts `base<n>:` (`base1:` is the leaf's base).
- An instance has no override of payload extras, so the row is always `upstream`, and only `take` applies. A changed card therefore needs an explicit decision like any other changed field: `apply` without one refuses with `undecided`.

The rebased instance must compose over the upstream (009 `compose_instance`), and its reasons propagate. For example, taking the removal of `durable` while the instance holds `Integrity` raises `state_without_facet`.

**Review also reports** (and never changes):
- `repinned`: chats whose active binding pins the ancestor; the commit re-pins them.
- `following`: chats whose binding follows the head (an explicit `follow=True`, or a chat an earlier build stored before [pin by default](#pin-by-default)). Those chats already show the new head; they are not rewritten.
- `history`: read-only references to what names the actor (a raw scan for its id), all kept:
  - chats (107), relations (017) and accepted events (021);
  - played turns (035) and transcript pages (036);
  - `knowledge`: 020 events, evidence and knowledge-log batches.

  `history.problems` lists files that could not be read for the list. It is reported, never a refusal, since the rebase writes none of them.
  A review is director-level: it names evidence ids whoever observed them, so it is for the campaign's owner and never goes into a character's prompt.
- `history_review`: upstream changes the description, a section or an uncomposed payload key, and the actor has history. Established events may contradict the new authored text, so a human reads both. This is a notice listing ids, not a contradiction check.

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
| `follows_head` | an instance that follows its head, or one a chat binds following the head, and a target that is not the head (a following chat would compose the result over the head) |
| `chats_unreadable` | a chat file of the campaign cannot be read or interpreted, so its bindings cannot all be found |
| `binding_disagrees` | the campaign's chats pin the actor to different revisions; rebind or detach one first |
| `binding_limit` | a chat to re-pin already holds 107's maximum number of bindings |
| `nature_changed` | an individual became a template or back: a different kind of thing, not a field change |

Composition refusals of either side (`PortrayalError`) propagate.

## Rebase apply (`SourceRebase.apply(review, decisions)`)

Decision refusals: `undecided` (a changed field, an `extra:` row included, without a decision), `unknown_decision` (a decision for a field that needs none or does not exist), `invalid_decision` (not `take`/`keep`) and `not_overridable`.

The returned `RebaseChange` writes:
- **the instance**, guarded by CAS on its reviewed etag.
  - A pinned `source` is re-pinned to the target; a following source stays unpinned. `derived_from` is re-pinned likewise if it named a pinned revision of the entity.
  - `base` becomes the target and `base_etag` the target's etag.
  - Every other envelope field, the payload's unknown keys, state and individuality are kept.
- **each chat with an active pinned binding of the actor.** The binding is tombstoned and a new pinned binding of the target, for the same actor, is added. The roster is unchanged.

`commit(approvals=None)` runs under the store lock. It re-checks:
- every layer the review composed, on both sides (a derived revision's base chain included): its bytes, and that each side still composes to exactly those layers, so a manifest that dropped or changed a derivation link is stale too;
- the source's head, when the instance or any chat binding of it follows the head: a new head since the review would change what the actor composes to;
- every chat of the campaign, at the etags the review read: a chat added, removed or edited since could pin the actor to the old revision.

Any mismatch is `StaleWrite` and nothing is written. The store's authorization (and its `approvals`) applies as for any transaction.

Both this commit and the canon proposal's are crash-explored under the 006 fault harness (`test_rebasestore_recovery.py`): recovery yields the state before or after, with no residue.

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
- that every layer each pick was composed from (a portrayal's base chain, or the template chain an individualized pick restates) still has the bytes the proposal composed, and still composes, derivation links included.

## Boundaries and deferrals

| Not here | Owner |
|---|---|
| Rebasing a derived portrayal onto its base's new revision (source-level rebase) | 042 later |
| Campaign-local overrides of description and sections, so one of two changed sections can be kept | 042 later (a 009 payload change) |
| Keeping a campaign's own voice, greeting or system prompt when a card changes (`keep` on an `extra:` row) | 046 later |
| Adopting a *different* entity for an existing actor (a world portrayal of its card), which needs a 107 roster change | 042 later |
| A reviewed way to drop lived state when upstream removes the facet it needs | 042 later |
| Checking at review that every layer of a derived ancestor is still what was adopted (`base_etag` covers the leaf; commit re-checks every layer against the review) | 042 later |
| A reviewed re-pin of a chat an earlier build stored following the head (today: detach and re-attach), and modes beyond pin, explicit follow and detach. Default chats are [pinned](#pin-by-default) since the Wave 5 audit, so no default chat sees a new head unreviewed | 042 later |
| A reviewed rebase of non-actor bindings (a pinned lorebook or scenario moves only by detach and re-attach) | 042 later |
| Semantic contradiction checks between new authored text and established events | 042 later (`history_review` only lists ids) |
| Promoting relations, lore, places and organizations | 042 later |
| Redacting `promoted_from` when a world is released or shared | 056 |
| Provenance previews and review UI | 052/056 |
| Undo | not planned for this slice |
