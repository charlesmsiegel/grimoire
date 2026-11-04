# Fictional Abilities, Limits and Costs Without Mandatory Mechanics

**Status:** **first slice implemented**, headless and pure: the `abilities` facet and a deterministic, model-free assessment of labelled attempts in `grimoire.core` (`abilities.py`, `ability_use.py`, codec support). The contract is in [ABILITIES.md](../../ABILITIES.md). Milestone 1's three constrained abilities and the radiation-pressure proving scenario are executable on synthetic fixtures. Nothing is adjudicated or committed: outcomes are 029's, committed costs are milestone 2 (with 021) and composition is milestone 3. Obligations on 020/021/029/034/035 and 022/024 are listed in ABILITIES.md.

**Outcome:** Keep supernatural abilities, technology, spells and extraordinary traits coherent in freeform play even without a rules engine.

## Scope and ownership
- Describe abilities by permitted effects, prerequisites, range, limitations, price, uncertainty and observed consequences as human-editable fictional facts.
- Distinguish a character's known capability, attempted feat, unsupported extrapolation and new ability requiring player/director approval.
- Handle conflicts, power combinations and evolving limitations without pretending a generic d20 schema applies.
- Provide readable references for consistency checking and optional adapters to real rulesets when selected.

## Milestones
1. Define three constrained, mechanics-free abilities and adjudicate attempted uses without inventing new powers.
2. Track explicit costs, cooldowns or constraints when applicable through normal accepted state changes.
3. Support composition, novel applications and evolving high-concept power systems with explainable decisions.

## Moonshot
An original freeform magic or superpower system can support inventive, logically consistent play for years, including clever edge cases, without forcing an artificial numeric game system.

## Proving scenario
A light-bending character tries to fly using radiation pressure. The narrator evaluates stated limits and costs rather than granting effortless flight or insisting on a non-existent dice rule.

## Interfaces and independence
Defines **fictional capability constraints**, not rules module authoring or universal abilities. [Fiction-First Adjudication](fiction-first-adjudication.md) consumes those constraints. A first slice can be independently tested with authored character records.

## Open questions
- Which constraints need structure versus natural-language prose?
- When is creative extrapolation permissible?
- How are capability costs reconciled with optional mechanical sheets?
