# Idempotent Action Effects and Crash Recovery

**Outcome:** One accepted action updates multiple human-editable records once, even after crashes, retries and external edits.

**Prerequisite met:** [021's next slice](turn-effects-and-wrap-up.md#next-slice-holdings-and-ability-charges-delivered) (holdings operations from accepted effects, committing ability costs, charging each `attempt_id` once) is done ([EFFECTS](../../EFFECTS.md#holdings-and-ability-uses)). An accepted effect and its holder files are one store transaction, and each attempt is charged once while an event holds it. Left for this sprint:
- completing a partial publication (an event file without its holder files, or holder files without their event, reported today as `receipt_mismatch` and `orphan_effect`);
- recording a human repair of a skipped holdings undo, which 021 reports as `partial_revocation` until then;
- orphan detection and duplicate recognition beyond the holders' bounded logs.

## First slice (delivered)

A partly published acceptance (021) is finished or aborted exactly once ([RECOVERY](../../RECOVERY.md)):
`EffectRecovery.complete` writes the changes an event's receipts name that never landed, `adopt` records changes
that landed without their event, and `revoke(partial=True)` aborts. 023 gained `complete` (finish an operation at
its receipt's exact seqs) and 017 `add(relation_id=...)`. The proving scenario runs with the real journal: the
store publishes relations before the event and holder files, so a crash leaves the companion registered and the
ball not yet spent (the reverse comes only from sync or a restore, and is tested too). Left for later slices: a
partly published revocation; recording a human repair of a skipped holdings undo; recognition beyond the bounded
holder log; persisting the proposal before commit so restart can adopt (with 035); the 007 decision audit; the
multi-transaction operation record (milestone 2).

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

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Surface the persisted `grimoire.security` decision audit, including the lifecycle of grants and approvals, as part of "expose audit/undo"; a decision is recorded before it takes effect (007).

## Open questions
- What is the smallest durable file-backed operation record?
- How do manual edits interact with retries?
- How can compensation and undo remain truthful after dependent events?
