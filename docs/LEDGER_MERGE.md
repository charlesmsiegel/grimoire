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

The merge is idempotent. Merging a merged ledger with itself, or with either side before it took the merge, gives the same bytes.

## Events

1. **Choice.** Each event's version is chosen three-way.
   - An agreed event changed on one device takes that device's version.
   - Changed on both, it is `both_changed`.
   - Missing on a device, it is `deleted`. A deletion is never merged.
   - A new id with two contents is `id_collision`. Since 028 tags new ids with the device ([DEVICE_SYNC](DEVICE_SYNC.md#per-device-ids)), only untagged libraries collide.
2. **Order.** The events the base lacks are ordered by:
   1. `moment.sequence`;
   2. the device that accepted the event, lower device id first. This is read from the id's device tag, not from which side holds the event, so a side that already took the merge orders the same way;
   3. that device's ordinal;
   4. the id.

   They are numbered `max(base ordinals) + position`. Ids are kept.
3. **Rewrite.** Only `ordinal`, `holdings.seqs` and `revocation.holdings_seqs` are rewritten (`REWRITTEN`). The fingerprint covers none of them, so every endorsement stays valid. Two versions that differ only there are one event.
4. **Replay.** Each new event is replayed, in merged order, with 021's planner against the merged relations as they stood just before it. It must reproduce the relation receipts it recorded (`receipt_mismatch`). A planner refusal comes back under the planner's reason, such as `precondition_failed`. An ability's actor must exist (`missing_agent`).
   - A new event revoked on its device must have taken effect in full (`partial_revocation`).
5. **Policy.** These are checked across the two devices:
   - one chat took new events on both (`same_chat`);
   - an agreed event was revoked on one device while the other accepted new events (`revoked_vs_extended`);
   - one fingerprint was accepted as two events (`same_effect`);
   - one attempt was spent twice (`attempt_reused`).

Wrap-ups and relation files merge three-way, as whole files. One file changed differently on both devices is `both_changed`. Two relations under one new id are `id_collision`.

After all of that, the merged ledger is checked as 021's `continuity` would check it. It may show no problem that neither device showed already (`new_problem`).

## Holdings

1. **Extension.** Each device's holder file must be the agreed file plus new log entries, numbered contiguously after the agreed seq. Anything else is `log_diverged`.
2. **Collection.** The new operations of both devices are collected once each, keyed by op id.
   - The same op id with different lines, note or cause is `id_collision`.
   - An operation logged on only some of its holders on one device is `partial_op`.
3. **Order.** Operations are put in one order:
   - each device's order on each holder is kept, and a cycle between the two devices' orders is `log_diverged`;
   - ties go by the event merge's position for an effect's operation (an inverse after its event);
   - then by the lower device id;
   - then by op id.
4. **Replay.** The operations are replayed from the agreed stacks with 023's own `plan`, all or nothing.
   - `seq`, `peers` and `after` are recomputed, and each entry keeps its original `at`.
   - The log is evicted as any write would evict it, against the exact envelope size.
   - A refusal comes back under 023's reason, such as `insufficient`, `count_limit` or `missing_holder`. An operation is never dropped.
5. **Hand edits.** A device's final count, less the agreed count, less the replay of its own new operations, is a hand edit.
   - It is kept on top of the replay when the other device neither edited that count nor changed it by an operation only it logged.
   - Two equal edits of a count that no one-sided operation touched count as one edit.
   - Anything else is `hand_edit_overlap`.
6. **Receipts.** The events' holdings receipts are rewritten from the replayed seqs.

## Not settled here

These are decisions for the owner's review: which reviews may merge automatically, what a review offers, and whether a hand edit or an operation wins an overlap.

Known limits:

- A hand edit that a later operation on the same device depended on (an edit that made a removal possible) refuses on replay, because the edit is applied after the replay.
- Domain conflicts beyond 017's preconditions are not detected, for example two devices each moving a scene on.
- An undecodable file is the caller's to report before the merge.

Proved in `backend/tests/test_ledgermerge.py` and `backend/tests/test_ledgermerge_properties.py`. The second runs seeded random offline play: both side orders agree, nothing is dropped, the merged library reads clean, and a second merge, including against a lagging side, gives the same bytes.
