# Turn-Level Effect Authority and Scene Wrap-Up

> **V2 scope correction:** V2 has no V1 wrap-up API. Own the acceptance boundary for canonical events in native storage; once resolved, narration retry cannot reroll or reapply the outcome. Long-form memory consolidation is a separate consumer. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Establish when a fictional result becomes playable truth and what end-of-scene review is allowed to change.

## Scope
- Distinguish draft narration, proposed effect, accepted fictional event, committed operation and retrospective memory summary.
- Choose a clear authority boundary for actions during a scene versus existing scene-end wrap-up.
- Define edits, rerolls, alternate responses, scene deletion, forks and user corrections without duplicated effects.

## Milestones
1. Write event-state vocabulary and authority diagram using today's scene/wrap-up APIs.
2. Commit one simple fiction-first event during a turn and expose it to the next response.
3. Reconcile scene-end memory and revised turns without applying that event twice.

## Moonshot
An interactive continuity inspector lets players safely undo or branch causes and see dependent downstream effects before choosing a new canon.

## Proving scenario
A character gives away a key mid-scene: the next reply knows it is gone; wrap-up remembers the event but does not remove another key; rerolling the turn cannot secretly duplicate it.

## Independence and interfaces
Owns timing and acceptance policy, not file journaling internals or mechanics legality; can use current local records before generalized transactions.

## Wave 2 handoff: accepted state versus narration

Consume generic 010 record revisions and the Wave 1 PC-authority decision policy. Do not reinterpret 012's witnessed claims as committed physical events. An effect must have a stable accepted operation/event identity, preconditions and one-time publication; speculative narrator prose and a proposed player action cannot write canonical state. Durable event receipts become compatible with 020's independent evidence persistence after Wave 3 interfaces land; 030 owns cross-file exactly-once crash recovery. The first step must not require a V1 wrap-up API (none exists on V2).

## Open questions
- Which narrations imply immediate accepted truth, and which require player review?
- What is the undo contract for rewinding an earlier turn?
- How do existing ledger facts coexist with real-time state?
