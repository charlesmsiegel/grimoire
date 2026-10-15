# Migration safety archives implementation evidence

Issue: #496. Approved [spec](../specs/2026-10-14-migration-safety-backups-design.md)
and [plan](../plans/2026-10-14-migration-safety-backups.md).

Product commit: `d5c73b51722d13959da398afdca839e2c66f6775`. Both final
code reviews approved. Focused checks and the complete frontend gate passed;
the full Windows gate was interrupted by the runtime stall described below.
Completed backend/Android verification must come from PR CI; no local full-gate
pass or backend coverage result is claimed.

## Behavior and test evidence

- Task 1 red run: 24 failed, 1 passed; missing callback, overbroad membership,
  unsafe output exclusions and scope/schedule semantics were observed before code.
  The focused backup/store/routes suite then passed 99 tests, 1 skipped.
- Task 2 red run: all 17 selected progress regressions failed before code.
  A further cleanup-error regression proved status propagated an OSError; its
  read-only probe cleanup now suppresses that optional failure.
- Real process tests use absolute parent-resolved machine lock paths and stdin
  handshakes for release. Terminating a Windows venv launcher did not reliably
  stop its child interpreter; graceful release fixes the harness rather than
  weakening the stale-owner assertion.
- Task 3 red run: 5 failed, 97 passed, covering presentation, partial scope and
  format-2/done polling. The six focused UI files passed 360 tests and TypeScript
  checking passed. After the stale-poll/save regression, ModelsView passed 84.
- A further output-directory alias regression failed before the physical-path
  validation: an external symlink could disguise output inside connections.
  This extends the same approved fail-closed overlap rule; ordinary backup
  lexical traversal rules remain unchanged.
- Repeated original reproduction with only disposable placeholder stores:
  migration done; image copied false; unrelated transcript copied false;
  active true and valid safety_backup counters observed during ZIP writes.
  Injected archive interruption failed and retry completed. Partial ZIPs restart
  from the beginning as explicitly scoped; completed archives remain reusable.

## Verification commands

Windows worktree interpreter: `<main-checkout>/backend/.venv/Scripts/python.exe`.
All make commands pass that absolute path as `PY`; reproduction sets this
checkout's `backend/src` first on `PYTHONPATH`.

- `make test-py-fast TESTS="backend/tests/test_backups_settings.py backend/tests/test_backups_store.py backend/tests/test_backups_routes.py" PY=<above>`
- `make test-py-fast TESTS="backend/tests/test_docs_guard.py backend/tests/test_backups_settings.py backend/tests/test_inference_migrate.py backend/tests/test_inference_retire.py" ARGS="--maxfail=0" PY=<above>`
- From frontend: `npm run typecheck` and `npx vitest run` on InferenceBanner,
  migration, BackupsPanel, ModelsView, ProvidersView and SceneInspector tests.
- `make check PY=<above>`: attempted; target results and the Windows runtime
  stall are recorded below. A separate `make check-pydantic1 PY=<above>` was
  started after the stall; its final result is reported with PR CI status.

## Theory note

Theory: Migration recovery protects exactly the records migration can rewrite; live archive work has a separate lifetime from layout state.
Instead of: Full-library safety ZIPs and partial ZIP resumption; selected recovery material addresses the costly work while keeping atomic publication.
Reused: Existing ZIP writer/compression, atomic streaming publication, backup lock, root registry, status note and proclock.
New concept: A fresh archive ownership lease, with serialized observer probes, prevents stale foreign counters resurfacing.
Assumes: The library is live; selection is a coarse recovery copy rather than a transactional snapshot, as before.
Cost: Inventory memory and callbacks scale with protected files; intermediate note writes are throttled to four per second.
Watch: The scope must track future migration write paths; recovery may require manually removing files created after the archive.

## Execution rulings

Native execution was explicitly approved. Frontend test preparation overlapped
backend verification while implementation stayed within the approved interfaces.
No scope expansion, new dependency, restore endpoint or partial ZIP resume.


## Fresh whole-change review and rulings

The independent review found two Important defects, with no Critical or Minor
findings. Both were reproduced before correction:

1. A save returning active settings could invalidate an in-flight poll without
   rearming its timer. The new active-save deferred-poll test failed (2 reads
   instead of 3), then all 85 ModelsView tests passed after adding an install
   epoch. The quick/slow read counter and newest-view-wins behavior are retained.
2. A successful read-only activity probe could briefly hold the main lock and
   consume the background starter's sole acquisition attempt. Four startup
   regressions failed, plus a separate inside-lock root-boundary regression.
   `try_ensure` now returns None only for contention; existing `ensure` keeps
   its immediate status-returning contract. The background starter retries only
   None, outside maintenance exclusion, with stop and root checks. A real pass
   returning pending still stops. This is a narrow execution ruling adding
   `main.py` and a private acquisition seam to preserve the approved no-wait
   behavior; it does not change archive scope or public wire contracts.

The first complete gate stopped in check-web on the known active-save
regression (5246 UI tests passed, 1 failed); backend targets had not run.
The full gate was restarted after both fixes. Ratcheted lint/mypy/eslint
findings were at their committed baselines; none was raised or weakened.

The fresh reviewer then inspected both corrections and returned no remaining
Critical, Important or Minor findings; full-gate completion was still pending.

The final migration suite passed all 103 tests; the five startup-specific
regressions also passed independently. Full-gate completion was still pending at that point.


