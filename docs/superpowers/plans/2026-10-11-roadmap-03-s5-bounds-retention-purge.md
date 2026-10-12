# Roadmap 03 S5 Bounds, Retention and Recoverable Purge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans, Native in this session. All slices, liberal commits and independent brutal-review loops are authorized by the human.

**Goal:** Bound disposable cache storage and remove deleted private text through recoverable, pinned-root in-place purges.

**Architecture:** The cache owns maintenance, leases, intents and generation checks. A marker is durable work to do; an intent fences work across the authoritative delete. Root selection has a shared leaf exclusion; routes prepare under it and purge after releasing every store exclusion.

**Tech Stack:** Existing optional SQLite, atomic writers, machine-local proclock, dataclasses, contextvars, threading and pytest. Requires reviewed S4. Original whole-spec gate is approved; this plan requires its own Codex gate.

**Spec:** [03 design](../specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md), sections3,10-13,14 and03-S5. No authoritative-store sweep or background watcher.

## Global Constraints and Constants

- All SQL remains in compiled.py. Reads/commits recheck containment, lifecycle, marker and live intents; failures are misses and never refuse a deletion.
- Current and obsolete recognized local databases purge in place with secure_delete; keep stable cache_meta schema/generation. Never unlink a database to complete a delete.
- Leases are per device AND schema. Retention may remove an abandoned cache, never this device's current schema. Guard parents and every recognized leaf, including sidecars, lease, clock, purge and intents, before acting.
- Proposed defaults: SIZE_CAP_BYTES=64MiB, LOW_WATER_BYTES=48MiB, MAINTENANCE_INTERVAL_S=3600, RETENTION_S=90days, TOUCH_INTERVAL_S=86400, VACUUM_PAGES=256. The cap bounds one device's derived text independently of library age; the quarter-cap headroom avoids repeat trimming; hourly eviction avoids write-path maintenance storms; ninety days tolerates rarely opened devices. These are structural choices, with later tuning outside committed real-store measurements.
- Size means (page_count-freelist_count)*page_size. Eviction uses bounded LRU chunks across artifacts/sources/materialized; explicit incremental_vacuum(256) follows eviction. Coordinated materialized removal affects only associations to the artifact actually evicted, preserving a newer replacement.
- Coarse touches are collected in memory, at most daily per row, flushed together; same-day hits do no database writes. Lease renewal has the same daily limit while open.
- Intent files contain only token and full owner device key. Foreign locks cannot prove a remote process dead. A failed durable intent/marker write logs once and remains a stated privacy limit.

## Interfaces and Ownership

Extend compiled.py, store.paths, world/campaign delete routes and private lifecycle preflight helpers; update frontend/src/components/StorageLocation.tsx and its owning tests. Create test_compiled_maintenance.py, test_compiled_purge.py and test_compiled_delete_routes.py; extend containment/lifecycle/guard/import tests.

```python
@dataclass
class DeleteIntent:
    token: str
    owner: str
    descriptor: int | None
    root: Path

prepare_delete(root: Path) -> DeleteIntent | None
purge_for_delete(root: Path, *, intent: DeleteIntent | None = None) -> None
paths.library_guard() -> ContextManager[None]
paths.pin_library(root: Path) -> ContextManager[Path]
```

The intent descriptor is process-local and is never serialized. prepare_delete acquires its unique token proclock without waiting, publishes through atomic, then returns; failures release the descriptor. purge_for_delete never raises, publishes a fresh marker, purges every guarded recognized local schema opportunistically, then retires only its matching intent after marker publication and releases its descriptor. Failure to publish retains the recoverable intent. intent=None supports later05 sync with a pinned root.

Every batch captures the marker and file generation when optional storage first starts, including put-only/write-time batches. Under the connection lock and before commit it rereads both plus all live intents; any change drops all staging. A pending active local intent fences reads and writes; an abandoned local intent is recovered only after NO_WAIT acquisition. A foreign intent purges this device conservatively and remains a fence; it is never retired locally. A marker mismatch purges before use; a busy purge costs a miss and retries at the next batch. Maintenance stays available with missing BUILD or GRIMOIRE_COMPILED_CACHE=0; missing SQLite still permits marker publication and logs unavailable physical purge.

