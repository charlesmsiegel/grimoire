# Test-suite performance report — baseline, changes, results

Spec: `docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`.
Plan (with the user's delivery decisions and both review-gate records):
`docs/superpowers/plans/2026-10-08-test-suite-acceleration.md`.
Consolidation evidence: `2026-10-08-test-consolidation-ledger.md` (this directory).
Machine-readable summary: `2026-10-08-test-performance-baseline.json` (this directory).

Nothing here describes a user library: every figure is the suite's own,
measured on synthetic stores under `tmp_path`.

## 1. Summary

| | before | after | |
|---|---|---|---|
| CI critical path, median (n) | 58.1 min (12) | 11.5 min (5) | −80 % |
| CI critical path, worst seen | 62.6 min | 12.1 min | |
| backend py3.11 job, median | 58.1 min | 11.5 min | |
| runner-minutes per CI run, median | 124.1 | 37.8 (4 complete runs) | −70 % |
| local 3.11 gate (coverage), session median | 4,557 s, serial | 550 s, 4 workers | 8.3× |
| collected tests | 14,299 | 14,275 at B (14,292 today) | every removed id mapped |
| combined coverage | 93.69–93.71 % | 93.69–93.71 % | floor 93 unchanged |

Where it came from, on one machine (§6): the same work done cheaper (the route
memo, the tokenizer cache, the consolidations) makes a serial run 2.3× faster
and uses 60 % less CPU; four workers then make it 3.6× faster again. Nothing
selects fewer tests: every gate still runs every one.

What still holds: the consolidations lost no arc a serial run executes (B's
serial arc set is byte-identical to a baseline run's, §7); every removed node
id maps to a field of a merged test (ledger), and 30 of 31 mutants against
merged assertions go red (the 31st is equivalent); no test changed outcome
between A and B, nor between serial, parallel, `worksteal`, shuffled and
stress runs (§9). Two things CI found under parallel load were fixed rather
than retried (§8): a memo-dependent count and a timing-ratio test. One branch
that only a serial run's thread interleaving reached is now asserted
deterministically (§7).

Spec success criteria, against the measurements: critical path ≥35 % median
reduction — met (−80 %), and inside the 10–15 min stretch band; p90 —
improved (62.2 → 12.1 worst seen); focused loop ≤1–2 min — met
(`make test-py-fast` on one file: 3–7 s); identities accounted for — met
(ledger, §8); coverage sets — met serially, explained in parallel (§7);
determinism — no unexplained flake (§9); compatibility — Pydantic 1, 3.14,
Android, ratchets and Istanbul unchanged and green; Windows — not run (§11).

## 2. What was measured, and how

**Source snapshots.**

| Name | Commit | What it is |
|---|---|---|
| baseline (A) | `aca2b73` | `a219190`'s production code and tests, plus the opt-in profiler hook in `conftest.py` (inert without `--phase-profile`). The profiler's own two self-test files are passed `--ignore`, so A collects exactly `a219190`'s suite. |
| head (B) | `9f12de0` | Every phase built (A–G) plus the end-review fixes: the last commit whose tests the campaign measured. |
| later | `487b280` … | The Codex-review fixes to the profiling tools, the linearity-test fix, the mypy fix and one added test (§8, §11). They change no timing figure measured here; their CI runs are in §3. |

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
baseline and head interleaved so drift falls on both. The planned campaign
was cut short when the container was reclaimed during an idle stretch (only
`A-cov311-1` had finished); it was relaunched in a shorter form, and what it
dropped is said below rather than left out:

| Runs | Purpose |
|---|---|
| A with coverage, serial ×2 | the 3.11 gate as CI ran it before; A's stable arc set |
| B with coverage, 4 workers `load` ×3 | the 3.11 gate as CI runs it now; B's stable arc set |
| B with coverage, serial ×1 | separates reduced work from parallelism; serial-vs-parallel arcs and manifest |
| B with coverage, 4 workers `worksteal` ×2 | the scheduler choice, with coverage on |
| B without coverage, 4 workers ×2 | the coverage tracing overhead |
| 11 concurrency-heavy files, 4 workers, ×10 | the stress loops |
| B shuffled (pytest-randomly), 4 workers × seeds 1234, 98765; serial × seed 1234 | order dependence |

Dropped from the relaunch, and covered instead by CI, where the runner is the
one that matters: the 3.14 and Pydantic 1 comparisons (CI's 12 runs before and
5 after, §3), and `make check-web` (no frontend file changed). The baseline
without coverage is the serial pilot of §5 (one run). `A-cov311-1` shared the
machine with development runs for its first quarter-hour; it landed within 1 %
of the uncontended `A-cov311-2` (4,578 s against 4,537 s), so it is used.

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

**After** — every CI run on this branch that ran the full jobs since the gates
went parallel (`fa3ff2e`), minutes. Runs cancelled by a newer push are not
listed. Two were red on a single test each, fixed in the next commit (§8);
their jobs still ran to the end, so their times are real:

| Head | py3.11 | py3.14 | Pydantic 1 | frontend | APK | critical path | runner-min |
|---|---|---|---|---|---|---|---|
| `fa3ff2e` | 10.9 | 7.5 ✗ | 11.1 ✗ | 3.3 | 3.0 | 11.1 | — |
| `908200e` | 12.0 | 7.4 | 10.5 | 2.6 | 9.7 | 12.0 | 44.1 |
| `ebeaeeb` | 11.5 | 7.9 | 11.5 | 3.3 | 2.3 | 11.5 | 38.3 |
| `487b280` | 12.1 ✗ | 5.3 | 11.4 | 3.1 | 3.3 | 12.1 | 37.3 |
| `ef26889` | 8.0 | 7.5 | 10.9 | 3.2 | 2.6 | 10.9 | 34.1 |
| **median (n=5)** | **11.5** | **7.5** | **11.1** | 3.2 | 3.0 | **11.5** | **37.8** (n=4) |

✗: `fa3ff2e`'s two were the memo's own test bugs (fixed in `a658b62`,
`908200e`); `487b280`'s was the linearity test (fixed in `22072a1`). The
mypy job was red on every run until `ef26889` for a reason outside this work
(§11); `ef26889` is green on all nine jobs.

The critical path is now the Pydantic 1 job, not 3.11: its pinned FastAPI
0.115 has no seam for the route memo (§11), so it runs the whole suite
without it. The APK's 9.7 on `908200e` is that job's own variance (Gradle);
nothing here touches it. Five runs is a small sample on shared runners; the
before/after gap (58.1 against at most 12.1) is far outside its spread.

## 4. Local results

pytest's own session clock (`wall_s`), seconds; the outer timer agrees to
within 0.2–1.5 %. CPU is user + system of the whole process tree; RSS is the
peak of the largest single process.

| Configuration | n | median | min | max | CPU | peak RSS |
|---|---|---|---|---|---|---|
| A serial, coverage | 2 | 4,557 | 4,537 | 4,578 | 4,282–4,322 | 1,255–1,349 MB |
| B serial, coverage | 1 | 1,972 | | | 1,716 | 1,247 MB |
| B 4 workers `load`, coverage | 3 | 550 | 543 | 556 | 1,577–1,715 | 796–843 MB |
| B 4 workers `worksteal`, coverage | 2 | 536 | 514 | 559 | 1,563–1,697 | 779–790 MB |
| B 4 workers `load`, no coverage | 2 | 327 | 322 | 332 | 965–1,025 | 731–743 MB |
| B 4 workers, shuffled, no coverage | 2 | 356 | 345 | 367 | | |
| B serial, shuffled, no coverage | 1 | 1,198 | | | | |
| A serial, no coverage (pilot, `a219190`) | 1 | ~2,700 | | | | |

Four workers mean five processes (the controller and four workers), each near
800 MB at its peak, against one process near 1.3 GB serially: about 3.2 GB
peak in all, well inside a hosted runner's 16 GB. Coverage tracing costs 1.68×
under four workers (550 against 327 s), the same factor as serially at A
(4,557 against the pilot's ~2,700).

Every run reported the same single failure, `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`,
which fails here because the container runs as root (§11) and passes on CI.

A focused change's loop, `make test-py-fast TESTS=<one file>`: 3.4 s for
`test_search_store.py` (40 tests), 7.3 s for `test_prompt_log_routes.py` (23
route tests), make's overhead included.

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

**After**, the same file ranking on the same machine (serial, coverage on, A
against B; node-seconds): `test_routes.py` 768 → 246, `test_path_guard_store.py`
289 → 57, `test_lock_domain_guard.py` 249 → 92, `test_inference_resolve.py`
102 → 34. Setup, which the per-app route analysis had dominated, is now
420 of B's 1,908 serial node-seconds; the slowest 1 %, 5 % and 10 % of nodes
hold 29 %, 49 % and 61 % — more concentrated than before, because the broad
per-test cost went away and the few genuinely slow tests remain:

- the lock-domain guard's whole-package survey (78 s serially, 120–125 s on a
  contended CI worker), paid once per worker by whichever of its five
  consumers a worker reaches first;
- `test_weather_draw.py::test_weight_fidelity_over_independent_zones[0.9]`
  (56 s), a statistical test sized for its confidence bound;
- the parallel-harness self-tests (71 s serially, 33 s across workers), which
  start real pytest and xdist sessions as child processes on purpose (§8).

Under four workers each worker was busy 404–460 s of a 550 s session; the
rest is worker start-up, collection (13 s), the coverage combine and report
at the end, and the tail of the last worker.

One more cost the profile shows, and that this work leaves in place: every
test gets a `tmp_path` (an autouse fixture asks for one), and pytest numbers
each new directory by scanning every entry already in the session's base
directory, so the cost grows with the run: serially 172 s of B's run, the
last thousand tests paying 15× what the first thousand did; across four
workers, each with its own base directory, about 13 s of each worker's time.
Overriding a core pytest fixture to avoid it was judged not worth its risk
here; it is the next measured lever for the serial run (§11).

## 6. The three columns (spec §3)

The spec asks that a speed-up say how much came from doing less work, how much
from parallelism and how much from selecting fewer tests. Measured on one
machine, coverage on, session medians:

| Column | From → to | Factor | Evidence |
|---|---|---|---|
| **Reduced work** | A serial 4,557 s → B serial 1,972 s | **2.31×** | CPU 4,302 → 1,716 s (−60 %); same arcs (§7) |
| **Parallelism** | B serial 1,972 s → B 4 workers 550 s | **3.59×** | 90 % of the ideal 4×; CPU unchanged (1,716 → 1,577–1,715 s) |
| **Selection** | — | **1×** | every gate runs every test; selection exists only in the `test-py-*` development targets, which say they are not gates |
| **Gate, end to end** | A serial 4,557 s → B 4 workers 550 s | **8.3×** | |

Reduced work, broken down by where it went (serial node-seconds, A → B):
route-analysis memo, mostly the route files' setup (`test_routes.py` alone
768 → 246); the tokenizer cache, the AST guards (`test_path_guard_store.py`
289 → 57, the lock-domain guard 249 → 92); the consolidations, at most 77 s by
the §6 inventory's own upper bound, the merged tests a few seconds of it.

## 7. Coverage

The floor (`COV_FLOOR = 93`), the coverage configuration (`source =
["grimoire"]`, branch measurement), and the XML report path are unchanged;
`COV_ARGS` is byte-identical to the old `check-py` line. pytest-cov combines the
workers' data before `--cov-fail-under` is evaluated.

**Totals.** A 93.69 / 93.71 %; B serial 93.71 %; B four workers 93.69–93.70 %
(five runs); CI at `487b280` 93.70 %.

**Arc sets** (`scripts/coverage_arcs.py`, every package file listed, each
file's source hash checked, stable set = the intersection of a configuration's
runs, spec finding B1):

| Set | runs | arcs | lines | variable arcs |
|---|---|---|---|---|
| A, serial | 2 | 85,624 | 57,804 | 16 (3 files) |
| B, serial | 1 | 85,640 | 57,813 | — |
| B, 4 workers `load` | 3 | 85,622 | 57,803 | 10 (4 files) |
| B, all six runs | 6 | 85,621 | 57,803 | 23 (5 files) |

- **A stable → B serial: nothing lost.** B's serial set is byte-identical to
  `A-cov311-2`'s (the same SHA-256 over every file's lines and arcs). The
  consolidations, the memo and the cache changed which tests run and how
  often, and not one production arc a serial run executes.