## Final focused checks and Codex implementation gate

- Combined scoped-backup, migration and retirement suites: **235 passed**
  across four workers (280.54s). Existing FastAPI/Starlette deprecation warnings.
- TypeScript plus all six focused UI files: **362 passed** (153.60s).
- Native `/codex:review --base main` thread
  `01a13dfb-8af3-75b2-9124-2589ca5363f3` could not read the checkout:
  both its Windows command and node_repl paths failed. No review approval is
  inferred from that attempt.
- The documented Windows fallback supplied the exact committed branch diff,
  approved spec, current conventions and complete relevant source to a fresh
  read-only Codex execution, without tools. It exited **0** and returned:
  **Approve: no material correctness, regression, concurrency or recovery defects.**
  It independently checked both race corrections and matched archive scope,
  publication, reuse, retention, scheduling and recovery to the approved spec.
- At that checkpoint, adversarial spec-conformance review and the full gate
  were still running; subsequent results follow.


The final `/codex:adversarial-review --base main` used the same exact-source
packet and approved spec (passed through the plugin in memory to avoid Windows
argument limits). Thread `01a13e03-600a-77a1-be51-f5fee498f4e9`, stored job
`review-mv93drav-fc4q66`, completed with **verdict: approve**, no material
findings, and the explicit next step to confirm the running full gate before
shipping. The PowerShell wrapper returned 1 with a Node deprecation warning;
the plugin's stored status was completed and its rendered verdict was approval.
The implementation and spec-conformance reviews are static assessments; they
are not substitutes for the full gate, whose result is recorded separately.


## Full-gate timeout investigation

The next full run stopped in `check-web`: two existing SceneReview tests
(`a swap clears that row's unanswered conflict` and `a conflict after an
untouched low row lands on its own row`) exceeded the existing 5-second limit;
eight later cases in the same file had cascading DOM failures. The run
reported 5237 passed, 10 failed and one unhandled error. All other 240 UI files
passed, including the migration regressions.

The entire unmodified SceneReview file then passed **182 tests** in isolation
(79.85s), including the two triggers in approximately 350ms. No unrelated
product/test fix was made. The full gate was rerun with the installed Vitest
worker controls `VITEST_MAX_FORKS=4`, `VITEST_MIN_FORKS=1`,
`VITEST_MAX_THREADS=4`, `VITEST_MIN_THREADS=1`, to bound this host's automatic
UI parallelism. The same complete suite and coverage checks run; backend
workers remain the default four. Final result follows below.

The unexpected failures in that timeout run were:

- `src/components/review/SceneReview.test.tsx > a swap clears that row's unanswered conflict`
- `src/components/review/SceneReview.test.tsx > a conflict after an untouched low row lands on its own row`
- `src/components/review/SceneReview.test.tsx > a conflict on the untouched low row itself is shown, and its drawer opens`
- `src/components/review/SceneReview.test.tsx > renaming the scene repoints alternatives, then swapping and saving sends the new scene`
- `src/components/review/SceneReview.test.tsx > a swap keeps the reviewer's edited beat`
- `src/components/review/SceneReview.test.tsx > un-rejecting a row whose record a swapped row now targets blocks the save`
- `src/components/review/SceneReview.test.tsx > a failed identity phase with no checked rows says only that the check could not run`
- `src/components/review/SceneReview.test.tsx > a degraded identity phase says why, even with semantic matching configured`
- `src/components/review/SceneReview.test.tsx > a semantic fallback is named even when a partial or failed resolver owns the reason`
- `src/components/review/SceneReview.test.tsx > basic matching is named when the possible-match lists came from basic matching`


## Bounded full-run result and Windows runtime stall

The bounded run completed these required targets:

- Ruff: 1136 findings, all at the committed baseline.
- Mypy: 161 findings, all at the committed baseline.
- Template verification: 551 checks passed.
- ESLint: 703 findings, all at the committed baseline.
- Frontend TypeScript and coverage: **241 files, 5247 tests passed** (564.91s).
  Statement coverage was **89.01%**. The unchanged SceneReview file passed
  all 182 tests within this complete run.

Backend coverage reached 96% test progress without reporting a test failure,
then stopped advancing. Two read-only CPU samples showed unchanged worker
counters. Temporary diagnostic tools, installed only in ignored task scratch,
identified the remaining worker in this stack:

```text
client fixture -> TestClient.__enter__ -> start_blocking_portal
  -> asyncio ProactorEventLoop initialization -> _make_self_pipe
  -> socket._fallback_socketpair -> lsock.accept()
```

Its current test was
`test_ledger_knowledge_routes.py::test_leaving_out_posts_is_not_the_whole_scene`
(setup), before app lifespan startup. The controller waited for worker results;
other workers waited for work. This establishes where the Windows run stalled,
without claiming a particular network or CPython root cause. No product code,
assertion, timeout or coverage floor was changed to work around it.

After capturing the stacks, only this worktree's verified pytest process tree
was terminated. `make check` consequently exited **2**, with no completed
backend coverage report; it did not reach `check-pydantic1`. The latter target
was then started independently with the same worktree interpreter.

Execution ruling: publish the reviewable draft with this limitation explicit
and finish whole-backend verification through the repository's normal CI jobs
(Python 3.11 and 3.14, Android dependency set, plus APK). All final product code
is unchanged from the approved reviews. CI status and the separate local
compatibility result are reported in the PR rather than inferred from the
partial local run. No review findings are deferred.