Recognized obsolete schemas are probed read-only for application id plus stable metadata before any mutation. Purge only the owned user tables in a bounded schema inspection, quote verified identifiers through a narrowly defined maintenance-only helper, use a short busy timeout and secure_delete, reset generation and vacuum. An unknown/linked/malformed file is left untouched. SQL guard exceptions for this maintenance helper require planted tests and a stated reason; normal readers still require live paths/keys.

pin_library uses a ContextVar token, reset in finally; home() consults this scoped explicit root before env/pointer resolution. Preflight, authoritative deletion, lock paths and root-dependent successful cleanup run inside that context. Another process may repoint the shared bootstrap pointer, but it cannot redirect this request's paths. library_guard is a reentrant process-local leaf mutex shared by paths.set_data_dir and delete-route root capture/preparation/authoritative mutation. Reset it after fork; paths cannot import compiled. Preserve the existing run/migration/maintenance exclusions and store lock contracts. Acquire the run/maintenance exclusion before library_guard consistently with the config route; never wait for the registry while holding the root mutex. Factor a private world preflight out of delete_world's existing exists/canonical/in-use checks and a private campaign preflight out of its exists/canonical checks; the actual store operation repeats its check. Known refusal before prepare calls no cache hook. A prepared failure may make the cache cold. Purge runs in finally after releasing pin_library, library_guard, campaign locks and maintenance_excluded, always with the captured library root. Successful root-dependent cleanup (campaign inference notes included) stays pinned too; a pointer change cannot remove the replacement library's notes.

## Review Focus

1. A prepared batch computes during a delete and attempts to repopulate the purged database (Task3).
2. A process dies after removal but before publishing the marker, followed by a404 retry (Task4).
3. A foreign device's intent looks locally unlocked, or concurrent deletes finish in opposite order (Task4).
4. Root selection changes between authoritative removal and finally purge (Task5).
5. The obsolete schema retains text, or a fresh current lease accidentally renews the old schema (Tasks2/4).
6. A linked cache-owned leaf points outside the pinned library (Tasks2/4).

## Task 1: Coarse Touches, LRU and Physical Shrinking

**Files:** compiled.py; test_compiled_maintenance.py; test_compiled_guard.py.

- [x] Write RED tests for same-day warm reads doing no SQL writes, next-day artifact/source/materialized touches batched once, LRU victims older than surviving hits, and associated metadata eviction preserving a newer row.
- [x] Lower constants in temporary tests, insert bounded native payloads over the cap, prove live pages reach low water and the physical database shrinks after incremental vacuum. Test private bounded maintenance selections cannot become public inventory APIs.
- [x] Implement touch queues using existing bounded staging, opportunistic interval checks on writes, bounded LRU chunks and static bounded vacuum. Parsing remains outside SQL. Source validation alone cannot induce one commit per row.
- [x] Run maintenance/derive/source/materialized/core/guard tests and configured ruff; commit a passing checkpoint.

## Task 2: Device-and-Schema Leases and Retention

**Files:** compiled.py; test_compiled_maintenance.py; lifecycle/containment tests.

- [x] Write RED tests for lease renewal on open and daily while held, no same-day rewrites, daily warm cache with old database mtime surviving another device's sweep, no-lease fallback, retired devices and local obsolete schemas aging out, and current schema never removed.
- [x] Plant symlink/reparse parents and leaves for database/journal/lease/clock/marker/intents. Require no action through them and unchanged external targets. Unrecognized files stay untouched.
- [x] Implement guarded recognized-file retention, including sidecars and lease, respecting the current-device/schema exemption and independent obsolete leases. No content/library reachability walk occurs.
- [x] Run maintenance/lifecycle/containment/import tests and ruff; commit.

## Task 3: Marker Generation and Whole-Batch Write Fence

**Files:** compiled.py; test_compiled_purge.py.

- [x] Write RED tests for a second independent SQLite connection, marker mismatch before a hit, prepared artifact/source/materialized/touch rows discarded after marker movement, and put-only batches capturing a generation before staging.
- [x] Test a marker arriving during decode and commit, a pending intent appearing after batch start, locked purge retried on the next batch, cache disabled or BUILD unavailable, and marker I/O failure without caller exceptions. A delete must never wait on a cache lock.
- [x] Implement guarded marker I/O through atomic, stable file generation capture, secure in-place purge and commit rechecks under the same lock/transaction. Recognized obsolete-schema purge follows the stable ownership protocol; no current or obsolete database unlink.
- [x] Run purge/lifecycle/ownership/core/materialized tests and ruff; commit.

