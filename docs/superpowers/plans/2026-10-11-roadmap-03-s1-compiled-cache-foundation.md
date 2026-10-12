# Compiled Cache S1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the disposable, content-keyed artifact cache and its failure boundaries on desktop and Android, without wiring production consumers.

**Architecture:** Keep SQLite ownership in `store/compiled.py`, with a small standard-library-only build fingerprint helper outside `store/`. Callers supply registered kinds and content keys; short locked database operations never encompass parsing. A process-owned connection closes on idle, root change and shutdown, and every cache failure yields a miss or drops a write.

**Tech Stack:** Python 3.11+, standard-library sqlite3/hashlib/json/importlib.metadata, pytest, existing store atomic/proclock/logs helpers, Chaquopy 3.12 and Gradle Kotlin DSL.

**Spec:** [Spec 03](../specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md), specifically **03-S1**. CLI spec gate approved 2026-10-11 in thread `01a12ba7-f81b-75d1-a2e6-12692e73bdfc`.

**Status:** Human approved S1 and **Native** execution on 2026-10-11. Codex plan gate **approved** on 2026-10-11, thread `01a12be2-f9da-7cc1-91ff-9e4e7da00a7f`; no material findings. Earlier findings are resolved by the owner-provided fork resets, transition fence, batch-entry lifecycle identity, initialization before pointer publication, and bounded artifact allocation described below.

## Global Constraints

- `<home>/.cache/compiled/<device>-v<SCHEMA>.sqlite`; device is the first 32 hex characters of `maintenance_reports.device_key(root)`, memoized per process and root.
- `sqlite3` has one runtime importer, `store/compiled.py`; no new base dependency.
- `DELETE` journal mode; `check_same_thread=False`; a read never waits for the connection lock.
- Every cache read takes caller-supplied keys; files answer membership, existence and counts.
- Checksums cover keys and values. Kind `version`, `BUILD`, ordered role/hash inputs and canonical JSON params enter the artifact key.
- `GRIMOIRE_COMPILED_CACHE=0` bypasses derived lookups and writes. Missing SQLite or an unavailable APK stamp disables the cache.
- Cache I/O runs outside campaign locks and off event-loop threads. There is no `OUTSIDE_DOMAIN` entry for a module with no cid-taking mutator.
- Bytecode and `__pycache__` are excluded from `BUILD`; vectors remain independent of `BUILD`, with the existing NUL-stripped text key unchanged.
- Reject linked/reparse cache directories and owned leaves before opening or mutating them. Log degradation once per failure kind, without paths or payloads.
- Follow `CLAUDE.md`, the zero-baseline import/path/atomic guards and the privacy placeholder set. Never regenerate the frozen campaign home.

## Review Focus

1. APK metadata and build-machine Python differ from runtime Python: the source stamp is portable, while runtime/dependency changes still invalidate keys (Task 1).
2. Ordinary parent directories contain a linked database or hot-journal leaf: no external target is touched (Task 2).
3. Cached JSON is null, false, zero, empty, mutable or valid-but-corrupted JSON: hits remain distinguishable from misses, and checksums/copies protect answers (Task 3).
4. A locked database raises a DatabaseError subclass: it is never renamed as corrupt (Task 4).
5. A root change or fork occurs while an artifact is being prepared: old handles and pending writes cannot cross the lifecycle boundary (Task 5).

## Scope and sequence

This is one reviewable slice, delivering 03-C7 and the single-key portions of C2/C6. S2 owns source stamps, filesystem clock validation, raw-byte reads and decision-kind enforcement. S3 owns collections/batch hit counts. S4 owns materialized rows and write-time source tracking. S5 owns leases, retention, deletion intents and purge. S6 owns consumer wiring, the ctime insertion fix, the generator and performance measurements. None is silently considered delivered by S1.

Read the touched files and their changes since `7f833e7` before edits. The relevant landed main/embedding lifecycle changes through `f816921c8` must remain intact. Commit after each coherent passing checkpoint, including tests, docs and review corrections; do not wait for a task or slice boundary.

## File map

