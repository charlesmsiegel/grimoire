# Turn-Level Effect Authority and Scene Wrap-Up

**Status:** **first slice implemented**, headless. It is `grimoire.effects` (pure) and `grimoire.effectstore` (service), on additive `accepted/` and `wrapups/` record families in `grimoire.store`, plus an additive 017 `reopen`. The contract is in [EFFECTS.md](../../EFFECTS.md). The proving scenario, the failure cases and the crash exploration are executable. The requester decided:
- the work is done on a topic branch, with a PR into `v2`;
- independent reviewer subagents stand in for the Codex gates;
- revocation (undo of a revised turn) is in scope.

State changes are 017 relation changes. The 023 holdings obligation below is still open; it waits for 023 to merge.

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

## Obligation: holdings operations (023)

021's first slice merged before [Counted Inventory](counted-inventory.md) (023), so its accepted effects change only 017 relations. 023 has since merged; the obligation is open for 021's next slice:
- an accepted effect that moves, uses or finds counted items carries a [holdings operation](../../HOLDINGS.md);
- that operation's `op_id` is derived from the accepted event, and its `cause` is the event;
- it is committed in the same transaction as the event record and its receipts, and checked by the same receipt and duplicate rules.

023's handoff below then applies. A rerolled or retried turn can never spend or grant twice.

### Handoff from Counted Inventory (023): item mentions become reviewed proposals

023's milestone 3 is re-homed here by requester decision. When a narration or turn mentions items changing hands, being used or being found, 021 may *propose* a [holdings operation](../../HOLDINGS.md): an `Operation` with an `op_id` derived from the accepted event and `cause` set to that event's id. On acceptance, 021 plans it with `HoldingsLibrary.apply` and folds the resulting change's `puts` into its own transaction after `recheck()`, holding the store lock (or, for a holdings-only change, commits it with `commit(approvals)`). It is never an automatic, unverified grant. Two rules follow from 023's contract:

- **Retries.** A retried turn reuses the same whole `Operation` (`op_id`, lines, `note` and `cause`), so 021 should persist the operation it proposed rather than regenerate it: a retry with a regenerated note is refused as `op_id_reused`. An identical retry is a no-op while the entry is in the holders' logs. To stay exactly-once after eviction, 021 must record each holder's `seq` when it creates the operation and pass it as `basis` on every retry (a stale retry is then refused as `beyond_history`), together with the first change's `holders=change.holder_etags` (a holder replaced in between is refused as `holder_replaced`, and `HolderReplaced` is never retried). This holds unless a holder's file was restored or recreated in between, which forgets the operation (see [HOLDINGS known limits](../../HOLDINGS.md#known-limits)).
- **Rerolls and edits.** A rerolled turn whose effect has *different* lines cannot reuse the `op_id` (it is refused as `op_id_reused`), and a new `op_id` does not undo the first one. Either commit holdings only when a turn is finally accepted, or commit an explicit compensating operation under a fresh `op_id` (`cause` naming the reroll).

## Obligations from 025

Ability costs from [fictional abilities](../../ABILITIES.md) are reported, never committed. 021 owns committing them through accepted state: charge each `attempt_id` **once**, from the assessment's `charges` (every `(ability, cost)`, including the other abilities of an approved combination), and only when the attempt was `permitted`. Instance ability changes (`added`/`replaced` abilities facets) are accepted state, never narration.

## Open questions
- Which narrations imply immediate accepted truth, and which require player review? **Partly answered:**
  - none does by itself; narration only proposes;
  - a player character's voluntary operation always needs its controller's own contribution and an endorsement of the exact operations;
  - every outcome needs the director's key.

  *When* director acceptance needs a human's review is 035's policy.
- ~~What is the undo contract for rewinding an earlier turn?~~ Revocation:
  - it is strictly latest first;
  - it needs the same keys as accepting, with the original endorser;
  - it restores relations in place (retract what was begun, reopen what was ended);
  - it is refused when something depends on it;
  - `partial=True` is the repair path.

  Cascading undo is the moonshot; forks belong to 054.
- ~~How do existing ledger facts coexist with real-time state?~~ The accepted events are the ledger and the relations are the state. Receipts tie each operation to a relation id, and `continuity` reports any disagreement (`receipt_mismatch`, `orphan_effect`) instead of trusting either side.