## Task 4: Recoverable Intents and Obsolete Schemas

**Files:** compiled.py; test_compiled_purge.py; guard tests.

- [x] Write RED crash injection after authoritative removal but before marker publication; a fresh process/batch recovers the local unlocked intent even after a404 retry. Active local owners remain fenced. Two unique intents cannot retire each other, including reverse completion order.
- [x] Plant a foreign intent and prove it is never claimed dead by a local lock; this device purges and stays fenced. Publish failure retains recovery work. Test actual current and obsolete owned schemas, unknown application ids and malformed metadata.
- [x] Implement prepare_delete/purge_for_delete and abandonment recovery, with separate full owner key and filename prefix identities. Close/fork/root-change must release only process-owned resources without erasing durable recovery work.
- [x] Extend the AST guard narrowly for private bounded maintenance SQL; test arbitrary dynamic SQL/public table enumeration still fails. Run purge/proclock/ownership/import tests under both dependency sets; commit.

## Task 5: Pinned Delete Routes and Sharing Copy

**Files:** store/paths.py; worlds/lifecycle.py; campaigns/lifecycle.py; routes/worlds.py; routes/campaigns.py; StorageLocation.tsx; test_compiled_delete_routes.py; existing data-dir/delete/frontend tests.

- [x] Write RED thread/barrier tests showing cooperative root selection cannot change during capture/prepare/removal, can change before finally purge, and purge still names only the old library. Add a real two-process regression: both libraries contain the same placeholder ID; A captures/prepares L1, B repoints the shared test pointer to L2, A deletes and purges L1 only. Assert L2 record/cache/notes remain unchanged. Test ContextVar isolation, nested pins and exception reset. Assert SQL purge occurs outside all exclusions. Existing404/in-use refusal prepares nothing; a failed prepared mutation still completes the cache attempt.
- [x] Implement the shared leaf root guard, scoped ContextVar root binding and private preflights, preserve existing comments/refusals/subject cleanup, and place completion in finally with pinned root. Exercise both world and campaign routes.
- [x] Add plain sharing copy beside Storage location: derived text and thumbnails/embeddings share the selected folder; deletes purge this device now and other devices on next open; dormant files survive until retention; text-keyed vectors remain until the embeddings folder is cleared. Avoid implementation details that do not help the user's choice.
- [x] Run delete/data-dir/lock/import/docs tests in both environments, owning frontend tests/typecheck/ESLint and ruff; commit.

## Task 6: Slice Gates

- [ ] Run focused all-cache/delete/ownership/containment/API tests, ratcheted lint/Linux mypy counters and frozen fixture diff. Full fresh-tip backend/frontend/APK gate follows S6; no claimed green Windows gate without evidence.
- [ ] Complete independent brutal-review/fix/re-review until non-blocking, mandatory native Codex review and original-spec fidelity gate. Mark03-C8 and S5 only after all tests/reviews cover their full contracts; commit evidence and the theory note.

## Self-review and Authorization

Each Review Focus maps to failing tests and an owning task. Fail-soft marker publication never silently claims physical purge succeeded; durable recovery and the settings residuals make that limit visible. Native/all-slices authorization is sufficient to execute once the mandatory plan gate passes; no additional human handoff question.


Codex plan gate01a12d64-d3d8-7b30-9452-20689e2303aa found the process-local guard alone could not bind paths against another backend changing the shared bootstrap pointer. The plan now pins every authoritative resolver through a scoped ContextVar and tests the real two-process switch, preserving L2 records and cache while deleting/purging L1. The mutex handles cooperative local ordering; the scoped resolver establishes path identity independently of exclusion. Follow-up plan gate required.


