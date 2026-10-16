# Inventory and equipment — near-term foundation, expandable over time

**Status: NEAR-TERM SPRINT CANDIDATE for a small mechanics-independent inventory, plus longer-term expansion. NOT implemented.**
**Transferred 2026-10-15** from [#167](https://github.com/charlesmsiegel/grimoire/issues/167), [#168](https://github.com/charlesmsiegel/grimoire/issues/168), [#169](https://github.com/charlesmsiegel/grimoire/issues/169), [#170](https://github.com/charlesmsiegel/grimoire/issues/170), and [#171](https://github.com/charlesmsiegel/grimoire/issues/171).
**Raw source text:** [archived-issue-text.md](archived-issue-text.md) retains every requirement, proposed alternative, example route and testing note from the original issues. Original issue suggestions predate important backend changes and are **not** adopted designs.
**Updated priority 2026-10-15:** A minimal campaign-local inventory is useful immediately in mostly freeform, mechanics-free monster-catching campaigns, where a player needs to track counts of capture balls, potions and other supplies. **Do not block that first slice on an unrelated mechanics engine, world-dependency DAG, complete item identity system, or configurable HUD.** Broader definition-sharing, equipping and deterministic rules remain future slices. The original issue trackers were retired for planning consolidation, not because the functionality shipped.

## Product goal

In any campaign, with game mechanics selected **or none**, the player and Grimoire must be able to understand and consistently maintain **who possesses what**; quantities, equipped items, consumables, communal resources and their provenance; and what changed during an action or a scene.

Worlds and content collections provide reusable **fictional definitions** for items and equipment. Campaigns own mutable **instances/holdings and transactions**. Don't make a change to a character's possessions rewrite the canonical item or source catalog. Not every item has or requires a stats sheet.

## First vertical slice — counts and simple possessions in freeform roleplay

**Problem:** The PC buys or receives eight capture balls, uses three during a scene, and should have **five remaining**. The next turn must not casually invent more because inventory is fictionally relevant even with **no mechanics module** selected. The engine should also know the difference between different kinds/tiers of capture ball, carried potions and currency or shared party supplies. This is the clearest near-term acceptance test.

**Smallest useful contract:**

1. **Optional campaign inventory.** Default/opt-in policy remains to be decided. One toggle controls whether inventory is actively tracked; switching it off leaves its files untouched. Can work when the chosen mechanics is `none`.
2. **Holders and stacks.** PC, NPC and optional party pool have named entries, stable campaign-local inventory entry IDs and integer quantities. Same-named stacks may be distinguished by a known local item/definition reference or explicit type, not solely by fuzzy matching. There is no need for a universal catalog or mechanics-specific stat sheet.
3. **Manual operations first.** Add, remove/consume, adjust and transfer small quantities through typed deterministic store operations with visible errors on insufficient quantity. A reliable manual editor and read API are more valuable than an ambitious unreliable automatic extractor.
4. **Narrative consistency.** The scene context can include **only relevant current holdings** (for example the present PC's five remaining capture balls) where appropriate, within existing token budgets. No blanket catalog injection. An LLM must not be given authority to silently invent or mutate counts.
5. **Read and edit UI.** A compact existing cast/inspector/detail panel or accessible standalone inventory view displays current holdings and allows simple corrections. Do **not** block on the future customizable HUD (#196).
6. **Editable authoritative files.** Markdown plus the project's existing user-editable JSON as appropriate. Reuse existing store locks, atomic publication, validation and provenance conventions. No canonical database required.
7. **Provenance and repeat safety.** Record a reason or scene where practical; repeated applications of the same approved operation must not duplicate consumption. For phase one, prefer deliberate UI/API writes; LLM-extracted operations can enter later through a staged, reviewed path.

**Not necessary in phase one:** fully generalized equipment slots, automatic item recognition across imported worlds, encumbrance, crafting recipes, price systems, complex stacking/splitting, character-sheet effects, package-managed item versions, cross-world migration, or a new multi-agent inventory reconciliation engine.

**Dependencies:** existing campaign and `items` records where useful. The *future* World Resolver should be able to provide richer shared definitions without breaking the identities/holdings recorded by the first slice. Keep a separation between **what an item is** and **who has how many** from the beginning.

**Phase-one acceptance scenario:** In a mechanics-free campaign, a PC has 8 standard capture balls and 2 stronger variants, spends 3 standard balls, gains 4 standard balls after shopping, and finishes with 9 standard plus 2 stronger balls. The counts persist across scene changes and reopening; the UI and relevant narrator context agree; an attempted overspend is refused/flagged; no shared world/item source file changes. Turning inventory off removes it from ordinary narration but retains it for later re-enabling.

**Timing:** Near-term feature sprint candidate, possibly after the immediate open backlog and/or the engineering cleanup wave; not a decision to interrupt the currently reviewed PRs or start building the full inventory system before the existing backlog clears.

---

## A. Campaign inventory and opt-in behavior — source #167

**Requirements**

- Campaign-local inventory state; toggleable independently per campaign. Disabling inventory must suppress inventory generation/context and editing operations without deleting previously recorded possessions.
- Inspect holdings for individual PCs/NPCs and shared/party resources. Holder identities must remain valid across appearances, cast changes and campaign-local actor versions.
- Persist state in **user-editable authoritative files** (Markdown/frontmatter and the established human-editable JSON format where appropriate), with existing Grimoire recovery and isolation semantics.
- Include relevant present-cast inventory in scene context **only when needed** and within normal packing budgets, without indiscriminate prompt expansion.
- Expose read and write APIs independent of any particular frontend HUD. Existing campaign Settings and UI patterns should be reused rather than forcing historical route suggestions.
- Define the state of inventory when campaign mechanics are changed or set to `none`: fiction and possession remain; only rules interpretation changes.

**Earlier alternatives to evaluate:** a campaign-level `inventory.json` ledger with holder keys; per-holder Markdown sidecars; or a play-state prose section. The old issue preferred the ledger; treat that as a candidate, not a decision. Inventing a new persistence system is not a requirement.

## B. Definition / instance / quantity / provenance — source #168

**Requirements**

- Distinguish reusable **item definitions** from individual physical item instances and fungible **resource holdings**.
- Support quantity, unit, equipped state, and holder; nonnegative ordinary fungible quantities and rules for zero, stacking, splitting, multiple copies and spending must be explicit.
- Discrete/equippable items may carry a definition reference; emergent items may begin without one. Multiple otherwise-identical objects can still be distinct instances.
- Record meaningful provenance: creation/acquisition, source scene/event, transfers, consumption, correction, and optionally source/reference lineage.
- Keep provenance/holdings consistent when scenes are renamed, moved, cut, forked or imported. No dangling pointer should silently become a different item.
- World-owned item descriptions, images and optional mechanics-specific stat information remain distinct from mutable per-campaign equipment state.
- Support fiction-only holdings in mechanics-free campaigns, and retain item identity if mechanics is switched.

**Original alternatives:** world-owned item definitions plus campaign holdings (preferred in the old issue), standalone campaign items only, or items stored as generic lore. Today Grimoire already has a first-class `items` entity kind; the historical claim that it doesn't is obsolete. Do not reimplement that kind.

## C. Typed inventory changes and recoverability — source #169

**Required operations:** `acquire`, `drop`, `transfer`, `consume`, `adjust`, `equip`, and `unequip`.

- Each operation should have a typed request/contract with target holder, item/instance or fungible resource, amount where applicable, scene/trigger, and optional explanation.
- Validate ownership, existence, quantity, equip applicability, source and destination, and per-system constraints deterministically; illegal state should produce a **visible failure or flagged proposal**, not a fabricated successful edit.
- Treat transfer as one logical change of custody; retries, partial write failures and concurrent edits must not duplicate or lose items. Reuse the existing state/recovery designs before inventing a second transaction ledger.
- Manual editing and LLM-extracted actions use the **same authoritative mutator**, not separate semantics.
- LLMs may propose inventory changes; they do not unilaterally change authoritative holdings. Integrate with Grimoire's existing preview/review/approved-write flows, subject to configurable autonomy for safe cases.
- Keep an understandable, auditable history; support correction/undo and scene-ID repointing, including narrative change/cut/retcon implications.
- When inventory is disabled, suppress application while preserving existing stored state, with clearly defined UI/API behavior.

**Original alternatives:** operation log plus derived/current holdings; setters with no history; pure event sourcing. Their relative merits must be judged alongside the later Mechanics II transaction design and the general Change Sets proposal, not settled independently by the old issue.

## D. Item identity and contradiction handling — source #170

- Resolve a proposed reference to an **existing known instance/definition**, a **fungible holding**, or a genuinely **emergent item**.
- Prefer explicit source-qualified identity and stable instance IDs. Names, display slugs and aliases are discovery signals, **never proof of shared identity**.
- Extractors should receive existing inventory identifiers where feasible. Disambiguation should avoid needless extra model calls; invoke an LLM only when ambiguous interpretation actually needs it.
- New/unknown items can become reviewed campaign-owned records with provenance; don't silently mint or merge a new world/library definition.
- Detect and surface contradiction classes: dropping or consuming an unheld item; insufficient quantity; transfer from the wrong holder; duplicate acquisition of a unique/discrete object; incompatible equip state; ambiguous collision with several known things.
- Flagged proposals default to **not approved** or require explicit resolution; never silently discard a conflicting action as if it had succeeded.
- No model output should bypass server-side validation on final application.
- Separate new-item creation, instance transfer, definition association and catalog promotion so accepting an operation never accidentally changes shared content.
- Use a single per-operation idempotency/replay policy: retrying an approved extract must not double-apply.

**Historical resolution alternatives:** exact ID, then safe aliases, then known resource catalog, then reviewed emergent creation; separate post-hoc conflict reconciliation; optional LLM disambiguation. Re-evaluate with the new World Resolution and Content Identity design.

## E. Inventory UI and flagged review — source #171

- Provide a readily reachable scene-level view of per-character and shared holdings, with quantity, equipped state and source/provenance available in details.
- Hide or disable inventory UI when the campaign inventory toggle is off without erasing state.
- Include manual corrections/typed operations and refresh the display after successful saves.
- Integrate suspicious LLM-proposed operations with the existing reviewed-edit checklist: show reasons, make risky proposals opt-in, and surface the final applied/rejected outcome.
- Future HUD widgets should be **clients of the inventory API**, not architectural prerequisites for the backend or the sole way to access inventory.
- Reuse existing campaign inspectors, record drawers, or a dedicated Inventory panel as appropriate to the then-current UI; the original issue presented all three approaches.
- Give helpful history/diff/provenance views so players can investigate "where did this item come from?" and correct mistakes.

## Cross-cutting invariants and dependencies

- **World Resolution / Content Identity / Content Composition:** definitions can come from the owning world or shared equipment catalogs; identity and provenance survive source changes, imports, variants and campaign mechanics switching.
- **Mechanics Extensibility / State Transactions:** equipped slots, encumbrance, consumption costs, currency and other ruleset semantics are optional overlays/effects, not prerequisites for fictional possession.
- **Change Sets / World Maintenance / Agentic Workers:** proposed changes must be source-scoped, validated, reviewable and auditable.
- **Character/Resource Progression:** crafting, economy, trade, resource depletion and rewards should reuse the same holdings/operation substrate.
- **Stories & Campaigns / Time:** scene moves, time skips, retcons, character state, and forks affect provenance; decide what can be reversed.
- **Frontend / HUD:** proposed inventory widget belongs to the campaign interface but must not demand a complete new HUD framework.

These dependencies affect the **full inventory vision** and justify deferring complex mechanics and reusable-catalog integration until the relevant base contracts are clearer. They do **not** block the independent **near-term fiction-first counts/holdings MVP** above.

## Follow-on slices after the first inventory MVP

1. **Richer identity and equipment** — discrete item instances, definition references, equipping and provenance beyond simple counted stacks.
2. **Advanced operations/history** — split/merge stacks, full audited transfer/cost operations, robust undo/recovery and scene provenance/repointing.
3. **Import/extract & reconciliation** — source-aware resolution, emergent items, ambiguous names and flagged staged edits.
4. **Richer UI & review** — sortable holdings, detailed inventory history, warnings and optional HUD widget integration.
5. **Mechanics integration** — demonstrate at least two distinct rules systems and mechanics-free play with the same fictional holdings. Optional encumbrance, item effects and actions should not change what inventory entries *are*.

These are proposed work slices; exact issue/PR boundaries should be specified from the current backend when the work starts.

## Acceptance scenario

Two NPCs and one PC inhabit a mechanic-free campaign that inherits an equipment definition from an optional shared source. The PC receives a named key and five rations; an NPC trades the key to another; the party consumes two rations. Grimoire records holders, counts, scene attribution and history. A duplicate proposal to consume the same rations cannot apply twice. An unsupported item name is offered as a reviewable emergent record. Attempting to transfer an unheld key is flagged. Switching to a mechanics module changes rules, not the historical ownership facts. Editing or removing the external item catalog must not silently reassign possession.

**Transfer ledger:** #167–171 are closed as standalone trackers with their **unimplemented product goals moved here**. The first simple inventory slice is **near-term**, while complex equipment/identity/reconciliation remain planned later. This is a change in priority, not an implementation. Full source bodies remain in [archived-issue-text.md](archived-issue-text.md).
