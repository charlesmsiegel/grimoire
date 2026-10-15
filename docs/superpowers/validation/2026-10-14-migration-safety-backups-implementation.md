# Migration safety archives implementation evidence

Issue: #496. Approved [spec](../specs/2026-10-14-migration-safety-backups-design.md)
and [plan](../plans/2026-10-14-migration-safety-backups.md).

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
- `make check PY=<above>`: running; final results and review gates recorded below.

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
regressions also passed independently. Full-gate completion remains pending.
