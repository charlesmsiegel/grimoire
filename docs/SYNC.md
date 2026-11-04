# Device sync: revisions and conflicts

**Status:** first slice of [Sync Revisions And Conflict Core](roadmap/sprints/sync-revisions-and-conflict-core.md) (Wave 3, #019), implemented as `grimoire.sync` over the [native store](STORAGE.md). It synchronizes **two libraries that are both open locally**: a desktop folder and a phone folder, or any two copies of one library. There is no network transport, pairing or UI yet; transport belongs to [App-Managed Device Sync (028)](roadmap/sprints/app-managed-device-sync.md). A green test suite proves this contract, not a working phone sync.

## What it guarantees

- **Timestamps never decide.** Sync compares bytes (etags). Nothing in `grimoire.sync` reads a clock, a modification time or randomness; a test pins this.
- **Non-overlapping edits merge deterministically.** `sync(a, b)` and `sync(b, a)` produce byte-identical libraries, the sync bookkeeping included.
- **Nothing is silently overwritten, resurrected or dropped.** Overlapping edits, deletions, records the other library cannot accept, and anything without a provable common ancestor become **named conflicts**. Each device keeps its own file and also receives the other device's version under its `.conflicts/`.
- **Crash-safe.** Every write is a 010 transaction. After a crash, reopening both libraries and syncing again reaches exactly the state an uninterrupted sync reaches.
- **One bad record never blocks the rest.** A path or source the other library cannot apply, a write the policy denies, or an unusable file becomes a conflict, and everything else still syncs.

## What is synchronized

`grimoire.sync.syncable(path)` is the single definition of the synced set, and the extension point for later families (018 images and blobs, 021 accepted events and wrap-ups, 035 scenes, ledgers). In this slice it covers the canonical records of 010 and of [107](CHATS.md): source manifests, definition revisions, campaign instances, 107's play-scope (`campaigns/<id>/scope.json`) and chat (`campaigns/<id>/chats/<chat_id>.json`) records, [017](GRAPH.md)'s relation records (`campaigns/<id>/relations/<relation_id>.json`), [021](EFFECTS.md)'s accepted events and scene wrap-ups (`campaigns/<id>/accepted/`, `campaigns/<id>/wrapups/`; an effect's relation changes are therefore delivered together with the event that made them, in one data commit), and [020](EVIDENCE.md)'s create-only knowledge records (events, evidence and knowledge-log batches under `campaigns/<id>/events/` and `campaigns/<id>/evidence/`). Instances, scopes, chats, relations, accepted events, wrap-ups and knowledge records are campaign-local: each is its own admission unit, never grouped with a source. S3 is proven both on a synthetic instance-shaped chat and on real 107 chats made by `grimoire.chatstore`:
- a lorebook attached on one device and a world attached on the other touch different files, so both merge;
- concurrent edits of one chat record are a keep-both conflict, and the new participant's actor still travels;
- after either, each device's navigation index still finds exactly one chat.

Never transferred:
- device-local state: `.txn/`, `.conflicts/` and `.sync/`;
- **derived indexes**, such as 107's navigation index. It is rebuilt from canonical records on each device, never synchronized as a second owner.

Reported as `skipped` and never touched:
- ledgers under `authority/`;
- the library marker;
- unknown root entries such as `worlds/`;
- [018](IMAGES.md)'s image objects and placements (`images/` and `placements/` under a source's `_source/` or a campaign) and its blobs under `assets/`: they are not synced yet, and they never pin a definition revision (placements attach to the entity), so they never block `resolve`;
- the reserved campaign family `scenes/`, and any file under `events/` or `evidence/` that is not a knowledge record of a known family;
- non-record files.

Sync compares whole files. It never interprets payloads, never merges inside a file, and never treats source inheritance (`derived_from`, `base`, portrayals) as device ancestry. Three structural readings are the only exceptions:
- a manifest's `heads`, for the head-dependency rule;
- on a true first sync only, a record's `previous` field, for one-hop lineage;
- when `resolve` replaces a definition revision, the store's reference check reads every manifest's heads and links, every instance's `derived_from`, `base` and pinned payload references, other revisions' pinned payload references, and every string in scope, chat, relation and knowledge payloads (a pinned reference, or an unpinned one beside a `resolved_revision`).

