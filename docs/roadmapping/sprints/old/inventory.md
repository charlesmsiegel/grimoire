# Inventory, Equipment, Economy and Crafting

**Priority:** small counted-holdings MVP is near-term and works with **no mechanics module**; complex equipment/economy is later.

## Goal

The player and narrator know **who has what and how much**, whether or not the campaign uses structured game rules. Campaign-owned possessions change through validated operations; shared item definitions never become mutable holdings.

## First gameplay slice — counted holdings

- Make inventory optional per campaign. Turning it off suppresses normal UI/context tracking but **does not erase** historical holdings.
- Maintain human-editable authoritative state for individual PCs/NPCs and a possible shared party pool, with stable holder references and counted stacks of different item kinds.
- Provide deterministic add/acquire, consume/remove, adjust and transfer operations with nonnegative quantities, clear insufficient-quantity errors and retry protection. Reuse existing file publication/lock rules.
- Allow easy manual correction through an accessible inspector or compact dedicated view; do **not** require a configurable HUD.
- Put only relevant current holdings into the scene/prompt, bounded by context policy, so narrator knowledge matches persisted counts.
- Distinguish **inventory consumables** (capture balls, potions, rations) from **individual captured monster characters**. Capture effects may consume a ball but the monster lives in the actor/party model, not as a fungible quantity.

**Acceptance:** the PC starts with 8 ordinary and 2 stronger capture balls, spends 3 ordinary, buys 4 and has **9 ordinary + 2 stronger** left after reloading. A repeated approved consume cannot spend twice; trying to spend 15 fails visibly; disabling/re-enabling tracking does not destroy state.

## Full equipment and resource expansion

- Differentiate source/world **item definition**, discrete individual **instance**, and fungible **resource stack** with optional units, provenance, owner, location, status and equipped slot. The existing item entity kind should be reused, not built again.
- Support typed **acquire, drop, transfer, consume, adjust, equip, unequip** through one deterministic mutator used by manual edits and reviewed LLM proposals. Explicitly handle zero, stacking/splitting, multiple copies, quantity limits, incompatible equipment and item absence.
- Preserve history and scene/event attribution through scene move, edit, deletion, retcon, fork, import, campaign/world divergence and restore.
- Resolve proposed item names against actual qualified identities, aliases and campaign instances; never silently merge different entries because their labels match.
- Flag contradictions: consuming an unowned item, overdrawing a stack, transferring from the wrong holder, duplicate unique-object acquisition and impossible equipped state. Conflicted proposals require review or rejection, not fake success.
- Stage LLM-extracted proposed changes rather than letting narration write inventory; reviewers see explanations, exact before/after differences and applied/rejected results.
- Make ruleset-specific **currency, gear effects, encumbrance, crafting, trade, depletion, rewards and economy** optional consumers of the same holdings/operations base, not mandatory infrastructure for casual RP.
- A richer party/character inventory interface can follow the basic view; optional provenance and operation history remain inspectable.

## Dependencies and boundaries

[State Transactions](state-transactions.md) provides recoverable multi-record changes, [World Resolution](world-resolution.md) resolves shared definitions, [Creature → Character](creature-to-character.md) creates individually captured actors and [Mechanics](mechanics.md) provides optional equipment/stat semantics. A simple local quantity ledger should **not** wait for a global content dependency DAG or the full mechanics engine.

## Open questions

- What file-backed storage representation best fits current campaign authority: one campaign-local holdings ledger, per-holder sidecars, or another existing state contract?
- Should inventory default on or off, and what context/UI behavior remains when disabled?
- How are different units, currency, stacking, unique objects, zero quantities and item instances represented without a universal ontology?
- Does the first slice track a persistent operation history, only an idempotent latest-state store, or a minimal hybrid with scene references?
- What is the initial UX for manual changes and reviewable extracted operations?
- How should references survive retcons, external hand edits and missing imported item definitions?
- What precision of resource use belongs to fictional roleplay vs a rules module, and when should automatic inference propose consumption?
