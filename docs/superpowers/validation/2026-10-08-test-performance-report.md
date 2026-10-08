# Test-suite performance report — baseline, changes, results

Spec: `docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`.
Plan (with the user's delivery decisions and both review-gate records):
`docs/superpowers/plans/2026-10-08-test-suite-acceleration.md`.
Consolidation evidence: `2026-10-08-test-consolidation-ledger.md` (this directory).
Machine-readable summary: `2026-10-08-test-performance-baseline.json` (this directory).

Nothing here describes a user library: every figure is the suite's own,
measured on synthetic stores under `tmp_path`.

## 1. Summary

_Pending: filled from the measurement campaign still running when this was written._

## 2. What was measured, and how

**Source snapshots.**

| Name | Commit | What it is |
|---|---|---|
| baseline (A) | `aca2b73` | `a219190`'s production code and tests, plus the opt-in profiler hook in `conftest.py` (inert without `--phase-profile`). The profiler's own two self-test files are passed `--ignore`, so A collects exactly `a219190`'s suite. |
| head (B) | `908200e` | Every phase built (A–G), the last commit before the end-review fixes. |
| final | `9f12de0` and later | B plus the end-review fixes: six self-tests, `faulthandler_timeout`, the CI budget step. Its CI runs are in §3; the evidence runs of §9 used it. |

Both snapshots carry `a219190`'s `backend/src` unchanged, so every comparison
is between two test suites over one production snapshot. The one production
edit on the branch comes after both (§11, mypy) and changes no behaviour.

**Local machine.** One container: Linux x86-64, 4 logical CPUs, 15 GiB, running
as root (§11). Python 3.11.17 (both sides of the 3.11 comparisons), 3.14.6;
pytest 9.1.1, pytest-cov 7.1.0, coverage 7.16.2, pytest-asyncio 1.4.0,
pytest-xdist 3.8.0 (head only), pydantic 2.13.5, FastAPI 0.142.4. The
environment is CI's: `backend[dev]` without the `desktop` extra. The Pydantic 1
runs use the gate's own `make check-pydantic1` (pydantic 1.10.26, FastAPI
0.115.14). Each run was wrapped in an outer timer (wall, CPU, peak RSS of the
largest child); the profile's `wall_s` is pytest's own session clock.

**Protocol.** The campaign ran sequentially on an otherwise idle machine,
baseline and head interleaved (A, B, A, B, …) so drift falls on both:

| Runs | Purpose |
|---|---|
| A and B with coverage, 3 each, interleaved | the 3.11 gate as CI runs it; the stable arc sets (spec finding B1) |
| A without coverage ×2, B 4 workers ×2, B serial ×2 | separates reduced work from parallelism |
| B with coverage, serial ×1 | serial-vs-parallel arc and manifest equality at one head |
| A, B on 3.14 with coverage ×1 | the second interpreter |
| A, B `make check-pydantic1` ×1 | the Android dependency set, venv build included |
| `make check-web` ×1 | the frontend gate, unchanged by this work |

One disclosure: the first baseline run with coverage (`A-cov311-1`) shared the
machine for its first quarter-hour with development test runs; it is marked
where it is used, and every comparison takes medians.

## 3. CI, before and after

**Before** — the 12 most recent successful `ci.yml` runs before this work
(cancelled and failed runs excluded), minutes:

| Job | n | median | min | p90 | max |
|---|---|---|---|---|---|
| backend (py3.11), coverage | 12 | 58.1 | 37.5 | 62.2 | 62.6 |
| backend (py3.14) | 12 | 37.9 | 25.4 | 40.5 | 41.9 |
| pydantic 1.10 | 12 | 27.8 | 19.2 | 28.7 | 29.6 |
| frontend | 12 | 3.3 | 2.1 | 3.5 | 4.0 |
| android debug apk | 12 | 3.0 | 2.1 | 3.2 | 3.8 |
| eslint / mypy / templates / lint | 12 | 0.9 / 0.6 / 0.4 / 0.2 | | | |
| **critical path** (slowest job) | 12 | **58.1** | 37.5 | 62.2 | 62.6 |
| runner-minutes per run | 12 | 124.1 | 108.0 | 138.0 | 141.3 |