## Device identity

`identify(store, device_id)` writes `.sync/device.json`. Device IDs are supplied by the caller and never generated. An ID is one lowercase identity segment that is also a portable path segment.

Refusals:
- a library with no identity, or a damaged one: `NoDeviceIdentity`;
- two libraries with the same ID (the same library twice, or a folder copied together with its `.sync`): `SameDevice`;
- changing a valid ID without `replace=True`: `IdentityExists`.

A damaged identity file is replaced by `identify`. When a device's valid ID changes, the same commit **retires** each of its peer archives and markers: the file stays present but is rewritten as `grimoire.sync.retired/1`, which records only the paths it named and is never valid again. The next sync of the new ID is therefore conservative rather than a first sync, the retired paths stay uncertain (a tombstone is not forgotten), and renaming the devices back can never revive an outdated base. If **both** devices take new IDs, neither has any file for the new pair, so the next sync is a true first sync (see Limits).

## Bookkeeping (`.sync/peers/<peer>/`)

- **`archive.json`** holds the last agreed state per path. `{"agreed": E}` means both devices verifiably held exactly E. `{"deleted": E}` is a **tombstone**: both devices were absent after last agreeing on E. Tombstones are kept; there is no compaction yet.
- **`pending.json`** records what an unfinished sync published on this device: `{"from": E0, "to": E1}` per path, or `{"uncertain": true}`. It is written in the **same transaction** as the data, and that data commit happens even when nothing is copied if the marker changes (for example, to record uncertainty), so a crash before the pending step can never leave rewritten archives without it. An entry is removed only after both archives are written and its path is resolved. A path whose base was `uncertain` and that did not end agreed, or that ended absent on both devices with no known tombstone, keeps (or gains) `{"uncertain": true}`, so the uncertainty survives the sync that saw it: otherwise the next sync would see a `none` base, and a one-sided deletion would be undone by a create-only copy.

Both files carry a `check` digest of their own content. A file that is not strictly valid is **ignored and reported** (`SyncReport.notices`), never partially trusted. This covers bad JSON, a wrong format, an archive for another pair of devices, and a failed `check`.

## Base derivation

A `.sync` file that exists but cannot be read (an I/O error, a file held by another program) stops the sync with `SyncInterrupted` before anything is written. An unreadable marker must never look like a missing one.

A path's base is trusted only where **both devices' own records agree**:
1. **Archive candidate:**
   - `first`: neither device has any file at its archive location for this pair (a true first sync);
   - the shared entry, when both archives are valid and hold the identical entry;
   - `none`: both archives are valid and neither has an entry (a path new since the last sync);
   - otherwise `uncertain`: one archive is missing or ignored, or the entries disagree.
2. **Pending override.** If either device has a pending entry for the path, the base is `agreed to` only when every entry names the same `to` and each device holding one still has those bytes: the copy verifiably landed, and the peer held `to`. If both archives already agree on that `to`, the marker is stale (a crash after the archives were written but before the marker was cleared) and the archive base stands: archives are only written after verification. Anything else is `uncertain`: a revert to the old bytes, an edit racing the copy, disagreeing entries, or a marker that parses but is invalid (ignored).

`uncertain` never copies anything. This is what defeats:
- a **clone** of a library that synced with the phone: its archive cannot make one device's version look like an ancestor;
- **ABA** after a crash: the user restores the exact old bytes before the sync finished.

## Decision table

Each device's file is `absent`, `present(etag)`, or `blocked`. A file is blocked when it is:
- a link;
- a look-alike spelling;
- unreadable, oversized or not a regular file (a directory where a record belongs counts too);
- not strict JSON;
- in a newer format;
- an envelope whose address does not match its path.

The rows are evaluated top to bottom, and swapping the devices mirrors every decision.

