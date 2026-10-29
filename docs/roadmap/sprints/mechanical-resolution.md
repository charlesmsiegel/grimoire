# Legal Mechanical Actions, Rolls and Effects

**Outcome:** Once an action is accepted, its system-correct outcome determines narration and persistent effects.

## Scope
- Reconcile existing Mechanics II Actions/Effect DSL with present checks, rolls, sheet editors and recorded randomness.
- Enforce legality, target requirements, pools, opposition, partial outcomes and associated resource/condition changes.
- Ensure the narrator cannot turn a miss into a hit or reroll a fixed outcome.

## Milestones
1. Resolve and commit a simple D&D action using a recorded roll.
2. Support opposed or partial outcome using the same shared interface but ruleset-specific logic.
3. Connect durable effects, conditions and action journals to the existing scene flow.

## Proving scenario
A recorded failed roll stays failed after narration retry; WoD opposed pools and PbtA partial success produce their own appropriate effect shapes.

## Independence and interfaces
Uses a ruleset contract and transaction port; generic combat UI, module tooling and narrative obstacles are independent consumers.

## Open questions
- What can be reused unchanged from Mechanics II?
- What action authority is necessary before a roll?
- Which conditions require durable effects versus derived displays?