- **B serial → B in parallel: three arcs are not reached in parallel**, and a
  serial run with per-test contexts (`--cov-context=test`, read straight from
  coverage's database) says by whom:
  - `routes/common.py` 489→490→488, `_record_prompt`'s skip when another
    holder has the campaign lock. Reached only inside
    `test_routes.py::test_concurrent_accept_vs_manual_check_distinct_entries`,
    when two concurrent requests happen to interleave that way — in every
    serial run and in no parallel one. Nothing asserted it. It is a real
    contract (a capture on the generating path never waits, and a skipped
    capture stamps no write token), so `test_prompt_log_routes.py::test_a_contended_capture_records_nothing_stamps_nothing_and_never_waits`
    now holds it deterministically, executes both arcs whatever the
    scheduler, and goes red with the early return removed (`af81ea5`).
  - `llm.py` 288→exit, `_settle`'s grace wait ending by cancellation.
    Recorded in the **teardown** of
    `test_routes.py::test_the_ceiling_does_not_wait_for_the_cancellation_it_requests`:
    the event loop closing a `_settle` still in its grace period as the test's
    app shuts down. Whether that happens depends on how far the abandoned
    pull got before teardown; no test asserts it, and asserting a shutdown
    artefact would pin timing, not behaviour. Left as is.
  - The rest of the variable arcs (`routes/observability.py`'s heartbeat
    branch, `store/logs.py`'s empty tail, `routes/tracker.py`'s
    cancelled-update path) vary between A's own two serial runs as well:
    elapsed-time and shutdown branches.
- **Manifests.** Every B run, serial, parallel, `worksteal`, shuffled,
  collected the same 14,275 node ids with the same outcomes
  (`profile_report.py compare`, exit 0 each); A → B is §8's ledger.

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
- **A timing ratio found by CI, and fixed.** `test_search_store.py::test_the_query_parser_is_linear_in_the_word_count`
  read 3.4× for twice the words (bound 3) on a four-worker run with the parser
  unchanged: it timed all small samples, then all large ones, so a burst of
  load from a neighbouring worker could land on one size only. The samples now
  alternate and the sizes are 4× apart, bound 8 (the geometric mean of linear
  ~4 and quadratic ~16). Not loosened: linear measured 4.1–5.2 under the stress
  loops' load, and restoring the quadratic dedupe reads 19.2× (`22072a1`).
- **A branch only a serial run's thread interleaving reached** now has a
  deterministic test (§7, `af81ea5`).

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

**With coverage, at B:** `load` ×3 549.7 s median (542.8–555.5), `worksteal` ×2
514.1 and 558.5. The difference is inside the spread of either; `load` stays,
being xdist's default and the scheduler the self-tests prove the coverage
union under. Busy time per worker under `load`: 404–460 s.

**Stress loops:** the eleven files with the most threads, locks, detached runs
and wall-clock bounds (`test_runs_detach`, `test_review_detach`,
`test_locks_store`, `test_proclock`, `test_image_name_locks`, `test_llm`,
`test_maintenance_runs`, `test_runner`, `test_embeddings`, `test_draft_runs`,
`test_turn_follow_ups`; 418 tests), four workers, ten times back to back:
4,180 of 4,180 passed, 17–18 s per loop.

**Shuffled order** (pytest-randomly, test order and the `random` seed both
shuffled): seed 1234 and 98765 under four workers, seed 1234 serially. All
three: the same 14,265 passed, the same 9 skipped and the same root-only
failure as every ordered run. No order dependence surfaced.

**Under CI's load,** two tests did fail once each, and each was a measurement
rather than a behaviour (§8); neither was retried away.

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
- **The budget's figures** come from the first parallel CI runs; the session
  check judges a five-run median, so it only becomes active after five
  comparable runs exist.
- **The Pydantic 1 job is the critical path now** (10.5–11.5 min): its FastAPI
  0.115 has no seam for the route memo, so the analysis the memo removes
  elsewhere still runs there per app. A memo for that FastAPI's own seam, or a
  fourth worker's worth of headroom there, is the next lever on the critical
  path.
- **The lock-domain guard's survey** is the slowest single test (78 s serially,
  120–125 s on a contended CI worker; the budget's slow-test line is 150 s
  because of it). It is per process by design; making it cheaper is a guard
  change, not a test-runner one.
- **`tmp_path` numbering is quadratic in the session** (§5): 172 s of a serial
  run, about 13 s per worker in parallel.
- **`llm.py` 288→exit** is reached only by an event-loop teardown, serially
  (§7); nothing asserts it, deliberately.

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