| # | Base | Devices | Decision |
|---|---|---|---|
| 1 | any | either blocked | conflict `blocked` |
| 2 | any | equal etags, or both absent | agreed |
| 3 | `uncertain` | differ | conflict `missing_ancestor` |
| 4 | `first` | one absent | copy (create-only) |
| 5 | `first` | both present | copy if exactly one side's `previous` is the other's etag (not for immutable records: revisions and knowledge), else `missing_ancestor` |
| 6 | `none` | one absent | copy (create-only) |
| 7 | `none` | both present | conflict `missing_ancestor` |
| 8 | `agreed E` | one is E, the other present | copy the other over E; an immutable record (revision or knowledge) is `revision_modified` |
| 9 | `agreed E` | one is E, the other absent | conflict `deleted` (deletion is not propagated) |
| 10 | `agreed E` | one absent, the other not E | conflict `edit_delete` |
| 11 | `agreed E` | both present, neither E | conflict `concurrent` |
| 12 | `deleted E` | one absent, the other is E | conflict `resurrected` |
| 13 | `deleted E` | one absent, the other not E | copy (a new record at a deleted path) |
| 14 | `deleted E` | both present, different | conflict `concurrent` |

A copy publishes the other device's **exact bytes** (`Transaction.put_exact`), so both devices converge on one etag. Those bytes still exist on the other device, so an aborted commit drops the copy rather than parking it under `.conflicts/` (journal kind `replica`). Before it is admitted, three overrides apply:
- **Head dependency.** A manifest copies only if every head it names will hold the origin's bytes on the destination: either already equal, or copied in the same commit. Otherwise the conflict is `head_dependency`.
- **Name collision.** A destination path that collides with a differently spelled entry gives `name_collision`.
- **Destination preflight.** The destination's writes are checked once with 010's read-only `Transaction.check()`. Only if that check fails are they admitted unit by unit, and a unit the store refuses becomes `unapplicable`. A unit is one instance, one manifest with its head revisions, or the other revisions of one entity. Instances are checked alone; source units are checked cumulatively, so cross-source link cycles are caught. In that fallback, one final combined check of everything admitted runs before the commit, defensively: by construction it should accept what the units accepted, and if it does not the error propagates. Library-wide problems propagate and are never blamed on one path.

**Denied writes.** Authorization cannot be dry-run without consuming approvals, so a data commit uses 010's partial mode (`commit(..., on_denied=...)`). Each write is authorized exactly once. A write denied for its path (not granted, approval required, reserved) is dropped together with every copy of the same source, becomes conflict `denied`, and its pending entry is removed. What remains is checked again. If it is no longer acceptable on its own (for example, another source's manifest needed the denied one), it is re-admitted unit by unit, and each refused unit is also `denied`, so a denial never stalls the rest. A denial that is not about a path propagates: an unissued handle, or an unavailable authority. Approvals consumed by dropped dependents are reported in `SyncReport.consumed_approvals`.

## Keep-both

For every conflict except `denied`, each device whose peer has a present, usable version receives that version as a **kept version**. It goes under `.conflicts/<hash16>/<name>~<digest12>[.N]`, through `Transaction.keep_version`. The authorization address is distinct from the record's own: `kept/<hash16>/<digest12>[-N]/<name>`. So "keep these bytes beside p" and "overwrite p" are different approvals, and they appear under different `preview()` keys.

A slot already holding those bytes is reused, so kept versions are never duplicated, and a conflict stays reported on every sync until a human ends it. A kept version that cannot be written (for example, because of path limits) is reported as `kept: false`. A device that has moved on from the base is never handed that base as a kept copy: it is not the peer's version. A device whose own file could not even be read receives nothing: there is nothing to keep it beside, and the next readable sync decides the path. A `denied` conflict keeps nothing either: the denial is only known while committing, after kept versions were planned. Both versions stay canonical on their own device, and the conflict is reported on every sync until the grant changes. Kept versions are not indexed: `load()` still shows one record per path.

