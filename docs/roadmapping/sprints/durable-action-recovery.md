# Idempotent Action Effects and Crash Recovery

**Outcome:** One accepted action updates multiple human-editable records once, even after crashes, retries and external edits.

## Scope
- Carry forward Mechanics II's existing Action/Effect/journal proposal rather than inventing a rival engine.
- Validate prerequisites, expected versions, logged randomness and all intended writes before publication.
- Recover partial file publication and expose audit/undo or explicit unresolved-conflict state.

## Milestones
1. Prove exactly-once transfer or consumption of one resource.
2. Commit an operation affecting inventory, actor and association with fault injection.
3. Generalize only guards and recovery protocols validated by a second consumer.

## Proving scenario
A process crashes after decrementing a capture device but before registering a companion; restart recovers one coherent outcome and never charges twice.

## Independence and interfaces
Uses existing atomic file writes and locks; authored world Change Sets are a separate semantic caller, not forced into gameplay Effects.

## Open questions
- What is the smallest durable file-backed operation record?
- How do manual edits interact with retries?
- How can compensation and undo remain truthful after dependent events?
