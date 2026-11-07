# Ledger merge (028, before 044)

`grimoire.ledgermerge` computes the merge of one campaign's **ledger** from two devices that each played offline: its 021 accepted events and wrap-ups ([EFFECTS](EFFECTS.md)), the 017 relations they change ([GRAPH](GRAPH.md)) and its 023 holder files ([HOLDINGS](HOLDINGS.md)). 019 holds these families back (`held`, [SYNC](SYNC.md)), since a file-by-file copy could deliver half an operation or give two events one ordinal. This package is the merge itself. Wiring it into sync, and the owner's review of what it cannot settle, come later ([DEVICE_SYNC](DEVICE_SYNC.md)).

It is pure: it uses no store, clock, randomness or JSON. The caller reads each library into a `Ledger`: every file as a `Version`, which holds the decoded value, its etag, and the envelope's owner and unknown fields. The caller then calls `merge_ledger(base, sides, instances=..., sizer=...)`, where:

- `base` is the ledger as both devices last agreed;
- `sides` holds exactly two ledgers, keyed by device id;
- `instances` are the campaign's instances in the merged library;
- `sizer` renders a holder file's exact size in the store envelope.

## Result

- **`Merged(files, order)`** maps each ledger file to its output:
  - `Kept(etag, source)` means an existing version's bytes, copied byte for byte from `base` or a device;
  - `Rewrite(schema, payload, owner, extra, previous)` means a new file, rendered in the store's scope-record envelope.
  - `order` is the merged order of the new events.
  - Both devices compute the same `Merged`, because the result does not depend on which side is listed first.
  - Each `Rewrite`'s `previous` is fixed by the inputs: the agreed version's etag, or else the etag of the lower device id's version. So both devices render the same bytes.
- **A review** (`Review`, or `LedgerReview` from `merge_ledger`) lists every reason the merge stopped, sorted, as `ReviewItem(reason, subject, detail)`. Nothing is written.
  - When every reason is a policy one (`POLICY`: `same_chat`, `revoked_vs_extended`), `candidate` carries the merge that would otherwise be made, and that merge replays cleanly.
  - Whether to take it is the owner's decision, not this package's.

An event, relation, holder or logged operation either device had is never missing from a `Merged`. Anything the merge cannot keep stops it as a review.

The merge is idempotent. Merging a merged ledger with itself gives the same bytes, and so does merging it with either side before that side took the merge, unless the merge evicted log entries (see the limits below).

## Events

