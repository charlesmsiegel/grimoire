# Mechanics, Rulesets and Adjudication

**Priority:** main mechanics-improvement sprint, after narrow ruleset-contract research. **Mechanics II belongs here.**

## Goal

Turn fictional intent into legal, reproducible system-specific actions and consequences without losing fiction-first play or pretending all games are d20 checks. The ruleset determines resolution, not the narrator's preferred dramatic outcome.

## Work to plan

- **Mechanics Extensibility:** one campaign selects one mechanics module or none; a world may recommend a default. A deliberate cross-system hybrid is one authored composite module, not two concurrently authoritative engines.
- **Ruleset-specific capability contracts:** identify legality, fiction-triggering, required sheet fields, action costs, rolls/pools, opposed actions, partial success, results, resources, progress clocks, conditions, contests, growth and effects.
- **Mechanics II integration:** take forward the designed Actions / Effect DSL, NPC legal-action choice, deterministic results, explicit requirements, durable operations and undo/recovery from the existing Mechanics II specification. Reconcile against present module/editor/roll implementations rather than designing a rival engine.
- **Combat and Resolution:** ensure selected actions are legal in the source game system, sample recorded randomness, commit wounds/resources/conditions and narrate the established result. A miss remains a miss.
- **Homebrew Support:** a module can introduce ruleset-specific sheet layouts, mechanics semantics, reusable content and bounded executable extensions only where declarative composition cannot suffice.
- **System Integration:** import or author supported versions/editions of actual rulesets with explicit compatibility boundaries. World content is accessible even with the module disabled; incompatible creature/item stat sheets remain available as data but cannot silently adjudicate.
- **Mechanic-aware activity:** support time costs, resource changes, long-term advancements and season-scale actions when a game explicitly requires them. Do not force season rules on every game.
- **Tests and authoring UX:** actionable module validation errors, example templates and safe authoring affordances; gameplay must work with the selected rules and expose honest unsupported cases.

## Proving scenarios

Use a D&D attack, a WoD contested social/supernatural action, an Ars Magica laboratory season and a PbtA move triggered by fiction with partial success and GM move. These must yield *appropriately different* interaction and outcome shapes through a minimal stable interface. Then prove mechanics-free handling of the same basic social scene without inventing a mechanical check. Replay an accepted action without changing an already-established roll.

## Dependencies and boundaries

[State Transactions](state-transactions.md) coordinates multi-record effects. [Inventory](inventory.md) supplies fictional holdings independently of mechanics; equipment/encumbrance are optional rule overlays. [Creature → Character](creature-to-character.md) needs a declared map from creature sheet baseline to character instance state. [Challenges](challenges.md) proposes interesting situations; mechanics resolves attempts against them.

## Open questions

- What is the minimum interface shared by these four genuinely different rules families without false abstractions?
- What parts of the existing Mechanics II Action/Effect/journal design can be implemented unchanged after current-package refactoring?
- Which ruleset capabilities need small executable hooks versus purely declarative sheets and effects?
- How are edition and supplement incompatibilities represented and validated?
- What is the policy for unavailable creature-to-character sheet conversion under a system where captures require individualized stats?
- How do mechanical clocks, seasonal work, injury and resource effects connect to fiction-first campaign state without two authorities?
- Which representative games/editions must be credibly playable before the 1.0 milestone?