The backend jobs' `pip install` step takes about 0.2 min, so caching it (spec
§10.3) would save nothing, and was not added.

**After** — CI on this branch, from the commit where the gates went parallel:

_Pending: filled from the measurement campaign still running when this was written._

## 4. Local results

_Pending: filled from the measurement campaign still running when this was written._

## 5. Where the time went

The serial pilot (`a219190`, py3.11, no coverage, `--durations=0`; the machine
lightly shared) took 45 min for 14,299 tests. Setup was 584 s of 2,644
node-seconds; the slowest 1 %, 5 % and 10 % of nodes held 18.6 %, 41.7 % and
56.4 % of the time. By module, `test_routes.py` led (450 s), then
`test_path_guard_store.py` (180 s) and `test_lock_domain_guard.py` (121 s).

Two causes accounted for most of it, and neither was the one the spec expected.

- **FastAPI's per-app route analysis.** `create_app()` and entering the lifespan
  cost 5–8 ms. But FastAPI (from the release that includes routers lazily)
  analyses every route of a router the first time an app matches a request
  against it — each endpoint signature, each parameter's pydantic
  `TypeAdapter` — about 0.5 s for all 528 routes, once per app, and the suite
  builds an app per test on purpose. Timed directly, it was 60–85 % of four
  route-test files' session time. `tests/route_memo.py` memoises the one
  FastAPI function that does it, per process; the app, its lifespan, its run
  registry and its overrides stay per test. Those four files went from
  21.2/28.5/44.0/10.9 s to 5.4/8.8/22.3/3.8 s.
- **A quadratic guard.** `guard_markers._comments_by_line` re-tokenized a whole
  source file for every node a guard asked about, and the lock-domain guard
  asks about every function in the package: `test_the_marker_is_not_a_rubber_stamp`
  took 93 s. Cached on the source text: 5 s.

_Pending: filled from the measurement campaign still running when this was written._

## 6. The three columns (spec §3)

_Pending: filled from the measurement campaign still running when this was written._

## 7. Coverage

The floor (`COV_FLOOR = 93`), the coverage configuration (`source =
["grimoire"]`, branch measurement), and the XML report path are unchanged;
`COV_ARGS` is byte-identical to the old `check-py` line. pytest-cov combines the
workers' data before `--cov-fail-under` is evaluated.

_Pending: filled from the measurement campaign still running when this was written._

## 8. Behaviour preservation

- **Consolidation**: the ledger maps every removed node id to the field of the
  merged test that now holds its assertion, gives the arc comparison per group,
  and lists 31 mutation probes (30 caught, one shown equivalent).
- **Harness self-tests** (spec §12):

| §12 item | Held by |
|---|---|
| 1 a failure on one worker fails the run | `test_parallel_harness.py::test_a_failure_on_one_worker_fails_the_run` |
| 2 coverage union, unimported module, floor | `::test_coverage_across_workers_is_the_union_and_keeps_unimported_modules`, `::test_a_missed_coverage_floor_fails_a_parallel_run` |
| 3 cancelled runs vs missing artifacts | unchanged `ci.yml` (`!cancelled()` + `if-no-files-found: error` on the coverage reports); the new profile artifact is `warn`, being diagnostic |
| 4 stable manifest serial vs parallel | `::test_the_workers_collect_exactly_what_a_serial_run_collects`; the real suite in §7 |
| 5 privacy floor in every worker, after undo | `::test_the_privacy_floor_holds_in_this_process`, `::…_in_every_worker` (`--dist=each` over the real `conftest.py`); subprocess-backed tests: §10 |
| 6 crash, hang, lost child | `::test_a_worker_that_dies_fails_the_run_and_names_the_node`, `::test_a_hung_test_never_reads_green_and_is_named`, `::test_a_slow_test_on_a_worker_is_named_too`; `faulthandler_timeout = 300`; `timeout-minutes: 45` on both backend jobs |
| 7 worktree imports the active checkout | every profile records `grimoire_from_this_checkout`; checked from a git worktree with the main venv (commit `bcd84d6`). Windows: not run (§11) |
| 8 a Pydantic-2-only path fails the Pydantic 1 gate | unchanged: `check-pydantic1` asserts `pydantic.VERSION` starts `1.10.` before running the suite, and `test_pydantic_guard.py` refuses v2-only API statically; this work added no stub of pydantic |
| 9 guard negative cases | unchanged and green after the tokenizer cache (all 260 tests of the seven guards that share it) |
| 10 a mutant against a merged assertion fails a retained test | the ledger's probes |