Task1 checkpoint: four maintenance cases RED, three exact-private-SQL guard cases RED. Daily artifact/source/history staging now uses bounded transferred timestamps; warm derive refreshes history without rewriting its artifact. Indexed eviction deletes only the association of an explicit artifact victim. Bounded128-row selections and incremental_vacuum(256) are private, exact and reasoned guard exceptions; public enumeration and changed limits remain rejected. Ruling: reduced test low-water values must exceed SQLite's measured45,056-byte schema floor;60/52KiB exercise eviction while preserving surviving rows. Initial broad run405 passed3 skipped with one old7-field decoder seam failure; decoder now accepts the timestamp-extended transfer. Corrected focused maintenance/source/history/guard verification recorded in build/s5-maintenance-final.log. Task2 tests are isolated in test_compiled_retention.py to keep the maintenance scenarios readable; seven observed RED cases, implementation next.

Task1 verification303 passed2 platform skips. The post-test Ruff result had one SIM102 in the new guard; collapsed the nested condition and verified it immediately in the next checkpoint.


Task2 checkpoint: seven observed lease/retention/containment RED cases now pass. Leases renew once daily on open and while held, obsolete versions retain independent leases, unknown files remain untouched, and all control leaves use lstat containment. Windows fixture SQLite connections require explicit close before testing unlink; context-manager commit alone leaves a handle open. Ruling: original-spec `delete-intents/` is the precise directory name. New S4 fidelity01a12e21-78bb-7363-8382-7a157f626d88 found an ordinary imported static-method wrapper missing from the decision index: one RED plus one safe control; qualified class-method indexing now passes all187 planted guard tests without inheriting class-body globals. Mandatory follow-up still required. Task3/4 purge tests14 RED at missing interfaces; implement the shared durable fence next.


Tasks3-4 shared checkpoint:14 initial purge/intent cases RED, now pass, plus all-family marker-drop coverage. First optional access captures marker and SQL generation, including put-only batches; every SQL transfer and commit compares captured generation before synchronization and under its transaction. A normalized mismatched file generation cannot revive stale staging (one intermediate failing regression corrected). Durable unique full-owner intents fence work; local abandoned owners recover via NO_WAIT, foreign owners are never retired, reverse completions retire only their own token, and publication failure retains durable work. Current/obsolete owned files purge in place with secure_delete and VACUUM before recording completion generation; busy erasure retries next batch. Disabled BUILD/cache/SQLite still publishes the marker. Bounded129-name schema inspection and verified quoted deletion have two observed RED guard cases, now allowed only by exact private reasoned shapes. Broad447 passed7 skipped had one prior corruption-recovery seam failure: initial generation-read errors now remain retryable so the same batch may store after recovery. Corrected purge/core/guard280 passed1 skipped, Ruff clean. Pinned routes and real crash-process coverage remain next.


Task5 and independent-review correction checkpoint: seven pinned-route cases RED plus two known404 controls; scoped nested/thread pins, cooperative local switch blocking, real second-process pointer switches for both delete routes, prepared-failure finally and old-root cleanup now pass. Actual child death after authoritative world removal leaves a durable intent; a404 retry invokes no hook, and the next cache batch recovers the orphan and erases text. Registry cleanup occurs after releasing the leaf mutex; successful root-dependent campaign notes remain pinned. Storage copy explains derived-text sharing, next-open purges,90-day dormant retention and retained text-keyed embeddings. Owning ConfigView61 tests passed, TypeScript passed. Independent review of7d9ab979 was blocking on busy-first-read resurrection and busy obsolete-schema retry, and identified partial-LRU hour suppression and a legal sqliteX_private table skipped by LIKE. Exact local regressions observed RED for all four. First-attempt marker is retained across retry; all local schema generations retry without rewriting completed files; GLOB treats underscore literally; bounded trimming remains immediately eligible until low water. The large synthetic regression initially violated private session locking and crashed the Windows SQLite driver at idle-close; corrected fixture uses the session mutex and one bulk transaction, then produced the expected eviction RED. Corrected maintenance/purge/pinned/crash34 passed; prior broader304 passed1 skip. New cache/root Linux mypy clean; whole Ruff1146 exact unchanged baseline after correcting two route import sorts. Architecture guards and full dual-environment S5 verification/reviews pending.