1. **Choice.** Each event's version is chosen three-way.
   - An agreed event changed on one device takes that device's version.
   - Changed on both, it is `both_changed`.
   - Missing on a device, it is `deleted`. A deletion is never merged.
   - A new event two devices hold, one in force and one revoked but otherwise alike, takes the revoked version, since a revocation is one-way (`revoked_copy`). With three or more devices this is how a revocation arrives: one peer got the event before it was taken back, another after. It is then checked like any new event revoked on its device (`partial_revocation`) and against the device holding it in force (`revoked_vs_extended`).
   - A new event both devices revoked themselves, so that the revocations differ, is `both_changed`.
   - Any other new id with two contents is `id_collision`. Since 028 tags new ids with the device ([DEVICE_SYNC](DEVICE_SYNC.md#per-device-ids)), tagged ids never collide this way; untagged ones can.
2. **Order.** Every kept event, agreed or new, goes in one order (`causal_order`), and ordinals are its positions, 1 to n, so agreed events are renumbered too:
   - **Causal.** No event goes before one it was accepted after. A library records that as `after` ([EFFECTS](EFFECTS.md#records), `/2`) when it is identified, or when it holds any event that records `after`. So a library whose identity was lost for a while (`.sync` deleted) and later restored still records `after` for what it accepted meanwhile, under untagged ids. Only a library that never took part (no identity, and no event with `after`) writes events exactly as before. An event without `after`, accepted by such a library or before this change, comes after the event without one just before it by ordinal, so such events keep the order their library accepted them in. 021's undo stack (latest first) therefore still takes back the last event a device accepted.
   - **Otherwise by key** (`merge_key`): `moment.sequence`, or `-1` for a revoked event (`REVOKED_SEQUENCE`), so a revoked event never holds back the events accepted after it; then the tag of the device that accepted it, read from the id (`e-<n>-<tag>`; untagged ids first, as the empty tag); then the id's allocation counter (`<n>`; an id without one after those with one); then the id. Among the events ready to place, the smallest key goes first.
   - **The same for every pair.** The order depends only on the events, never on which two devices are merging, which side holds an event, or its current ordinal. What a device holds always includes everything its events were accepted after, and on any such set the order is the larger order restricted to it. So with three or more paired devices merging pair by pair, each from its own agreed state, every pair numbers the same events the same way, and the numbering settles once every device holds every event. One exception: the key of an event changes when it is revoked, so a revocation reaching the devices one by one may renumber once, and settles when every device holds it.
   - **Sequence order.** 021 requires active events in ordinal order to have non-decreasing sequences (`inconsistent_ledger`). An event a device accepts is never at a sequence below an active event it was accepted after. With revoked events keyed first, the key therefore never decreases along "after", and taking the smallest ready key gives active sequences in order. A device may take back an event at a later sequence, then accept one at an earlier sequence after it, while the other device accepted one in between: the revoked event goes first and the rest follow by sequence. Only a hand-edited `after` can leave no such order, and the merge then stops with `new_problem` naming `inconsistent_ledger`, never a merged ledger that breaks it.
   - Events that each say they came after the other (only a hand edit makes that) stop the merge as `order_cycle`.

   A first merge may renumber a library's earlier events. Ids are kept.
3. **Rewrite.** Only `ordinal`, `holdings.seqs` and `revocation.holdings_seqs` are rewritten (`REWRITTEN`). The fingerprint covers none of them, so every endorsement stays valid. Two versions that differ only there are one event, so a hand edit of only those fields of an agreed event is discarded: the agreed record is kept, renumbered as above.
4. **Replay.** Each new event is replayed, in merged order, with 021's planner against the merged relations as they stood just before it. It must reproduce the relation receipts it recorded (`receipt_mismatch`). A planner refusal comes back under the planner's reason, such as `precondition_failed`. An ability's actor must exist (`missing_agent`).
   - A new event revoked on its device must have been undone in full: no relation change skipped and no holdings change left in force (`partial_revocation`).
5. **Policy.** These are checked across the two devices:
   - one chat took new events on both (`same_chat`);
   - an event the merge keeps revoked is held in force by the other device, which accepted new events after it, directly or through others (`revoked_vs_extended`). The event is either agreed, or new and revoked on its way to only one of the two (above). Only events the other device recorded as accepted after it count: an event a third device gave it, accepted without the revoked one, does not, and neither does a device that already has the revocation;
   - one fingerprint was accepted as two events (`same_effect`);
   - one attempt was spent twice (`attempt_reused`).

Wrap-ups and relation files merge three-way, as whole files. One file changed differently on both devices is `both_changed`. Two relations under one new id are `id_collision`. The exception is a relation file that one device holds as the other's with only the revocation of an event the merge keeps revoked applied: 021 retracts a relation the event began and reopens one it ended, each under the event's `revoked` token. That version is kept (`revoked_relation`), as the event's revoked version is.

After all of that, the merged ledger is checked with 021's ledger checks: the problems `continuity` reports from the accepted events, relations, wrap-ups and holder files. Its 020 bridge check and store problems are not run, since the merge does not read `events/` or other files. The merged ledger may show no such problem that neither device showed already (`new_problem`).

## Holdings

1. **Whole files.** A holder file only one device changed, or both changed alike, is taken whole, unless an operation it shares with a replayed file pulls it into the replay. Its log must still extend the agreed one (entries it already evicted aside).
   - So a merged file stays as it is when merged again, even once eviction dropped entries its log no longer shows.
   - Every other file is replayed, as below.
2. **Extension.** A replayed device file must be the agreed file plus new log entries, numbered contiguously after the agreed seq, with none of them evicted yet. Anything else is `log_diverged`.
3. **Collection.** The new operations of both devices are collected once each, keyed by op id.
   - The same op id with different lines, note or cause is `id_collision`.
   - An operation logged on only some of its holders on one device is `partial_op`.
4. **Order.** Operations are put in one order:
   - each device's order on each holder is kept, and a cycle between the two devices' orders is `log_diverged`;
   - ties go by the event merge's position for an effect's operation (an inverse after its event);
   - then by the lower device id;
   - then by op id.
5. **Replay.** The operations are replayed from the agreed stacks with 023's own `plan`, all or nothing.
   - `seq`, `peers` and `after` are recomputed, and each entry keeps its original `at`.
   - The log is evicted as any write would evict it, against the exact envelope size.
   - A refusal comes back under 023's reason, such as `insufficient`, `count_limit` or `missing_holder`. An operation is never dropped.
6. **Hand edits.** A device's final count, less the agreed count, less the replay of its own new operations, is a hand edit.
   - It is kept on top of the replay when the other device neither edited that count nor changed it by an operation only it logged.
   - Two equal edits of a count that no one-sided operation touched count as one edit.
   - Anything else is `hand_edit_overlap`.
7. **Receipts.** The events' holdings receipts are rewritten from the replayed seqs.

## Not settled here

These are decisions for the owner's review: which reviews may merge automatically, what a review offers, and whether a hand edit or an operation wins an overlap.

Known limits:

- A hand edit that a later operation on the same device depended on (an edit that made a removal possible) refuses on replay, because the edit is applied after the replay.
- Domain conflicts beyond 017's preconditions are not detected, for example two devices each moving a scene on.
- An undecodable file is the caller's to report before the merge.
- Events without `after` form one line by the ordinals of the versions kept. Two unidentified libraries that each accepted events offline (which also collide on ids) would interleave by ordinal, not by what each accepted after.
- Two devices that each revoked one new event stop as `both_changed`. Picking one revocation would also mean picking its relation and holder changes, which the merge does not do alone.
- Holder logs are still numbered per pair. With three or more devices that all change one holder, one pair's merge can put an entry between two that another pair agreed, and that pair's next merge is then `log_diverged`. That is a review, never a reordered log, but such devices do not converge on their own. How three devices share a holder is open.
- A device that missed a merge whose replay evicted entries cannot be merged against it automatically: the merged log no longer shows those entries (`log_diverged`). Once both devices hold the merge, it is stable.
- Operations a device logged offline past 023's log bounds (1000 entries, or the file size) were evicted on that device, so that holder is `log_diverged` when both devices changed it.

Proved in `backend/tests/test_ledgermerge.py`, `backend/tests/test_ledgermerge_pairs.py` and `backend/tests/test_ledgermerge_properties.py`. The last runs seeded random offline play: both side orders agree, nothing is dropped, the merged library reads clean, and a second merge, including against a lagging side, gives the same bytes. It also runs three devices paying, revoking and merging pair by pair in random order: tagged ids never give `id_collision`, and when no merge stopped, all three end up holding one library.