| File | Responsibility |
|---|---|
| `backend/src/grimoire/compiled_build.py` (new) | Pure source-manifest and runtime/dependency fingerprinting; no store or SQLite imports |
| `backend/scripts/compiled_build.py` (new) | Build-time CLI with explicit package/templates/output paths |
| `backend/src/grimoire/store/compiled.py` (new) | Registry, keys, artifact SQL, containment, connection ownership and failure handling |
| `android/app/build.gradle.kts` | Generate and stage the source stamp before assets are packaged |
| `android/app/src/main/python/android_entry.py` | Resolve the extracted stamp before backend imports |
| `backend/src/grimoire/routes/config.py` | Close the compiled connection before changing the storage root |
| `backend/src/grimoire/main.py` | Close compiled resources on a worker during lifespan teardown |
| `backend/tests/conftest.py` | Session/per-test machine-lock privacy floors and compiled teardown |
| `backend/tests/test_compiled_build.py` (new) | Manifest, runtime versions, stamp fallback and build CLI |
| `backend/tests/test_compiled.py` (new) | Artifacts, schema, failures, locks and containment |
| `backend/tests/test_compiled_lifecycle.py` (new) | Threads, idle, root switching, shutdown and fork |
| `backend/tests/test_compiled_guard.py` (new) | One SQLite owner and key-bounded read APIs, with planted failures |
| `backend/tests/test_android_entry.py`, `test_vectors.py`, `test_data_dir.py` | Existing platform/vector/root contracts extended narrowly |
| `CONTRIBUTING.md`, `ROADMAP-CHECKLIST.md` | Document the new guard and accurate gate/delivery status |

## Test commands

Run from this worktree. `$py` below is the task-local interpreter:

```powershell
$py = 'C:/Users/charl/github/grimoire/.worktrees/spec-03-compiled-cache/build/venv-check/Scripts/python.exe'
$env:PYTHONPATH = "$PWD/backend/src"
& $py -m pytest backend/tests/test_compiled_build.py -q
```

Use the explicit `PY` path on every make invocation. New tests are expected to fail before their implementation, then pass. No inline LLM fake is needed for this slice; route tests use the existing shared fixtures.

## Task 1: Source fingerprint and APK stamp

**Files:** Create `compiled_build.py` in the package and scripts paths above; create `test_compiled_build.py`; modify Gradle and Android entry; extend `test_android_entry.py`.

**Interfaces:**

- `compiled_build.source_digest(package_dir: Path, templates_dir: Path) -> str` hashes sorted `(domain, relative_path, exact_bytes_hash)` entries.
- `compiled_build.dependency_versions() -> dict[str, str]` supplies installed base/desktop dependency versions and their installed transitive dependencies; absent optional desktop dependencies have an explicit absent value. Missing required metadata makes fingerprinting unavailable, rather than silently guessing.
- `compiled_build.fingerprint(package_dir: Path, templates_dir: Path, *, stamp_path: Path | None = None, require_stamp: bool = False) -> str | None` combines the source digest with `sys.version` and the version map. Unreadable inputs, invalid stamps and a required missing stamp return None.
- `python -m scripts.compiled_build --package PATH --templates PATH --output PATH` writes a JSON stamp containing `format: 1` and `source_digest`. The build stamp covers source inputs; runtime Python and distribution versions are added at runtime, so host versions are never substituted for Android's versions.

- [x] Write `test_source_manifest_and_stamp`, `test_bytecode_does_not_move_build`, `test_each_build_input_moves_build`, `test_unavailable_inputs_disable_build` and `test_cli_uses_explicit_paths`. Assert source, package data, template, Python and dependency changes alter the fingerprint; pyc/import artifacts and absolute checkout location do not. Test missing/malformed/unknown-format stamps and absent optional encoder metadata.
- [x] Run `test_compiled_build.py`; confirm the new helper/CLI is missing.
- [x] Implement the helper with fixed suffixes `.py`, `.json`, `.md`, `.html`, `.txt`, `.ttf`, `.j2`, `.xhtml`, `.ncx`, `.xml`, `.opf`, `.css`. Exclude `__pycache__` and bytecode. Test that this manifest covers all currently shipped resource suffixes. Base roots mirror pyproject base dependencies; desktop roots add tiktoken and uvicorn's standard-extra distributions. Test the root list against pyproject to prevent drift. No imports of prompts, grimoire.store or third-party packages: Gradle's build Python only needs the standard library.
- [x] Implement the build CLI and Gradle `generateCompiledBuild` Exec task. Declare backend/package and template inputs, script/helper inputs and one generated stamp output. Use the configured build Python when provided, otherwise the available `python` command; the source stamp has no host-version component. Set PYTHONPATH to backend/src plus backend. Make `stageGrimoireAssets` depend on this task and stage `grimoire/compiled-build.json` alongside frontend/templates.
- [x] Extend the Android entry test to assert `GRIMOIRE_COMPILED_BUILD` names `compiled-build.json` beside the extracted templates directory before imports. Set that variable from the supplied extracted assets path; preserve socket-reporting order, HOME, inference setup and USB behavior.
- [x] Run the two focused test files; commit the passing fingerprint/packaging checkpoint.
- [x] Copy the main checkout's machine-local `android/local.properties` into this worktree if absent, then run `make check-apk PY=<task-local-python>`. Inspect the built APK with zipfile: require the generated stamp, the sqlite3 standard-library package and `_sqlite3` extension/native SQLite library. The existing APK has those SQLite components; the new build must also carry the stamp. Do not install on a phone or touch its store. Record packaging evidence and the source-fingerprint cost on package/template inputs only.

