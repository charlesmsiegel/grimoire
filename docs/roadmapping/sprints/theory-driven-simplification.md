# Theory-Driven Code Simplification

**Outcome:** Make a complicated Grimoire subsystem conceptually smaller, not merely split into smaller Python files.

## Scope
- Recover its domain model, authoritative state owners and likely next feature changes before editing.
- Remove duplicated decision paths, accidental wrappers and mutually inconsistent abstractions.
- Measure modification effort and regression risk against representative change rehearsals.

## Milestones
1. Select one verified high-churn area and document how it works.
2. Remove one redundant design while retaining external behavior and compatibility.
3. Prove a realistic new change crosses fewer boundaries; repeat for other areas.

## Moonshot
Maintain an executable architecture map that explains the ownership and invariants of every important subsystem to a new human or coding agent.

## Proving scenario
A new actor kind or world lookup is added without a second write authority; existing campaigns and the Android/Pydantic 1 runtime still work.

## Independence and interfaces
Independent of feature delivery; target one subsystem at a time. Coordinate touching world and mechanics interfaces with their experiments, but do not wait for them.

## Open questions
- Which surfaces show verified conceptual debt?
- What changes are strong tests of a recovered theory?
- Which compatibility facades are intentional?
