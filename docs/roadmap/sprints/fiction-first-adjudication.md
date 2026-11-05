# Fiction-First Adjudication and Fair Freeform Outcomes

**Outcome:** Mechanics-free actions produce credible, consistent successes, failures and costs without relying on narratorial whim or assuming every action succeeds.

**Unscheduled prerequisite, awaiting a decision:** [021's next slice](turn-effects-and-wrap-up.md) (holdings operations from accepted effects, committing ability costs, charging each `attempt_id` once) is not on any wave yet; this sprint depends on it.

## Scope and ownership
- Represent a proposed action, known constraints, capabilities, stakes, uncertainty and accepted outcome without manufacturing dice rolls.
- Distinguish straightforward actions, uncertain outcomes, consensual authorial decisions and actions requiring explicit clarification; challenge is independent of whether mechanics are enabled.
- Provide bounded, evidence-led outcome selection and conflict review; unusual approaches are evaluated rather than rejected for departing from a prepared path.
- Keep outcomes stable across a narration retry and separate reversible dramatic framing from authoritative success/failure.

## Milestones
1. Define transparent fiction-first resolution examples for simple, risky and impossible actions with the current scene data.
2. Implement a small adjudication proposal/acceptance boundary and tests for consistent outcome narration.
3. Extend to open-ended contested or high-stakes freeform play with graded consequences and uncertainty disclosure.

## Moonshot
A mechanics-free campaign achieves the credibility of a thoughtful human GM: consequences emerge from fictional causes, not hidden fiat, while creativity remains unrestricted.

## Proving scenario
A PC tries to vault a collapsing bridge. Grimoire considers established ability, distance and urgency; it does not guarantee success or abruptly invent an unrelated difficulty merely to force a dramatic failure.

## Interfaces and independence
Requires the [Player Sovereignty](player-sovereignty.md) intent/authority contract; can prototype against existing scene facts and an isolated test harness. [Mechanical Resolution](mechanical-resolution.md) remains authoritative when a ruleset is selected.

## Obligations from 025

The first slice of 025 ([fictional abilities](../../ABILITIES.md)) produces a deterministic `AbilityAssessment` that this sprint consumes. 029 must:
- produce the attempt's labels with a labeller that is **not the narrator**, and put every intended outcome *and* the mechanism it uses into `needs` (the classification follows the labels; under-labelling is undetectable);
- gate "the power works" on `assessment.permitted` only, never on `approval == "not_needed"`;
- treat a `combination` as approvable only case by case, by an explicit director ruling naming the assessment's exact `terms` (the limits and prerequisites it sets aside); charge `charges`; proper combination rules remain milestone 3;
- authenticate who issues an `AbilityRuling` (adjudication channel only, never narration), and for a player-controlled actor take the attempt from that player's own contribution (`attempt_id` is its `contribution_id`);
- decide supersede/revoke for rulings and whether a ruling pins the entity's `resolved_source`; `AbilityRuling` is provisional and may be replaced here.

## Open questions
- What standard of evidence distinguishes obvious success from uncertain success?
- Who accepts a freeform high-stakes result?
- How much explicit adjudication should a casual RP player see?