Second independent loop:99e0df38f separately commits the GLOB guard/fixture correction inadvertently left out of190's staging list (three targeted guard checks passed). Complete native and Py1 S5-focused suites619 passed10 platform skips each; architecture973 passed2 skips. Reviewer then reproduced response middleware stamping replacement-library revision on both successful and failing deletes: two actual-HTTP RED cases, fixed by carrying captured root in ASGI scope and pinning response/error bookkeeping; delete/revision95 passed. Foreign-intent retirement before marker delivery also reproduced RED; any pending intent observed at first access is now a sticky batch fence. Final purge/routes32 passed, Ruff1146 unchanged after removing unused imports in pending S6 tests. Mandatory code/fidelity review additionally raised pre-read False-policy fence, session closure versus active-intent ownership, check-to-commit ordering, disabled pending-maintenance recovery and class namespace aliases; these remain open for targeted reproductions/fixes. S5 is not complete.


Mandatory review corrections: native01a12e4a-aa1e-7bf3-bfa5-9c15a25c2eea and fidelity build/s5-fidelity.log produced nine observed RED cases with two safe class-alias controls. Source validation/collections now establish a fence before bytes regardless of policy. Intent ownership belongs to the active DeleteIntent request, independent of cache connection lifetime; unrelated root reopen/compiled.close cannot announce owner death. Fork children close inherited FD references without unlocking the parent. Ruling: the plan's process-resource cleanup requirement does not permit cache-session closure to abandon a live authoritative request; tests explicitly abandon mocked owners, and actual crash recovery remains covered. SQL commit now immediately rechecks publication and erases moved snapshots before releasing its operation exclusion. Existing durable erasure maintenance runs independently of the kill switch/BUILD, including empty batches, while still deferring async/campaign-held callers. Class namespaces expose ordinary callable aliases through the existing bounded export resolver. Corrected affected316 passed1 skip, Ruff clean. Independent idle-close trim loss reproduced RED and fixed by a durable tiny cache_meta trimming flag; final affected322 passed1 skip, whole Ruff1146 exact baseline. Follow-up independent/native/fidelity gates required; S5 remains open.


Independent ba306 follow-up: campaign-held SQL opt-out must remember the first durable delete boundary even before SQLite becomes available; observed RED now passes. An unchanged foreign intent must retain its completed generation rather than alternating with the local marker; observed four secure purges reduced to one. Empty validation performs no storage work. Cache/support/source/purge verification:199 passed3 platform skips; focused Ruff clean. Imported-class namespace mutation guard follow-up remains under verification, and completion gates remain open.

Mandatory follow-up native01a12e7f-8a69-75e2-9a88-91008a1ec44a and fidelity (exact thread retained in build/s5-followup-fidelity.log) found writer pre-SHA fencing, device/owner publication ordering, and fork descriptor registration. Each observed RED; corrected writer entry fence, owner-first publication and intent-resource mutex coordinated with fork/retirement. Windows real restored-mtime experiments required native ChangeTime rather than Python birth-time ctime; unsupported/remote/unknown Windows filesystem metadata opts out.211 passed2 skips for cache/source/purge/consumers; previous broader lifecycle223 passed6 skips. Focused core/token Linux mypy and Ruff clean. Mandatory re-review remains open; no slice completion claimed.


Final review correction (2026-10-12): a genuinely corrupt owned payload table
cannot be securely cleared with SQL. Corruption is now propagated to purge's
owned-file handler rather than swallowed as busy. After ownership/integrity
rechecks under the same NO_WAIT exclusion, and only without a hot journal,
the disposable damaged database is erased. cache0 creates no replacement;
failed erasure retains durable maintenance. Two observed RED cases now pass
in enabled/cache0 modes; healthy in-place/second-connection regressions remain
passing. The originating spec states this bounded exception explicitly.

Implementation closure: all Tasks1?5 contracts delivered. Final whole-spec native/fidelity followups remain mandatory; no green full Windows gate is claimed. Fresh owning/all-cache matrix917native/911Py1 passed,6/12platformskips and one explicitly base-failing clock test deselected. Subsequent narrow Windows filename-case guard348passed5skip: ordinary reads, maintenance ownership and NO_WAIT lock identity now all use host normcase. Independent original erasure/fence repros proved sixRED?GREEN and375passed1skip; lastcase-only finding re-review pending. Canonical main/aside ownership is independently proved; unknown rawNOTADB never licenses destruction. Reviewed finalgate IDs and completion evidence follow below.