Task 1 evidence (2026-10-11): fingerprint/Android-entry tests 42 passed, one Windows socket skip; import/path guard checkpoint 53 passed; new-file ruff clean. `make check-apk` passed (49 Gradle tasks, 4m 1s). Archive assertions confirmed the exact source stamp, sqlite3 package, _sqlite3 extension and native SQLite libraries. Packaged metadata resolves all required base dependencies and an explicitly absent optional encoder. One source-only manifest pass took 0.093s; this is a local pass, not a cold-start benchmark. No device install or data-store inspection.

## Task 2: Owned schema and safe database location

**Files:** Create `store/compiled.py`, `test_compiled.py`; extend `conftest.py`.

**Interfaces:**

- `SCHEMA = 1`, stable `APPLICATION_ID = 0x4752494D` and `cache_meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)`. Metadata starts with schema `1` and purge generation `""`; its table/key protocol stays stable for S5's obsolete-schema maintenance.
- `artifacts(kind TEXT, key_digest TEXT, payload TEXT, check TEXT, last_used INTEGER, PRIMARY KEY(kind, key_digest))`, all fields NOT NULL.
- A private session holds root, pid, connection, lock, lifecycle epoch and last SQLite-use time. Database location comes only from the supplied pinned library root.
- `_open_locked(root: Path) -> _Session | None` opens/validates that session under the connection lock; Task 2 tests drive this private seam. Task 3's Batch methods own lock acquisition and never expose a connection to callers.

- [x] Write tests for the exact device/schema filename, application_id, metadata and artifact schema. Assert DELETE journal mode, `auto_vacuum=INCREMENTAL`, `secure_delete=ON`, `synchronous=NORMAL`, and `busy_timeout=5` milliseconds. Assert device_key runs once per `(pid, root)` and that its failure disables cache attempts for that process.
- [x] Write linked-parent, linked-database, linked-journal and Windows reparse-attribute tests. Keep external target bytes and stamps unchanged. Where symlinks are unavailable, retain the attribute-injection test and run an actual Windows directory-junction test when supported. A foreign valid SQLite file at the owned filename is a miss; its tables are not overwritten.
- [x] Run the tests and confirm failure before `compiled.py` exists.
- [x] Add session/per-test `proclock.lock_dir()` floors to conftest, matching the existing default-home/pointer floors so `monkeypatch.undo()` cannot escape to the real machine directory. Add teardown that closes compiled handles/timers after each test. Leave the frozen fixture home untouched.
- [x] Implement module-scope optional SQLite import, fail-soft device-key memoization and safe directory creation. Use lstat plus reparse attributes; do not resolve cache-owned links. Check all owned leaf candidates before connect and mutation. Open with `check_same_thread=False`, timeout 0.005 seconds, validate owned metadata before reading rows and set auto_vacuum before creating tables. Unknown schema/ownership is a miss, not a migration or corruption rename.
- [x] Run focused tests plus existing path/import/atomic/lock-domain guards; commit the passing schema/containment checkpoint.

Task 2 evidence (2026-10-11): 13 missing-feature assertions RED -> 13 GREEN, including real symlink/junction and injected reparse cases. Isolation regressions in the Android resolver and two child-process locks were reproduced, then fixed by retaining the read-only resolver and passing the temporary lock floor explicitly into child scripts. Combined schema/lock/architecture verification: 281 passed, three platform skips. Ruff remains exactly at its 1146 baseline; Linux-platform mypy exactly matches all 165 baseline findings. No baseline file changed.

## Task 3: Registered keys and checksummed JSON artifacts

**Files:** Extend `compiled.py` and `test_compiled.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class Kind:
    name: str
    version: int
    path_inputs: tuple[str, ...]
    params: tuple[str, ...]
    allow_persisted_sources: bool

KINDS: dict[str, Kind]  # production entries are added with S6 consumers

@dataclass(frozen=True)
class Key:
    kind: str
    digest: str

@dataclass(frozen=True)
class Lookup:
    hit: bool
    value: object = None

def make_key(kind: Kind, inputs: Sequence[tuple[str, str]], *, params: Mapping[str, object]) -> Key | None: ...
def batch(root: Path | None = None) -> Iterator[Batch]: ...  # context manager

class Batch:
    def lookup(self, key: Key | None) -> Lookup: ...
    def put(self, key: Key | None, value: object) -> None: ...
    def flush(self) -> None: ...
```

