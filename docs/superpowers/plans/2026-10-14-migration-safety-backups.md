# Migration Safety Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete #496 by limiting migration archives to protected settings and showing live archive progress without weakening recovery or scheduled full backups.

**Architecture:** Keep selection, ZIP publication, naming and retention in `store/backups.py`. Migration owns progress and activity; existing status responses, presentation helpers and polling carry them to the UI. Reuse the existing backup lock, atomic publisher, root activity registry and completed-archive notes.

**Tech Stack:** Python 3.11+, standard-library ZIP/filesystem/threading, FastAPI, React/TypeScript, pytest and Vitest; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-14-migration-safety-backups-design.md` (approved by the user after the resolved Codex spec review recorded in `docs/superpowers/validation/2026-10-14-migration-safety-backups-spec-review.md`).

## Global Constraints

- Protect `config.md`, recursive `llm_connections/` and `sampler_presets/`, `inference-retired.json`, and immediate campaigns' `campaign.md` and `revision.txt`, preserving exact bytes and relative paths.
- Do not traverse assets, worlds, scenes, logs, other campaign subtrees or caches. Unreadable selected content, selected nested directory symlinks and output overlap that omits protected records fail closed; the store root itself may be reached through a symlink.
- New names are `pre-inference-settings-grimoire-<stamp>.zip` and `pre-retirement-settings-grimoire-<stamp>.zip`. Keep `SAFETY_PREFIX` and `RETIRE_PREFIX` as family prefixes and recognize old full safety names.
- Retention prunes only ordinary full archives. Settings archives never satisfy the full-backup schedule; old full safety archives still do.
- Progress phases are `safety_backup` and `retirement_backup`. Inventory reports `done: 0, total: null`; known counts satisfy `0 <= done <= total`. Intermediate persistence is at most four writes per second, with immediate transitions and final counts.
- Independent `active` spans the complete live migration hold. Current settings stay editable during retirement; missing additive API fields remain compatible.
- Reuse only completed archives named by this root's existing note. Preserve retirement's same-run-created exception; partial ZIP resumption and automatic restore are outside scope.
- Preserve privacy, atomic writes, nonblocking migration exclusion, Android-compatible dependencies and plain pydantic fields. Use only existing placeholder names and isolated fixture stores; never regenerate the frozen fixture's `home/`.

## Review Focus

1. A backup folder inside protected settings must fail before any protected write, rather than silently dropping files (Task 1 and Task 2).
2. A format-2/done response before retirement begins must keep the open page polling through later progress and completion (Task 2 and Task 3).
3. Old full safety archives and new partial archives together must retain distinct scheduling and recovery meaning (Task 1 and Task 3).
4. A stale foreign-process note, moved root or failed progress write must not leave false live progress or make a settings read wait on migration (Task 2).
5. A file vanishing after inventory must complete its count slot without publishing an incomplete ZIP after an unrelated read error (Task 1 and Task 2).

## Files and ownership

- `backend/src/grimoire/store/backups.py`: selected inventory, shared ZIP writer, series recognition, row scope and scheduling.
- `backend/src/grimoire/store/inference/migrate.py`: additive status fields, root-scoped live progress, throttled notes and both archive call sites.
- `backend/src/grimoire/store/locks.py`: clarify that status makes an immediate nonblocking probe, never a waiting acquisition under another lock; no lock API change. Reuse `store/proclock.py` unchanged for the optional per-archive progress lease described in Task 2.
- `backend/tests/test_backups_settings.py` (new): focused scoped-archive behavior; use existing `test_backups_store.py` conventions.
- `backend/tests/test_backups_store.py`, `test_backups_routes.py`, `test_inference_migrate.py`, `test_inference_retire.py`: existing family contracts, API passthrough, protected-write ordering, progress/activity and reuse regressions.
- `frontend/src/api/types.ts`, `components/BackupsPanel.tsx`, `components/inference/{migration.ts,InferenceBanner.tsx,useInferenceSettings.ts}`: compatible wire types and presentation/polling.
- Matching component/helper tests and `frontend/src/routes/ModelsView.test.tsx`: real open-page behavior through the existing harness.
- `CLAUDE.md`, `docs/store-guarantees.md`: current backup, progress and recovery guarantees. Historical specs remain historical.

## Commands

Backend commands below run from the repo root with this checkout's `backend/src` first on `PYTHONPATH` and the main checkout's venv interpreter. Its executable is `backend/.venv/bin/python` on macOS/Linux or `backend/.venv/Scripts/python.exe` on Windows; pass that absolute executable as `PY` to make in this worktree. Frontend commands run from `frontend/` so the test configuration loads.

### Task 1: Scoped safety ZIPs with honest listing and scheduling

**Files:** `backups.py`; new `test_backups_settings.py`; existing backup store and route tests.

**Interfaces:** Retain `create_backup(when: datetime | None = None, prefix: str = "", *, on_progress: Callable[[int, int | None], None] | None = None) -> Path`. A nonempty supported family prefix now selects settings scope and inserts `settings-` before `grimoire-`; an empty prefix and `create_image_backup()` retain their scopes. Internal `_settings_files(root: Path, skip: tuple[Path, ...], directory: Path) -> list[Path]` inventories selected files before publication. `_archive_into` accepts that optional inventory and callback while continuing to own the shared ZIP compression and timestamp behavior. `list_backups()` adds `scope: "full" | "settings"`; `due()` ignores settings rows.

- [ ] **Step 1: Write failing behavior tests.** Parameterize both family prefixes; seed only placeholder settings and unrelated content, and assert the exact ZIP member set and each member's original bytes. Assert the callback sequence starts `(0, None)`, then `(0, total)`, finishes `(total, total)` and never decreases. Monkeypatch walks to fail if they enter assets/worlds/scenes; ensure those failures are never reached.

  Name the primary test `test_settings_archive_preserves_only_protected_bytes`, using a local fixture that sets `GRIMOIRE_HOME`, writes the six protected path shapes from Global Constraints, and returns their relative-name-to-bytes mapping as `protected`. Add unrelated image/transcript bytes separately. Its core assertions are:

  ```python
  assert zipped_bytes == protected
  assert archive.name.startswith(prefix + "settings-grimoire-")
  assert progress[0] == (0, None)
  assert progress[1] == (0, len(protected))
  assert progress[-1] == (len(protected), len(protected))
  ```

- [ ] **Step 2: Write the remaining boundary and scheduling regressions before coding.** Add tests for unreadable selected files/directories, absent optionals, vanished files/directories, file links, linked store root, selected directory links, empty selection, old timestamps, same-second allocation and external/ancestor/root output. Destinations at `llm_connections/`, `sampler_presets/`, `campaigns/`, one campaign directory or inside a selected settings subtree must raise before publication if excluding them would omit selected data. Also add old/new family listing and API scope, settings-only due, mixed full/settings schedule, retention, root-output artifact exclusion and abandoned-temp cleanup tests. Keep ordinary/image and legacy-name compatibility cases as baseline preservation tests; skip only unavailable symlink creation.
- [ ] **Step 3: Run the new tests and observe failures.** Run `make test-py-fast TESTS=backend/tests/test_backups_settings.py ARGS="--maxfail=0" PY=<absolute-main-venv-python>`; this explicitly overrides the fast target's first-failure stop so every new regression is observed before coding. Expect missing progress support, old names and overbroad membership before implementation.
- [ ] **Step 4: Implement inventory and shared writing.** Enumerate only selected root records, the two settings subtrees and immediate campaign directories. Use strict stat/list operations: absence may be skipped, other errors propagate. Reject selected directory symlinks (including nested ones); retain file-symlink behavior. Emit `(0, None)` before inventory; the shared writer emits known totals after inventory. Validate output overlap before excluding any protected record. Root output remains supported by excluding recognized archives and temps. Reuse `_store_file`, `_walk_error`, `_skips`, the backup lock and `atomic.streaming_write`; count tolerated vanished members as processed, including a regular file that vanishes before copying. Empty inventories finish at `(0, 0)`.
- [ ] **Step 5: Implement recognition and scheduling.** Extend the two kept-family regexes with an optional noncapturing `settings-`, preserving date/ordinal groups. Share recognition with exclusion and abandoned-temp cleanup. Set scope from recognized filenames; treat older names as full. Keep `sweep()` ordinary-only and filter settings rows in `due()`. Update old assertions about newly generated safety names/scheduling, retaining explicit legacy-name fixtures; every new behavior now has a regression that was run before its implementation.
- [ ] **Step 6: Run focused suites and commit.** Run `make test-py-fast TESTS="backend/tests/test_backups_settings.py backend/tests/test_backups_store.py backend/tests/test_backups_routes.py" PY=<absolute-main-venv-python>`; expect all selected tests passing. Review the diff, then commit `fix: scope migration safety archives to protected settings`.

### Task 2: Live migration progress and activity across both archive phases

**Files:** `migrate.py`, the lock-contract comment in `locks.py`; existing migration/retirement tests; process-test scaffolding follows `backend/tests/test_proclock.py`.

**Interfaces:** Append compatible fields `active: bool = False` and `progress: dict | None = None` to `Status`, preserving existing positional fields. `as_dict()` includes activity and includes progress only during an active archive. Extend `_remember(..., progress: dict | None = None, progress_owner: str = "")` and `_Run`; callbacks call `_Run.archive_progress(phase: str, done: int, total: int | None) -> None`. An internal `_archive(run: _Run, prefix: str, phase: str) -> Path` wraps the existing backup call and always clears progress. `_activity(root: Path) -> bool` reports the local registry or another process's nonblocking migration-lock hold. Private `@contextmanager` helper `_progress_lease(root: Path) -> Iterator[str]` yields an archive nonce when it can hold an optional machine-local proclock, else an empty string; `_owns_progress(root: Path, nonce: object) -> bool` verifies that lease without waiting. The nonce is private status-note metadata, never part of the public progress object.

- [ ] **Step 1: Write all new migration regressions before implementation.** Observe status from a wrapper around the callback supplied to the real archive writer, for both phases; assert phase/counts, active activity and retirement's existing done state. Pause retirement planning with threading events before any archive and inspect status from another thread. Also write the controlled-clock throttling, persistence failure, archive failure/retry, stop/exception cleanup, invalid/stale foreign-note, moved-root, same-thread and real second-process regressions described below before adding the behavior. Existing no-write idle, marker ordering and archive-reuse cases are preservation checks, not tests that must fail.
  Name the regressions `test_archive_progress_is_visible_while_writing` (parameterized phase) and `test_progress_activity_covers_retirement_planning`. The former wraps the callback supplied to the real backup function: invoke the supplied callback, then capture status, so both initial and final counts are observable. The latter pauses planning with threading events and reads from another thread. Their key assertions are:

  ```python
  assert during["active"] is True
  assert during["progress"]["phase"] == phase
  assert planning["state"] == "done" and planning["active"] is True
  assert "progress" not in planning
  assert after["active"] is False and "progress" not in after
  ```

- [ ] **Step 2: Pin persisted-progress ownership before coding it.** Add `test_foreign_progress_requires_the_current_archive_lease`: with the fixed migration lock held by a child, a valid old same-root note must expose no progress while the new run is paused in planning. The child then holds the new note's nonce-specific lease and progress becomes visible; after releasing that lease while retaining the fixed lock, progress disappears even when clearing the old note was made to fail. Include a new archive in the same PID and an old still-live PID so PID checks cannot make this pass accidentally. The process holder takes an absolute lock path resolved by the parent, following `test_proclock.py`; test monkeypatches do not implicitly propagate to a Windows child. Add `test_progress_readers_cannot_impersonate_the_archive`: keep the new run's fixed lock held during planning, pause reader B after it acquires the old free lease but before releasing it, and have reader C read the same stale note. C must expose no progress. Then let B release, verify stale progress remains hidden, and confirm that a real live archive lease is still observable. Use handshakes and guaranteed process/descriptor cleanup, with no timing sleeps as a race proof.
- [ ] **Step 3: Run new regressions and record their intended failures.** Run `make test-py-fast TESTS="backend/tests/test_inference_migrate.py backend/tests/test_inference_retire.py" ARGS="--maxfail=0 -k progress" PY=<absolute-main-venv-python>`. Name all new Task 2 cases with a `progress` prefix so that selection covers the complete new matrix. `--maxfail=0` runs all selected cases before coding. Expect missing activity/progress for new positive behavior; run preservation cases as baseline checks. If a new negative case already passes because the old build never reports progress, keep it paired with its positive live-owner case and do not manufacture a failure.
- [ ] **Step 4: Implement progress and optional writer ownership.** Extend the existing guarded root registry to membership plus optional live progress, initialized/cleaned by `_running_on`. Memory is authoritative for local readers; notes are best effort. For each archive, `_progress_lease` creates a UUID4 hex nonce and tries `proclock.acquire(proclock.lock_path(root, "inference-progress", nonce), proclock.NO_WAIT)` under the already-held fixed migration lock. It holds the descriptor for that archive only, and releases it in `finally`. If acquiring the optional lease fails, yield an empty nonce and continue archiving with local progress; never reuse an old nonce. Assign `_Run.progress_owner` for that archive and persist it as `progress_owner` beside progress in the existing note; clear it with progress before releasing the lease. Failures opening, probing or releasing the optional lease must not turn an otherwise valid archive into an error; unverifiable ownership suppresses foreign progress. A foreign reader first requires a 32-character hexadecimal nonce and an existing machine-local lease file. Before probing that lease, it must itself try-acquire the root-specific observer mutex at `proclock.lock_path(root, "inference-progress-reader", "probe")` with `NO_WAIT`. Failure or contention on the observer mutex suppresses foreign progress. While holding that mutex, it probes the nonce lease: contention now means the archive writer holds it, because every status reader is excluded from concurrently holding a successful probe; an acquired probe is released before the observer mutex and means no live owner. Release both descriptors in `finally`, treating I/O failures as unverifiable ownership. Missing/unreadable/free lease files mean no foreign progress. Never probe a lease without holding the observer mutex: otherwise one reader can impersonate the archive owner to another reader. The writer does not take the observer mutex, so readers never delay archive work and all read-side lock acquisitions are nonblocking. The main migration lock alone, PID, timestamps and note shape do not establish ownership. Lease files live outside the store, use existing proclock hashing, contain no library content and may remain as tiny inactive lock files; no new cleanup or restore format is introduced. A failed note clear or a new run's planning phase can therefore never revive the prior archive's progress.
- [ ] **Step 5: Implement the count lifecycle and nonblocking activity.** `_Run.archive_progress` updates local memory on every callback and persists using `time.monotonic`, forcing phase/known-total transitions and final counts. `_archive` clears local progress and the note in `finally` before releasing the optional lease; run termination clears memory again. Pass the callback at both safety sites without changing reuse or write ordering. `_activity` checks the local root registry first; otherwise recheck the resolved root around a try-acquire/release of the existing fixed migration lock, without holding it while reading settings. Do not attribute another locally active root to this root. Probe I/O failures must not turn status reads into errors. Keep migration state rules and no-write idle behavior; foreign progress also needs valid phase/count shape and matching root provenance. Reject bool counters, negative/out-of-range counts and unknown phases.
- [ ] **Step 6: Verify the complete failure matrix and commit.** The tests already written above use a controlled monotonic clock for quarter-second throttling; fail note writes while local progress remains visible and archiving succeeds. Assert no stale progress after lease release, run termination or a root switch. Before a deliberately failed/rejected archive, capture protected bytes and prove protected writes did not land. Keep old/full and new/scoped completed reuse, missing-archive retry and retirement's created-this-run exception covered. Run both focused migration suites and Task 1 tests; expect all selected tests passing. Commit `fix: report migration archive progress across retirement`.

### Task 3: Show partial archives and keep progress visible on an open page

**Files:** API types, backup panel, migration helpers/banner/poller, their existing tests, and `ModelsView.test.tsx`.

**Interfaces:** Add `BackupEntry.scope?: "full" | "settings"`, `MigrationStatus.active?: boolean` and optional `progress: { phase: "safety_backup" | "retirement_backup"; done: number; total: number | null }`. Export `migrationProgress(status: MigrationStatus | null | undefined): string | null` from `migration.ts`; both the banner and current-layout quiet line use it. Extend `upgrading()` to keep polling while activity or progress exists, without changing layout-based editing guards or poll intervals.

- [ ] **Step 1: Prepare pinned frontend dependencies.** Run `make frontend-deps` from the repo root, which installs the lockfile-pinned packages needed by the following frontend commands. This worktree currently has no Vitest executable.
- [ ] **Step 2: Add all UI regressions before implementation.** Banner/helper tests assert preparing text for unknown totals, `3 of 8 files` for copying, `0 of 0 files` without percentage arithmetic, retirement wording and unchanged failure/newer/pending fallbacks. Backup-panel tests cover mixed scopes, older rows without scope, settings-only listing with `No full backups yet.`, and partial-recovery guidance. Write the open-page fake-timer regressions as well: first format 2/done/active/no progress, then retirement preparation/copying/completion; and format 1 through the global marker into an active gap. Assert continued reads and enabled editing during the gap, counts once copying starts, stopped reads after completion, and newest-save/install wins over an older in-flight poll.

  Name the regressions `renders archive preparation and file counts`, `labels settings archives as partial` and `settings archives alone do not count as full backups`. Assert:

  ```typescript
  expect(screen.getByRole("status")).toHaveTextContent("3 of 8 files");
  expect(screen.getByText("Model settings safety archive (partial)")).toBeInTheDocument();
  expect(screen.getByText("No full backups yet.")).toBeInTheDocument();
  ```

  Name the new open-page regression `retirement beginning after a done response stays visible`. Use the same real ModelsView harness as the existing unlock test; assert continued reads and enabled editing during the active gap, then the copying line and stopped reads:

  ```typescript
  expect(main().getByRole("button", { name: "Edit models" })).toBeEnabled();
  expect(main().getByText(/3 of 8 files/)).toBeInTheDocument();
  expect(api.getInferenceSettings).toHaveBeenCalledTimes(readsAtCompletion);
  ```

- [ ] **Step 3: Run the new UI regressions and observe the failures.** Run `npx vitest run src/components/inference/InferenceBanner.test.tsx src/components/inference/migration.test.ts src/components/BackupsPanel.test.tsx src/routes/ModelsView.test.tsx` from `frontend/`; expect missing text/partial scope and no follow-up read after the initial format-2/done response. Preservation cases remain green.
- [ ] **Step 4: Implement shared copy, compatible wire fields and polling.** Render archive preparation/copying through `migrationProgress`; let `migrationLine` expose retirement archive progress even when state is done. Keep failures and skipped-item details visible alongside any applicable progress. Label partial backup rows `Model settings safety archive (partial)`, compute full-backup emptiness separately from the list, and explain that original content or a full backup is needed; empty-folder restore instructions apply only to full archives with the app stopped. Extend `upgrading()` for activity/progress while preserving newer-format exclusion, newest-read-wins, timer cleanup and existing intervals.
- [ ] **Step 5: Run focused frontend verification and commit.** Run `npm run typecheck`, then `npx vitest run src/components/inference/InferenceBanner.test.tsx src/components/inference/migration.test.ts src/components/BackupsPanel.test.tsx src/routes/ModelsView.test.tsx src/routes/ProvidersView.test.tsx src/components/SceneInspector.test.tsx` from `frontend/`; expect all selected tests passing. Commit `fix: display settings archive progress and partial backup scope`.

### Task 4: Document the contract, run the gate and open a reviewable PR

**Files:** `CLAUDE.md`, `docs/store-guarantees.md`, current backup docstrings, any affected documentation guard assertions, and `docs/superpowers/validation/2026-10-14-migration-safety-backups-implementation.md` (new evidence/theory note). Ratchet baselines change only if findings decrease.

**Interfaces:** No further product interfaces. The implementation note explains the protected-file selection, ZIP publication boundary, activity versus migration state, restart/reuse behavior and concrete limits of partial recovery.

- [ ] **Step 1: Update current guarantees and validate them.** Replace current full-safety-archive claims with the protected scope, new names, never-pruned treatment, full-backup scheduling distinction, progress/activity and manual recovery limits. Retain family-prefix values required by `test_docs_guard.py`; keep historical designs untouched. Run `make test-py-fast TESTS="backend/tests/test_docs_guard.py backend/tests/test_backups_settings.py backend/tests/test_inference_migrate.py backend/tests/test_inference_retire.py" PY=<absolute-main-venv-python>`; expect all selected tests passing.
- [ ] **Step 2: Repeat the original disposable-store reproduction.** Verify neither images nor transcripts appear in either settings family, progress is observable during writes, failed archives publish nothing, retries finish and completed archives are reused. Record the exact commands and results, without any real-library observations.
- [ ] **Step 3: Run the full required gate.** Run `make check PY=<absolute-main-venv-python>` with the existing default workers. Inspect every target's result. Resolve real failures; use `make baseline` only for a reduced ratcheted count and commit the smaller baseline with its fix. Do not weaken guards, regenerate frozen `home/`, lower coverage floors or claim unavailable checks passed.
- [ ] **Step 4: Complete both implementation review gates.** Commit the current documentation and gate evidence first, so branch review sees the full change. Run `/codex:review --base main` on the complete branch diff, then `/codex:adversarial-review --base main` against that diff and approved spec, explicitly checking every spec requirement. Resolve or justify concrete findings and verify affected checks again. The Windows launcher issue may require providing exact source/diff input to read-only Codex review; record that limitation and its final assessment accurately.
- [ ] **Step 5: Commit, push and create the draft PR.** Read the fix-issue PR-writing reference, commit the documentation and verification evidence, verify a clean issue branch, push it and open a draft PR against main with `Fixes #496`, the behavioral change, recovery limitation and actual validation. Check CI and address any failures. Do not close or comment on the issue. Requesting the repository's Codex PR review is allowed by its review workflow; record and resolve any findings before calling the work complete.

## Plan self-review and handoff

All approved spec sections map to Tasks 1-4; the five review-focus conditions each have explicit regressions. Existing route dictionaries carry additive fields without a new endpoint or pydantic model. No new store modules, dependencies or run classes are needed. Execute sequentially because each task consumes the preceding interfaces. Native execution in this session is recommended: the archive, status and UI changes share one contract, with fresh whole-diff reviews at the end. Human plan review and execution-method selection remain required before implementation.
