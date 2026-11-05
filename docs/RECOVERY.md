# Recovering partly published acceptances (Sprint 030, first slice)

Current contract of `grimoire.effectstore.EffectRecovery` and 023's `grimoire.holdings.complete`. The roadmap's
first useful proof for [Durable Action Recovery](roadmap/sprints/durable-action-recovery.md) is "replay or
abort a partial accepted event exactly once"; this slice delivers it for one accepted effect (021) with its
relation changes (017) and holdings (023). Builds on [EFFECTS](EFFECTS.md), [HOLDINGS](HOLDINGS.md),
[GRAPH](GRAPH.md) and the store journal ([STORAGE](STORAGE.md)).

## Where partial publications come from

An acceptance is **one** store transaction: the accepted-event file, its relation adds and end rewrites, and its
holder files. The journal makes it all-or-nothing under crashes alone. It is still left partly written when:

- **staging vanished after the commit point:** a crash during publication, then a cleanup tool or a person
  removes the remaining `.txn/*.new` files. Recovery publishes the entries whose staging survived and reports the
  rest in `RecoveryReport.incomplete` ([STORAGE](STORAGE.md)). Entries publish in put order, relations first,
  then the event file, then holder files, and recovery abandons the whole journal while nothing is published yet.
  So a crash leaves only a prefix: *the relation changes without their event* or *the event and its relations
  without the holder files*;
- **a publish race** (`WriteConflict`, step 11): an external edit of one target is kept and the rest publish;
- **sync, restores or hand edits** delivering only some of the files, in any combination (for example the holder
  file alone: the item spent, nothing else).

021 reports the two shapes and refuses a plain retry:

| Shape | What landed | `continuity` | A retry of `accept` |
|---|---|---|---|
| **A** | an active event whose receipts fail because a receipted relation file, an end rewrite or a holder entry never landed | `receipt_mismatch` | `receipt_mismatch` |
| **B** | relation files or holder-log entries carrying `effect:accepted/<X>` with no `accepted/<X>.json` | `orphan_effect` | `orphan_effect` or `precondition_failed` |

## The service

`EffectRecovery(library)` takes a `TurnEffectsLibrary`. It holds the same trusted handle (the outcome key) and
gains no other authority: both operations publish only what was already accepted. Each returns 021's
`EffectChange`: `preview()` gives the exact bytes, `commit(approvals)` writes them as one journal-protected
transaction after re-checking everything the decision rested on. Each decision is made under one store lock,
failing closed as `accept` and `revoke` do (`undecodable_event`, `inconsistent_ledger`, `undecodable_relation`,
`undecodable_holdings`).

### `complete(ref, *, expected)`: finish shape A

Writes exactly the changes the active event's receipts name that never landed, as the original commit wrote them,
so the receipts then verify. The event file is never rewritten.

1. The event must exist (the store's own error otherwise), be active (`already_revoked`), have etag `expected`
   (`StaleWrite`) and be the **latest** active event (`not_latest`, as `revoke`): a later event's play may
   contradict what finishing an earlier one would write, and holdings' exact seqs cannot protect relations.
2. Receipts and the holdings receipt (lenient, as the duplicate check reads it) already verify: a **no-op**
   (`duplicate_of=ref`, `preview() == {}`), whose commit re-checks like a duplicate's. This is the exactly-once
   retry.
3. Any `effect:revoked/<id>` token for the event in a campaign relation or holder file refuses
   (`not_completable`): a revocation of it partly landed, and completing would redo what it undid.
4. **A human repair is never doubled.** A holder that logs the event's holdings entry and logged anything after
   it, or a relation one of the event's `begin`s wrote that has been ended since, refuses (`not_completable`,
   naming `holdings` or the operation): finishing would write the rest of an effect someone already undid or
   finished by hand by other means (a hand refund of a synced spend would leave the effect in force uncharged).