`batch()` resolves home once if no root was passed and captures pid/root/epoch at context entry, before caller compute. A batch entered during a transition is permanently disabled, even if its first cache operation occurs after the fence exits. It never holds a connection lock while callers compute. Context exit flushes successful staged work; an exceptional caller drops the staged rows without masking its exception. S1 has only artifact rows; it makes no source-validation or materialization claim.

- [x] Write parametrized tests for null/false/zero/empty payloads, fresh mutable copies, canonical nested params, input-role ordering, path-derived params, changed version/BUILD and unregistered kinds. Assert `Lookup(hit=True, value=None)` differs from a miss. Test unsupported JSON values, NaN, malformed JSON, key-swapped rows and valid-but-altered JSON as uncached/misses.
- [x] Run these tests and confirm the interfaces are missing.
- [x] Implement canonical JSON with sorted keys, compact separators, ensure_ascii=True and allow_nan=False. Hash `[kind.name, kind.version, BUILD, params, ordered_inputs]`. Validate registry identity and exact required params/path inputs; an invalid key request is uncacheable. Memoize BUILD once per pid using Task 1, package location and `prompts.templates_dir()`; reuse the existing Android detector to require a stamp on Android. No source stat/read occurs here.
- [x] Compute row checks over `[kind, key_digest, canonical_payload]`; use bound SQL parameters. Every read addresses `(kind, key_digest)`. Decode JSON afresh on each hit. A checksum or decode failure costs a miss and allows later replacement.
- [x] Bound optional artifact work: `MAX_PAYLOAD_BYTES = 1 << 20` (one MiB, matching the existing vector-record allocation boundary), `MAX_KEY_BYTES = 64 << 10`, `MAX_JSON_DEPTH = 64` and `MAX_JSON_ITEMS = 100_000`. These bounds can make a large derivation uncached but never alter its file-reader answer. Set SQLite's connection `SQLITE_LIMIT_LENGTH` to twice MAX_PAYLOAD_BYTES before reading any stored values; use a key-bounded SQL byte-length/type predicate to reject payloads over MAX_PAYLOAD_BYTES before Python materializes them. SQLITE_TOOBIG is a miss, never corruption recovery. Preflight caller JSON iteratively for depth/item/string limits, serialize with a bounded iterencode accumulator, and reject excessive cached JSON nesting before json.loads. Catch recursion/allocation failures as cache misses or dropped writes. Test oversized TEXT/BLOB rows, Unicode serialization expansion, very wide/cyclic values and deep JSON, including correctly checksummed hostile rows; assert file-reader output remains available.
- [x] Stage puts in the batch, coalescing identical keys; encode/check before taking the connection lock. Flush one transaction using BEGIN IMMEDIATE and executemany, committing only if the root/epoch still matches. Reads acquire without blocking; writers wait at most `WRITE_LOCK_SECONDS = 0.010`, then drop. Failed commit rolls back and drops rows. No writes on artifact hits in S1; coarse LRU touches belong to S5.
- [x] Run all compiled tests; commit the passing artifact checkpoint.

Task 3 evidence (2026-10-11): artifact interfaces produced 32 expected missing-feature failures before implementation; all 48 cache tests now pass. Ruff remains at baseline; architecture guards passed 152 tests at the artifact checkpoint. Staging bounds (128 rows / 4 MiB) skip excess cache rows, preserving one transaction per request; all pending rows drop on caller exceptions. The row and byte budget tests failed before this behavior was implemented. New-file ruff is clean.

## Task 4: Cache degradation and corruption classification

**Files:** Extend `compiled.py` and `test_compiled.py`.

**Interfaces:** Degradation remains `Lookup(hit=False)`/dropped writes. `_warn_once(failure_kind: str) -> None` uses store.logs with a fixed message and failure-kind field only. No path, exception text or payload is logged.

- [x] Write tests for GRIMOIRE_COMPILED_CACHE=0, missing sqlite3, read-only/unreadable storage, device/build failure, SQL busy/locked errors, failed commit and one-log-per-kind behavior. Assert disabled lookup/put creates no cache directory and that a read with the connection lock already held returns immediately.
- [x] Write corruption tests using real invalid database bytes and injected extended sqlite_errorcodes. Assert only primary SQLITE_CORRUPT/SQLITE_NOTADB can rename aside; BUSY/LOCKED/read-only/unknown errors never rename a file or journal. Assert failure to acquire the recovery proclock preserves the old file, and a successful fresh open deletes only its recognized aside copy.
- [x] Run to demonstrate the missing failure paths.
- [x] Implement non-blocking read failure handling and bounded write handling, separating cache exceptions from caller computations. Mask extended sqlite_errorcode to its primary code. Corruption replacement uses a machine-local proclock with NO_WAIT, rechecks containment and closes this process's bad handle before rename. Do not recover a merely missing/foreign/mismatched-schema database by destructive replacement. A failed optional recovery remains a miss.
- [x] Write the module's durable theory in its docstring and nearby comments: files establish the live set, caller keys establish selection, checksums detect damaged values, metadata establishes cache ownership, and every cache failure returns control to the caller. Link the spec and the adversarial tests that pin those claims.
- [x] Run compiled tests and the architecture guards; commit the passing degradation checkpoint.