Dismissing a kept version by deleting it is not supported yet: the next sync delivers it again. While a conflict stays open and one device keeps editing the record, every new version it sends lands in a new slot, so kept versions accumulate (one per sync, up to the record's size each) until compaction exists.

`resolve` marks its copy in the receiver's pending marker in the same transaction, as a sync does, so an interrupted resolve followed by a revert is a conflict, never an overwrite. It refuses while either device's marker for the pair is ignored.

Kept copies of the **other device's** version are regenerable (journal kind `keep`): an aborted commit drops them, because the other device still holds the bytes. The version `resolve` replaces is the receiver's own and may exist nowhere else (`hold`): an aborted commit preserves it like any other version.

## Ending a conflict

- `resolve(left, right, path, keep=<etag>, expected={device: etag | None}, handles, approvals=None)` makes the device that does not hold `keep` receive it. Its replaced version is kept under `.conflicts/` in the same transaction, and both archives then record `agreed keep`.
  - **Manifest:** absent head revisions are copied with it. A head revision that differs must be resolved first. When a differing head revision has the same ID on both devices and the manifests differ too, neither can be resolved first: each device's manifest pins its own revision. Add a new revision on one device and move its head there, then resolve the old revision and the manifest.
  - **Definition revision:** allowed only if nothing on the receiving device can pin it: a manifest head, a link endpoint, an instance's `derived_from`, `base` or a pinned reference in its payload, a pinned reference in another revision's payload, or any text in a scope, chat, relation or knowledge record that names it (a chat's source bindings). Every parsable record is checked, those of excluded sources included. A manifest byte-identical to the receiving device's copy of the winner's manifest is exempt: it is the winner's own manifest, which names the revision being replaced by ID. A blocked path that could be or hold a record means "cannot prove unreferenced" (`RevisionReferenced`), so it blocks revision resolves until it is fixed; a blocked entry that cannot hold records (a root-level link, a non-JSON note under `sources/`) does not.
  - **Knowledge records** cannot be resolved (`ResolveRefused`): they are immutable evidence, and choosing one version would rewrite history. Both versions stay, and the conflict stays reported.
  - **Refused (`ResolveRefused`, nothing written):** a deletion choice, a blocked side, `keep` matching neither device, either device differing from `expected`, or an archive for the pair that is missing or ignored on either device. Rewriting such an archive would forget why every other path is uncertain, so the devices must complete one sync first; that sync records the uncertainty in the pending markers.
- Deleting a record on both devices by hand ends a `deleted` conflict, and a tombstone is archived.

## Apply order and crash safety

`plan_sync` only reads (directory listings are cached for the whole plan). `SyncPlan.preview()` gives, per device, the exact bytes of every authorized write.

`apply_sync(plan, handles, approvals=None)` writes in a fixed order, where `a` is the lower device ID:
1. data(a): copies, kept versions and the merged pending marker, in one transaction;
2. data(b);
3. verify: every published and kept file is re-read and must hold the planned bytes;
4. archive(b), then archive(a);
5. pending(b), then pending(a).

An archive entry `agreed E` is written only after both data commits and verification succeeded, and only for paths both devices verifiably held as E. A first sync with nothing agreed still writes an empty archive.

Errors:
- These stop the sync with `SyncInterrupted`, and no archive is written after the stop:
  - a stale write (a library changed after planning);
  - `WriteConflict` (an external edit raced publication; 010 kept the human's file);
  - a per-path refusal at commit time;
  - a failed verification.
- `CommitIncomplete`, `OSError`, `PendingRecovery`, `RecoveryBlocked` and root or V1 errors propagate; 010 recovery owns them.

`sync_until_quiet` retries `SyncInterrupted` until a sync completes and writes nothing. Its report merges every attempt: all copies, notices and consumed approvals, and the conflicts of the final sync. After `attempts` it raises the last error, or `SyncNotQuiet`, carrying the merged report.

Planning takes the two libraries' locks in a fixed order (by root path), so two syncs of the same pair in opposite argument order cannot deadlock (pinned by a test running both orders concurrently).

## Limits

- One archive and one pending marker per pair, each under 010's 8 MiB file cap (tens of thousands of entries). A plan whose archive or marker could exceed it is refused before any write (`ArchiveTooLarge`). Compaction is a later milestone and a release blocker for large libraries.
- Not in this slice:
  - more than two devices, transport and pairing;
  - causal branches, and history beyond one agreed etag per path;
  - deletion propagation, and resolving toward a deletion;
  - ledger sync;
  - semantic or field-level merge (042);
  - dependency checks beyond manifest heads;
  - dismissing kept versions.
- A true first sync, or one after `.sync/` was deleted or both devices were re-identified, re-creates records deleted on one device beforehand. After a tombstone, resurrection is detected only for the exact tombstoned bytes.
- **Independently created actors can merge.** 107 names a new actor `<entity>-<n>` by the first free number in its campaign, and the actor record holds only identity and source. Two devices that, offline, each add the same character to chats of one campaign create byte-identical `…-1` records, which sync treats as agreed: both chats then share one actor. Making actor IDs unique per device belongs to 107; until then this is a known limit for the chat flows that create actors offline on two devices.
- **Knowledge logs are not merged yet.** Knowledge records are immutable like definition revisions: a new one is copied create-only, a changed one is `revision_modified`, and `resolve` refuses them. Two devices that each append the same knowledge-log batch number get a keep-both `missing_ancestor` conflict on that batch; merging two devices' logs into one is a later 019 milestone.
- **A restore after a finished sync is an edit.** Pending markers protect a restore of the old bytes made before the archives record the copy: the holder of a marker no longer holds its `to`, so the path is a `missing_ancestor` conflict that keeps both versions (even when the copy was already verified). Once the archives are written, a restore is an ordinary later edit and is synchronized as one.
- **Denials are coarse.** A denied write drops every copy of its source to that device (instances are independent), not only the writes that strictly depend on it.
- **Single-device trust in markers.** A pending marker is trusted on one device's word, and the `check` digest detects damage, not forgery. `.sync` writes need no handle; keeping them out of workers' reach is the application's job until 007/028 integrate.
- **Link-drop policy.** A copied manifest is admitted exactly as a local commit of it would be. Under 010's accepted link-order policy, it can cause *another* source's cross-source link to be dropped on the destination, and the next `load` reports it.
- 010's R7 window remains: an external edit landing exactly between a target's guard read and its `replace` is lost. 019 narrowed it for a file being created: its directories are now made before that guard read, so only the publish step's own path re-check (a stat, no read of the file) and the `replace` remain in the window.
- **Rollback.** A pre-019 build cannot recover an unfinished journal that contains `replica`, `keep`, `hold` or `state` entries (`RecoveryFailed`, unknown kind). Finish recovery with a 019 build, or use `discard_journal`.

## Proofs

The scenarios live in:
- `backend/tests/test_sync_scenarios.py` (S1–S8 and the failure scenarios; F5 is covered by the S7 tests and F11 by an S8 test; the harness additions, F8, are in `test_builtin_invariants.py`, `test_fault_harness.py` and `test_store_replica.py`);
- `backend/tests/test_sync_decide.py` (every combination of the table, mirror symmetry, the safety property, base derivation and the documents);
- `backend/tests/test_sync_faults.py`: two replica roots under the 006 harness, covering crashes, I/O errors, recovery crashes, interleaves at every observation gap, an ABA restore and a meddle. A second fixture (S9u) makes every base uncertain through the archives alone (one device re-identified, one deletion) and crashes or fails every commit boundary: no point re-creates the deletion. Its docstring reasons the commit-outcome and observation-gap reductions that keep it near a minute on Linux. The unreduced explorations exist as opt-in tests (`GRIMOIRE_EXHAUSTIVE_SYNC=1`): every mutating operation with crashes, torn writes and errors, about 220 points and two minutes on Linux, ran with no violation. Read faults (an unreadable marker, record or verification read) are covered by monkeypatched scenarios, because the harness faults only mutating operations.