5. Each failing relation receipt must be plainly **unpublished**:
   - a `begin` whose receipted relation file does not exist is re-added **under the receipted relation id**,
     `since` the event's moment, provenance `effect:accepted/<id>` (017's `add(relation_id=...)`, below). 017's
     checks apply: `missing_endpoint`, `instance_id_conflict`, an overlapping record of the fact is
     `precondition_failed`;
   - an `end` whose receipted relation still records the fact, is unretracted, unended, began before the moment
     and was never ended by this event and reopened, gets its ending (`until` the moment, same provenance) by
     017's `end` with CAS;
   - anything else (another fact, a retraction, an ending by something else, the id now held by another file)
     is `not_completable`, naming the operation. Nothing is written.
6. When the holdings receipt fails, 023's `HoldingsLibrary.complete` finishes it at the receipt's exact seqs
   ([below](#023-complete)). A holder that no longer fits the receipt is `not_completable` (`holdings`); other
   023 refusals pass through (`insufficient`, `stale_count`, `missing_holder`, `instance_id_conflict`,
   `too_large`, ...).
7. Ability charges need no write: they are recorded on the event.
8. The commit re-checks: accepted events unchanged, each relation change's own `stale()`, every relation file
   readable, every receipted relation that verified pinned as read (`relation changed`), each written holder's
   instance file (023's `recheck`), every other receipted holder file pinned as read (`holder file changed`),
   the agents' instance files (`agent missing or changed`), no actor id paired with another kind, and that the
   relation and holder files naming the event in a 021 token are still exactly those read: a revocation landing
   in between is `StaleWrite`, not completed over.

### `adopt(proposal, *, controls, contributions=(), endorsements=())`: finish shape B

The retry of an acceptance whose changes landed without its event file. It presents the same proposal and keys.
After the campaign, chat and fail-closed checks and the duplicate path below, it finds the orphan (any unreadable
relation or holder file refuses first, since the orphan could be in it), then makes every check `accept` makes
(permitted uses, actor pairs, authorization and endorsements, one effect per turn, the sequence floor, the
attempts), then writes the event file under the orphan's id X, with receipts naming the landed changes, plus
whichever changes are still missing.

- An active event with this fingerprint already exists: exactly `accept`'s duplicate path (a no-op, or
  `receipt_mismatch`, for which `complete` is the way). This is the exactly-once retry after the adoption landed.
- **Finding X.** A landed change of this proposal, written by an acceptance with no event file, is:
  - for a `begin`: an unretracted relation recording the operation's fact with `since.sequence` equal to the
    proposal's and provenance `effect:accepted/<X>`;
  - for an `end`: an unretracted relation recording the fact, ending at the proposal's sequence with provenance
    `effect:accepted/<X>`;
  - for the holdings part: a touched holder logging an entry with this effect's derived op id, the same lines and
    note, and cause `effect:accepted/<X>`. An entry with that op id but other content or a free-text cause is a
    human repair (`orphan_effect`).
- Exactly one X (`nothing_to_adopt`, `ambiguous_orphan`; one operation landed twice is ambiguous too). X must
  name no accepted-event file and no 020 event file, and every 021 token naming X in the campaign's relation and
  holder files must be one of these landed changes (`orphan_effect`: X has another history, such as an
  `effect:revoked/<X>` token or a token on an unrelated record).
- Operations not landed are planned as `accept` plans them; landed ones are not re-planned (017's preconditions
  would refuse their own record), but a landed `begin` still needs both endpoints' instance files
  (`missing_endpoint`, pinned at commit), must not have been ended since (`not_completable`), and each landed
  receipt must then verify as `continuity` checks it.
- Holdings landed on every holder: the entries' seqs are the receipt. On some: the rest are completed at the seqs
  the landed entries' `peers` name, as `complete` does. On none: applied as `accept` applies it, with cause
  `effect:accepted/<X>`. Another unrecorded change on a touched holder still refuses (`orphan_effect`).
- **A human repair is never doubled.** A touched holder that logged anything after this effect's entry, or, when
  no entry landed but relations did, anything stamped at or after the earliest landed relation change (its
  `recorded_at`, or its ending's `at` for an `end`), refuses (`not_completable`, `holdings`): that change may be
  a hand refund of the stray spend (which a later abort would refund again) or a hand charge (which adopting
  would charge again). A time on either side that is not an aware ISO time (017 and 023 keep hand-edited text)
  refuses the same way.
- The event gets `ordinal = 1 + max`, `accepted_at` now and `committed_by` the outcome key, like any acceptance.
  The commit re-checks as `accept`'s does, plus every landed relation and holder file pinned as read, and the
  files naming X in a 021 token still exactly those read (`StaleWrite` when another history arrives).

### Aborting

No new write path:

- **Shape A:** `revoke(ref, reason, expected, endorsements, partial=True)`. Unpublished operations are recorded as
  skipped and hold nothing; a holdings part that landed nowhere is skipped as `never applied`; one that landed
  everywhere is undone. Nothing is then in force, and a late retry of the same effect is `attempt_revoked`.
- **Shape B:** `adopt`, then `revoke`: two transactions, each exactly once, and the state between them is the
  coherent completed one.

## 023 `complete`

`grimoire.holdings.complete(op, current, at, *, seqs, exists, size, max_file=MAX_FILE)` is pure.
`HoldingsLibrary.complete(op, *, seqs) -> HoldingsChange` reads like `apply` under the store lock.

- `seqs` names exactly the operation's holders, each a positive integer (`invalid_field`).
- Each holder either logs the operation at `seqs[holder]` with the other holders' seqs as peers (it landed), or
  has no entry for it and is at exactly `seqs[holder] - 1` (it never got it). Logged with other content:
  `op_id_reused`; logged at another seq or with other peers: `seq_moved`.
- Every holder landed: `AlreadyApplied`, whatever they logged since (nothing is written, so nothing can be
  doubled; `EffectRecovery` makes its own moved-on check before it calls this).
- Otherwise a landed holder that logged anything after its entry, or a missing one not at exactly
  `seqs[holder] - 1`, is `seq_moved`: a holder changed since (a later operation may be a hand repair of this very
  partial, and completing would then give twice), or was restored further back. Missing holders must have an
  instance file (`missing_holder`).
- The lines are applied in order to the **missing holders' stacks only**. A line's effect on a holder depends only
  on that holder's counts, so this is exactly what the whole operation did there: `insufficient`, `stale_count`,
  `count_limit`, `stack_limit`. Each missing holder gets the entry the original would have written (same op id,
  lines, note and cause, its receipted seq, peers naming the other holders' receipted seqs, `after` from its own
  counts) and eviction as `plan` does (`too_large`).
- The store change puts, and pins the instance files of, the written holders only; `entries` names every holder's
  entry. A paired written holder is `instance_id_conflict`. A caller relying on the landed holders' files pins
  them itself (`EffectRecovery` does).

## 017 `add(relation_id=...)`

`RelationLibrary.add` takes an optional `relation_id`: the relation is written under that id instead of a fresh
one, create-only. Any file already at that id, even an unreadable one, refuses it as `relation_id_taken`; the
commit's create-only put catches a race. Only `complete` uses it, to restore a receipted relation under the id
its event names.

## Proof

- `backend/tests/test_effect_completion_recovery.py`: the proving scenario with the real journal. Tam throws one
  ball at the wisp and they become allies; the process crashes after the commit point once the relation (shape B)
  or the event file (shape A) is published, the remaining staging is removed and recovery runs. `complete` or
  `adopt` then yields one coherent outcome (one ball spent, the companion once, continuity clean); a retry and a
  late `accept` are no-ops. `explore_crashes` over both, with I/O errors and recovery crashes, checks atomicity,
  idempotent recovery, no residue and a `charged_once` invariant from every recovered state. A partial revocation
  aborts shape A coherently.
- `backend/tests/test_effect_completion.py`: every partial shape (including the holder file alone, from a sync),
  each refusal above, pins at commit, abort of both shapes, and 017's chosen id.
- `backend/tests/test_holdings_complete.py`: 023's `complete`, including a move landed on either side.

## Known limits

- **A deliberate restore cannot be told from a vanished write.** A holder file restored to its pre-effect backup
  looks exactly like one whose entry never landed, so `complete` and `adopt` finish it. The preview names every
  holder file written; a human who restored on purpose aborts instead.
- **A hand-deleted relation file cannot be told from a vanished one either.** `complete` re-adds a receipted
  relation whose file is gone, even when a person deleted it to undo the effect by hand; the preview names it.
- **Repairs by hand refuse; they are not recognised.** The moved-on rules refuse on any later holder change or
  ending, including an unrelated one (a director grant of another item to the same holder), which then makes it
  a human repair. Aborting by `revoke` after a hand refund (021's undo applies the inverse lines to the current
  counts) refunds again: a human who repaired by hand does not also abort.
- **A holder that never got its entry and was touched since** can neither be completed (`seq_moved`) nor aborted
  coherently: `revoke(partial=True)` reads "different operation" at its seq and skips the undo as possibly in
  force (`partial_revocation`). A multi-holder holdings part that landed on some holders only is the same for
  `revoke(partial=True)`: complete it first, then revoke. Both stay a human repair.
- **Aborting needs the latest event**, as `revoke` does (`not_latest`); so does `complete`.
- **Adopting a relation-only orphan trusts the presented proposal's chat and turn.** A relation records its fact,
  moment sequence and token, not the turn, so a different proposal at the same sequence whose operations include
  the orphan's could adopt it. The holdings op id derives from the effect's fingerprint, so a holdings orphan is
  tied to its own effect.
- **A hand-deleted event file cannot always be told from a vanished one.** A revoked event whose revocation left no
  `effect:revoked/<X>` token anywhere (every operation and the holdings undo skipped) and whose file is then
  deleted by hand can be adopted back as active.
- **The "touched since" test for a relation-only orphan reads timestamps.** Holder logs carry no seq tied to a
  relation, so the test compares each log entry's `at` with the landed relation change's time. It is
  conservative for changes stamped later (an unrelated later change of the ball count refuses the adoption, which
  is then a human repair), but trusts the clocks: a hand charge stamped earlier than the relation, on a device
  whose clock lags, is not seen and adopting charges again. The preview shows the holder files it writes.
- **Restart does not recover by itself.** Nothing detects a partial publication and runs these operations, and
  shape B needs the original proposal and endorsements, which a crashed process no longer holds. **Obligation on
  035 (and milestone 2):** persist the proposal and its endorsements before committing an acceptance, so a
  restart can adopt; until then shape B after a real crash is a human repair. The proving tests carry the
  proposal across the crash in memory.
- **No record that a completion or adoption ran,** beyond the writes' own tokens (`effect:accepted/<X>` on what
  they publish). The 007 audit obligation stays with this sprint.

## Out of this slice (named owners)

| Item | Owner |
|---|---|
| A partly published **revocation** (an undo logged on some holders only; a revoked event file with an applied undo missing from a holder) | 030, later slice |
| Recording a human repair of a skipped holdings undo (`partial_revocation`) | 030, later slice |
| Orphan detection and duplicate recognition beyond the bounded holder log; per-file identity (epochs) | 030 / 019 |
| A durable operation record for actions spanning several store transactions (inventory, actor and association) | 030 milestone 2 |
| Detecting partial publications at open and persisting proposals so restart can recover | 035 / 030 milestone 2 |
| Recording in the event that it was completed or adopted; the 007 decision audit | 030, later slice |

No new record family, schema or codec: every write is an ordinary 017 relation file, 023 holder file or 021
accepted-event file that a pre-030 build reads normally. All fixtures are synthetic.