Task 4 evidence (2026-10-11): five corruption-path failures and a stale-error replacement regression reproduced before their fixes; all 67 cache tests pass. New-file ruff is clean. All 152 architecture guards passed.

## Task 5: Idle, root-change, shutdown and fork ownership

**Files:** Extend compiled.py, maintenance_reports.py, logs.py and paths.py; create test_compiled_lifecycle.py; modify config route/main teardown; extend test_data_dir.py and helper lifecycle tests.

**Interfaces:** `compiled.close() -> None` closes the owned connection, cancels its timer and advances the lifecycle epoch. It is idempotent and never raises. Explicit close invalidates pending batches; an ordinary idle close may reopen under the same root/pid. `compiled.root_change() -> Iterator[None]` is a context manager fencing cache operations across the entire authoritative root switch, without holding the connection lock across its body.

- [x] Write tests with two worker threads sharing one connection, a held write lock causing an immediate read miss, and one transaction for several staged puts. Assert no lock is held during caller compute.
- [x] Write `test_idle_closes_handle`, `test_root_change_drops_prepared_batch`, `test_failed_root_change_is_harmless`, `test_shutdown_close_runs_on_worker` and `test_fork_reopens_with_fresh_locks`. Use an injectable monotonic clock/timer seam instead of sleeps. Test a real fork on POSIX and simulated pid change everywhere. In separate real-fork cases, hold the connection lock, maintenance_reports._DEVICE_LOCK, logs._append_guard and logs._size_guard on a different parent thread. Require the child's lookup to finish or miss within a bounded timeout, including its first device-key lookup after the memo reset. For each log lock, force child cache degradation through the real logs.record sink and assert the warning completes without exposing paths or payloads.
- [x] Write an async test that attempts lookup/flush on a running event-loop thread and asserts sqlite.connect/execute are never reached. Cache operations there miss/drop; caller output remains available.
- [x] Write `test_root_switch_cannot_reopen_old_handle`: pause the route immediately after the fence has closed the old session but before set_data_dir changes the pointer. A concurrent old-root lookup must miss without opening SQLite, and a batch created inside the fence must drop after the switch. In separate tests, enter a batch before the switch and inside the fence but defer every lookup/put until after fence exit: neither may capture the new epoch or reopen the old root. Assert no old handle or pending rows survive successful or failed switches.
- [x] Run the lifecycle/root tests and confirm the new behavior is absent.
- [x] Implement `IDLE_SECONDS = 30.0`: one timer per open connection, updating last-use in memory; the timer reschedules itself if still active rather than creating a thread per lookup. Its callback closes under the connection lock. A stale timer cannot close a replacement connection.
- [x] Make root initialization precede pointer publication in paths.set_data_dir. Factor initialization for an explicitly supplied root, keep ensure_home's public behavior, initialize the requested custom/default root and the effective env-override root before the atomic pointer write, and return the already initialized effective root without fallible initialization after publication. Preserve env priority, pointer contents and custom/default resolution. Test custom selection and default reset with a regular file named worlds/campaigns, plus pointer-write failure: the old pointer and active root remain selected, no authoritative contents are removed, and the route returns its existing error response. Directory creation before a failed pointer write is harmless preparation.
- [x] Wrap store.set_data_dir in compiled.root_change inside the existing config exclusion. Enter under the short connection lock: raise a transition counter, close the session and advance the epoch; then release the lock for the root switch. Lookup, put, flush and open miss/drop while the counter is nonzero. Finally, under the short lock, close any owned session defensively, advance the epoch again and lower the counter. Nested fences remain fenced until the outermost exit. Batches pin pid/root/epoch at context entry and permanently disable themselves if entered while fenced; recheck that identity before any open or write. A failed pre-publication initialization or pointer write leaves the old root usable with a cold cache. Keep validation/refusal behavior, migration restart, pending-run cleanup and logs reset.
- [x] Close compiled resources via anyio.to_thread.run_sync in the outer lifespan finally. Preserve runner.uninstall before the first await and existing client-close ordering/failure isolation. Do not add a startup cache sweep.
- [x] On POSIX register fork hooks: close SQLite in the parent before fork while holding its connection lock, release in the parent, and create fresh locks/state in the child without operating on an inherited SQLite handle. Add a pid-change fallback for platforms without hooks. Clear process-scoped build/device/warning memo state in the child. maintenance_reports owns the reset of its own _DEVICE_LOCK: register a child fork hook there and provide the same pid-change fallback before device_id takes the lock. Do not reach into that helper's private lock from compiled.py. Fork synchronization can wait for the parent's short DB operation; ordinary read lookups still cannot wait.
- [x] logs.py likewise owns child resets for _append_guard, _size_guard and the thread-local recursion latch; clear copied size/cap bookkeeping. Register its child hook and run its pid-change fallback before record or forget_file_sizes can acquire a guard. Audit the device and warning helper call chains for any further application-owned locks before implementing fork recovery; reset those locks in their owners or return a bounded cache miss without acquiring them. Keep logging's normal append serialization and cap behavior intact.
- [x] Run lifecycle, data-dir, existing LLM lifecycle and compiled tests; commit each passing lifecycle checkpoint.

