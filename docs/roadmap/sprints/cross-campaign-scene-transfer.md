# Safe Cross-Campaign Scene Transfer

**Outcome:** Copy a scene into another campaign without assuming characters with the same name or world are identical.

## Scope
- Create a new target scene with explicit cast, actor/version and chronology mapping.
- Preview missing sources, incompatibilities and continuity choices rather than mutating the origin.
- Distinguish transcript-only import from selected transfer of state and attached assets.

## Milestones
1. Transfer a scene between campaigns sharing a world, transcript only.
2. Handle an alternate character portrayal with explicit mapping.
3. Offer optional reviewed continuity and asset transfer with conflict recovery.

## Proving scenario
A copied scene gets a new ID; an alternate NPC portrayal stays distinct and the original campaign remains unchanged.

## Independence and interfaces
Can start with transcript-only existing import APIs; source-qualified identity enriches cross-world mapping but is not a blocker to same-world MVP.

## Open questions
- Which historical facts may move with a transcript?
- How should missing art be presented?
- When is transferring an effect fundamentally unsafe?