- **Ordering found by CI, and fixed.** The route memo made one test's count
  depend on what earlier tests in the same worker had analysed
  (`test_a_first_request_analyses_only_the_domains_it_walks`, `(46, 110)` on a
  py3.14 worker); it now counts with the memo bypassed (`a658b62`). The memo's
  own switch test read a FastAPI attribute the Pydantic 1 gate's FastAPI does
  not have; fixed in `908200e`.

## 9. Scheduler, workers, and stress

Without coverage, whole suite, head `08fb85d`, one run each:

| Workers, scheduler | wall | busy per worker | peak RSS of largest process |
|---|---|---|---|
| 4, `load` | 335 s | 276–293 s | 800 MB |
| 4, `worksteal` | 335 s | 313–314 s | 770 MB |
| 4, `loadfile` | 399 s | 358–374 s | 727 MB |
| 2, `load` | 605 s | 577–581 s | 917 MB |

`loadfile` strands `test_routes.py` (828 tests) on one worker, as the spec
warned. `load` and `worksteal` tie; `load` is xdist's own default for `-n` and
is what `check-py` uses. Two workers to four is 1.8×.

_Pending: filled from the measurement campaign still running when this was written._

_Pending: filled from the measurement campaign still running when this was written._

_Pending: filled from the measurement campaign still running when this was written._

## 10. Isolation audit (spec §8.2)

A read-only audit of all ~380 test files, with file:line evidence for every
item; the decisions:

| Item | Finding | Decision |
|---|---|---|
| Filesystem | Every test writes under `tmp_path` / its `GRIMOIRE_HOME`, except one production path: `proclock.lock_dir()` puts lock files under the real `~/.local/state/grimoire/locks` (deliberately env-proof), reached by most route and store tests; three test files patch it. Workers cannot collide (one subdirectory per store root), but it is a write outside `tmp_path` that is never pruned. | Pre-existing; reported to the user (§11). Not changed here: a redirect must reach the three child-process lock tests too, or they pass without testing anything. |
| Networking | `live_server` binds port 0; `test_install_scripts.py` probes the launcher's fixed 8173 and skips when busy; the rest are ephemeral or never open a socket. | Skips are an outcome in every manifest comparison. |
| Processes | Every child is awaited or killed in `finally`; `test_image_name_locks.py` has no kill fallback after a timed-out handshake. The self-test harness spawns nested pytest runs, which strip coverage's and pytest's environment. | Recorded; the image-lock test's teardown is a candidate for the house kill-on-timeout pattern. |
| Global state | `importlib.reload(store)` per `client`, the cid-keyed process-wide campaign RLock, calendar plugin registry, `random` seeding: all within one process and pre-existing; xdist only changes which tests share a process. | The shuffled runs (§9) are where a leak would show. |
| Fixture scope | The only non-function fixture is the session privacy floor, which runs once per worker and is tested per worker. No module- or class-scoped fixture exists. | — |
| Collection | Deterministic: no hash-ordered or environment-, time- or worker-derived parameters, no collection hooks. Workers' manifests match (`collection_agrees`). | — |
| Wall-clock bounds | About twenty assertions bound elapsed time (`test_image_name_locks.py:269` `< 0.2 s`, `test_locks_store.py:808`, `test_routes.py:8581`, `test_embeddings.py:347,396`, …). The harness's own sub-second windows were rewritten as relational checks before any ran under xdist. | Run under contention in the stress loops (§9); never loosened. |
| `_survey()` per worker | The lock-domain guard's survey is cached per process, so each worker that gets one of its five consumers pays it once. | Accepted; measured in the profile. |