Task 5 evidence (2026-10-11): 198 compatibility tests passed across cache, roots, logs, maintenance and LLM lifecycle. The extended lifecycle/log suite passed 76 tests with four POSIX fork skips on Windows; simulated pid resets and real log sink checks ran. Ruff remains exactly at 1146 baseline findings. Real POSIX execution remains a platform verification limit.

## Task 6: Architectural guards and slice verification

**Files:** Create test_compiled_guard.py; extend test_vectors.py; update CONTRIBUTING.md, roadmap and the intentional store API snapshot addition.

**Interfaces:** `_violations(source: str, module: str) -> list[str]` in the test guard is independently exercisable on planted source. Production callers get no connection/cursor accessor or cache-inventory API.

- [x] Write planted failures for sqlite3 imports/aliases outside store.compiled, a public cache read with no supplied key, and SELECT payload FROM artifacts without a key predicate. Also pin the permitted registry/metadata/schema PRAGMA operations so the guard does not mistake maintenance for source membership.
- [x] Run the planted cases to show the guard does not yet exist.
- [x] Implement the AST guard across runtime package sources. Detect import and ImportFrom aliases; check artifact read APIs take keys and all artifact read SQL is statically key-bounded. Reject dynamically assembled read SQL rather than pretending an AST guard can prove it safe. State the guard's limits in its docstring. Future S3/S5 guarded maintenance exceptions require their own reasoned tests; do not add them in anticipation.
- [x] Add the vectors golden-key test: existing `(space + NUL + NUL-stripped text)` selects the same .vec across BUILD changes. Assert saved vector bytes remain loadable after a compiled BUILD change. Leave vectors.py unchanged.
- [x] Document the guard and its owner in CONTRIBUTING.md. Mark only S1 delivery/checks actually evidenced in the roadmap; S2-S6 and their contracts remain open. Commit the passing guard/documentation checkpoint.
- [x] Run focused tests under branch coverage for compiled.py/compiled_build.py, then `make check PY=<task-local-python>` and `make check-apk PY=<task-local-python>`. Keep the frozen campaign home and snapshot byte-identical. Do not increase lint baselines to hide findings; regenerate smaller baselines only for findings genuinely removed.
- [x] Run `/codex:review` on the S1 diff, resolve findings, and commit passing fixes. Then run `/codex:adversarial-review` against the complete S1 diff and originating spec specifically for 03-S1 fidelity. Resolve every material finding before recording completion. Provide full review snapshots if the runner's two-file limit or Windows sandbox failure omits evidence; remove those transport files afterward.

Task 6 verification so far (2026-10-11): guard/vectors/docs 121 passed; focused cache/build/Android/vector suite 182 passed with five platform skips and 90% combined branch coverage (helper 92%, cache 89%), without warnings. Ruff is exactly 1146 at baseline; Linux-platform mypy exactly matches all 165 per-file/rule baseline findings. Actual make check stops at the preexisting Windows-only atomic/proclock findings. The remaining template (443), ESLint (727 at baseline), frontend typecheck/test coverage (4600 tests, 212 files) and APK gates passed. The rebuilt APK's source-only stamp matches today's package/templates, and sqlite3, _sqlite3 and libsqlite3 are present. Full backend and Python 3.12/Pydantic 1 compatibility runs and the required review gates remain pending.

Native code review (`01a12c44-01fa-7d11-8ae7-23db5f7252f5`) identified four material issues: ownership probes could roll back a foreign hot journal; corruption replacement could race a writer; cancellation could skip shutdown cleanup; and quoted/JOIN SQL could evade the inventory guard. Seven regression cases failed before their fixes. Immutable ownership probing, shared nonblocking SQL/recovery exclusion, inode revalidation, shielded teardown and the strengthened guard resolve them. An owned hot-journal recovery case and an idle handle replacement case also pin the protocol.

Post-review verification: 201 passed, five platform skips, 89% combined branch coverage (helper 92%, cache 88%). Four skips require POSIX fork; the fifth is Windows refusing replacement of an open SQLite file. Actual `make check` again stops solely at the existing Windows mypy differences: atomic.py attr-defined 0→2 and proclock.py attr-defined 4→7. Linux mypy matches the exact 165-finding baseline, and ruff remains exactly 1146. A fresh APK rebuild passed all 49 Gradle tasks; archive inspection confirms its source stamp matches the current package/templates and all three SQLite layers are packaged. Final full backend/Pydantic 1 runs and the S1 adversarial fidelity review are running; the earlier interrupted pre-fix runs are not completion evidence.

Final adversarial fidelity review (`01a12c68-1114-7090-98a2-c38f73e77ddb`) reviewed all 14 full diff/spec/plan snapshots and identified one material omission: damaged artifact rows missed silently. Six new regressions failed for checksum, malformed/deep/nonfinite JSON, BLOB and oversized payload damage. Commit 7a20830e1 resolves them with one fixed `artifact` warning per process; ordinary misses stay silent, bounded SQL preserves rejection evidence without loading hostile payloads, and failures never rename the database. The final cache/lifecycle/guard/logging/teardown suite passed 194 tests with five platform skips. Fresh focused branch coverage passed 207 tests with five skips (89% combined; helper 92%, cache 89%). Python 3.12/Pydantic 1 focused verification passed 176 tests with five skips and the installed Starlette/AnyIO deprecation warning. Actual make check still stops only at the existing Windows mypy pairs; ruff remains at 1146. Another APK rebuild passed 49 tasks, with its current source stamp and SQLite layers verified. All material native and fidelity findings are resolved; full backend runs started before the final warning change and must be reported separately from this fresh final-code evidence.

The full Python 3.12/Pydantic 1 run completed: 20 failed, 17949 passed, 44 skipped, eight warnings and ten long-ID setup/teardown errors. One new omission was real: store_api_baseline.json lacked the intentionally introduced public store.compiled module. Its one-line dir addition preserves every previous API name and leaves __all__ identical. Native follow-up review (`01a12c8d-01ce-7cb3-8cb3-7a5ae8775800`) accepted this as a deliberate API addition; the reviewer used complete snapshots and could not execute locally. Fresh API/import/docs checks passed 66 tests in each Python environment. Frozen campaign home and snapshot.json were not changed.

For the remaining five failures beyond the original fourteen Windows cases, isolated replay on this branch passed four cases and failed two; the same six-case selection on a temporary exact f816921c8d2bd91628cfe12e28bda9106a254f11 source archive passed three and failed three. The base archive's sole harness addition isolates its machine lock floor. Continuity's not-a-generation clock assertion and both absorb-budget cases therefore reproduce on the base in Python 3.12. Selector setup's PermissionError and the search scaling assertion pass on both replays; these remain transient full-run failures, not proven fixed or newly classified as baseline. The search failure measured a zero small CPU-time sample before its floor. No unrelated product/test behavior was changed to conceal those results.

Execution rulings retained from the Native ledger:
- Extend the three existing lock tests to retain the real read-only resolver and explicitly share the temporary parent/child lock floor; otherwise they test different lock domains. Cost if wrong: cross-process exclusion evidence is invalid.
- Skip excess staged rows. This supersedes the rejected auto-flush-prefix ruling because the binding spec requires one request transaction. Cost: large requests warm fewer disposable rows.
- Use the plugin's native review/start API with full inline snapshots when its CLI rejects focus and its read-only shell cannot start. The whole-branch reviewer fetched pinned base context; the API-addition follow-up was explicitly scoped. Cost: omitted unchanged context can weaken review; snapshot-only limits are recorded.
- Retain the full integration runs started before the final warning fix, with fresh final-code native/Pydantic 1 focused checks instead of restarting both complete Windows suites. The API expectation's one-line correction landed while the native integration run was still active; separate fresh guard runs verified it in both environments. Cost: an unrelated integration interaction may not be measured at the exact final tip. Source selection, keys and lifecycle protocol did not change in the final warning fix.
No minor findings are deferred. Real POSIX fork and on-device execution remain explicit verification limits. Full-repository Windows failures remain visible; no gate is claimed green on that platform.

Final native full backend result: 16 failed, 17959 passed, 38 skipped, seven warnings and ten long-ID setup/teardown errors; the repository branch-coverage floor passed at 93.88% (required 93%). Fourteen failures match the preimplementation Windows baseline. The two additional failures were response-extend setup's transient config PermissionError and a missing follow-up run; both pass in fresh isolated replay on this branch and on the exact pinned base (2/2 each). They remain visible full-run failures, not claimed fixed. Together with the Pydantic 1 results and targeted final-code checks above, this completes the planned verification work with explicit Windows/platform limitations. The full native run began at d3c100948 before the final warning fix, and read the corrected public API expectation later in that run. No unrelated baseline increase or frozen-campaign edit was made.