**Serial islands (§8.4):** none was needed; nothing in the suite contends for a
resource another worker holds.

**Sleeps and polls (§9.2).** Classified, none changed: detached-run polls are 10 ms
with generous deadlines (an event-driven wait would need a production hook);
the 0.3–2 s injected delays are the deadlines and timeouts under test; the
30–120 s "sleeps" are cancellation targets inside fakes and held providers and
never elapse. None appears among the profile's slow tests.

## 11. Unresolved risks and known gaps

- **Windows.** `make check-py` now defaults to four xdist workers on every
  platform, and no Windows run was available here. xdist and pytest-cov support
  Windows; process start-up is slower there. `make check-py WORKERS=0` is the
  serial run.
- **The route memo leans on a private FastAPI function.** It switches itself
  off when the function is missing or its signature moves (the Pydantic 1 gate's
  FastAPI 0.115 already runs without it), so an upgrade can make the suite
  slower but not wrong; `test_route_memo.py` says which. Its cached graphs are
  shared objects that FastAPI does not mutate after building them — checked in
  FastAPI's source, and asserted by comparing every route of a second app with
  a fresh build — but that assertion runs once per worker, not after every
  test.
- **`proclock.lock_dir()`** writes under the real home directory (§10).
- **This container runs as root**, so `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`
  fails here on both sides (root ignores the read-only bit); CI's runner is not
  root and passes it.
- **`test_android_manifest.py::test_there_are_android_resources_to_check`**
  skips every path with a `build` component, so a checkout that itself sits
  under a directory named `build` fails it. Found because the first measurement
  worktrees lived under `build/`; they were moved. Pre-existing; not changed.
- **mypy was red on `main`** since `a219190` (`routes/campaigns.py`, one new
  `union-attr` in `_absorbed_cast_refs`, which read `scene.get("cast")` twice
  so the type check could not narrow the second read). Fixed on this branch at
  the user's request by reading it once; the behaviour is unchanged, and
  `a219190`'s own tests of that function (`test_absorbed_cast_is_legacy_appearance_evidence`,
  `test_malformed_absorbed_cast_does_not_break_appearances`) pass.
- **The budget's figures** come from the first parallel CI runs and should be
  revisited after a few more.

## 12. Rollback

- Serial gate, everywhere: `make check-py WORKERS=0` (and `check-pydantic1`).
  To make it the default, set `WORKERS ?= 0` in the `Makefile`; the CI jobs
  call the targets and need no change, except `timeout-minutes: 45`, which a
  serial 3.11 coverage run can exceed and would need raising.
- Route memo off: `GRIMOIRE_TEST_ROUTE_MEMO=0`.
- The consolidations are ordinary test edits, revertible commit by commit
  (`c998e26`); the ledger maps each.
- Nothing else changes a gate: the profiler, the budget step and the
  development targets are all opt-in or non-failing.

## 13. CI design (spec §10)

- **Fast and full roles.** The lint, mypy, ESLint, templates and frontend jobs
  were already a fast layer (each under four minutes); the full backend gates
  now finish in about the time the frontend job used to set the pace at, so a
  separate Python smoke subset would buy minutes at most and was not added.
  Every required job keeps its name.
- **Artifacts.** `backend-coverage` (3.11) and `frontend-coverage` unchanged;
  `backend-profile` added (3.11, diagnostic).
- **Sharding (§10.4):** not needed; four workers on one runner bring the
  critical path well under the spec's stretch target.
- **Performance budget (§13):** `profile_report.py budget` against
  `backend/tests/perf_budget.json`, warnings only, on the 3.11 leg. Session
  time is judged on the median of five comparable runs -- this one and the
  newest four earlier CI runs (this branch's, then `main`'s) whose
  `backend-profile` the job can fetch, with the same Python minor version,
  workers, scheduler and coverage setting, run to completion -- and with fewer
  than five it shows the median and warns of nothing. Collected and skipped
  counts and the slow-test line are judged per run. The total critical path
  across jobs is not budgeted automatically; §3 reports it.