Further rulings:
- Deliberately add store.compiled to the public API snapshot's dir list, verified by the existing RED guard, fresh 66-test runs in both environments and a scoped native review. Cost if wrong: accidentally accepting a public API change.
- Accept the real POSIX fork checks as unexecuted on this Windows host, with four real cases provided and simulated pid resets exercised. Cost if wrong: child-process deadlocks may remain platform-specific; no POSIX result is claimed.
- Accept APK build/archive verification without on-device execution. Optional import/build failure still degrades to a miss. Cost if wrong: Android persistence can remain disabled or encounter an unmeasured runtime issue.
- Keep S2-S6 as separate implementation units from the approved full spec; their absence is not an S1 defect. Cost if wrong: confusing foundation delivery with completed Spec 03. The overall spec checkbox remains open and S2 waits for human review of its approved plan.

## Baseline evidence and verification limits

Before any runtime changes, documentation guards passed 63 tests, ruff reported 1146 findings exactly at baseline, and all 443 template checks passed. A full mypy probe with `--platform linux` matched all 165 baseline findings exactly; native Windows mypy adds platform-specific findings in atomic.py/proclock.py. Do not ratchet those increases into the baseline.

The unmodified Windows backend run finished with 14 failed, 17811 passed, 33 skipped and 10 setup/teardown errors. A serial run independently reproduced `test_atomic.py::test_concurrent_appends_interleave_whole_lines` losing rows. The diff reconstruction cases' long parameter IDs exceed Windows' 32767-character environment-variable limit when pytest sets PYTEST_CURRENT_TEST; that setup/teardown error also reproduces serially. Record and classify remaining failures from pytest's cache without dumping enormous node IDs. These results are a baseline limitation, not a passing gate. New compiled tests and full-suite deltas must be reported separately; do not claim make check passed unless its actual output does. S1 completion still requires the review gates and accurate reporting of every outstanding verification limitation.

## Self-review and handoff

The six tasks cover S1's Android prerequisite, location/schema, key checksums, optional/fail-safe behavior, connection lifetime and guards. Each Review Focus condition has a named test owner. No production derive/source/materialized/purge/consumer behavior is claimed by this plan. The wider spec's seven review corrections remain assigned to S2/S4/S5/S6 as applicable; the stable schema metadata and connection boundaries avoid obstructing them.

The human reviewed this plan and chose **Native** execution on 2026-10-11. After the Codex plan gate passes, use executing-plans without another execution-method question. Preserve the user's instruction to commit liberally; implementation approval does not authorize merging or publishing.

Independent brutal review loop 1 (pinned becede38c) reproduced two blockers: nested-query inventory guard bypass and default home resolution crossing the root fence before identity capture. Both have new RED->GREEN regressions. The guard now rejects nested SELECT/JOIN and shadowed SQL literals; batch captures identity before root resolution. Cache/lifecycle/source/guard tests pass 143 with five platform skips; ruff clean. Independent re-review follows the committed fixes.

Independent brutal review round 2 at ae1218d found predicate-suffix and import-rebinding guard bypasses. Eight planted regressions failed before the fix. Accepted WHERE forms now consume the complete statement, and literal resolution excludes imports, star imports, definition names, exception bindings and pattern captures. Independent re-review is required at the resulting commit.

Independent brutal review round 3 at 62aab19 found quoted SQL text could invent a terminal safe WHERE after naive comment removal. Two regressions failed before correction. A single lexical pass now recognizes quoted literals/identifiers before comments and masks their contents. Guard regressions and clock/lifecycle tests: 92 passed, 4 platform skips; ruff clean. Re-review remains pending.

Round 3 also found the dynamic PRAGMA prefix exempted trailing SQL scripts. Its planted regression failed before the fix. The owner now uses a static application-id PRAGMA, and the guard rejects all dynamic SQL without an exception.

Independent round 4 at 92801c571 confirmed prior fixes and found SQLite's single-quoted table names were masked as ordinary literals. Both planted table forms failed before correction. The lexer now preserves exact artifacts/sources names under all supported quoting forms for conservative rejection. Review evidence at the pin:216 passed, five skips;ruff clean. Further independent review follows the corrected checkpoint.

Final ownership correction: the old raw-garbage corruption fixture incorrectly licensed destructive recovery from SQLITE_NOTADB plus a filename. Unknown/unprovable ownership remains a miss under the original ownership contract. Ordinary replacement now requires fresh recognized metadata; a real owned corrupt payload-table/public-lookup recovery control remains green. Existing .corrupt neighbours also require independent proof in open, retention and delete cleanup. Native/fidelity followup below covers this clarification against the original spec.
