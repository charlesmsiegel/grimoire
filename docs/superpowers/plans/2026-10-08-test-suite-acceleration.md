# Test-suite acceleration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shorter *trustworthy* feedback loops — the local edit → verdict loop and the CI critical path — without dropping a behaviour, an environment, a coverage arc or a gate.

**Spec:** `docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md` (the spec section numbers below are its §).

**Delivery shape (user decision, 2026-10-08):** one branch, `claude/jolly-rubin-verkak`, carrying **one PR at a time**. Each PR is opened, driven to green and merged before the branch is restarted from `main` for the next. So this plan details **PR A** fully and gives every later PR its scope, its exit evidence and the measurements it consumes; each later PR gets a short addendum to this file (its task list, written against the tree as it then stands) and its own plan gate before it is implemented, because `main` moves under this program and a task list written today for PR F would be stale by then.

**Gate record:** the Codex CLI is not installed in this container and the user chose not to install it (2026-10-08). Every Codex checkpoint in `CLAUDE.md` is therefore stood in for by an independent Claude adversarial review, and each PR body says so in plain words rather than claiming a Codex review ran. The spec → plan stand-in review ran against the spec at `a219190`; its findings are folded in under *Spec review findings* below.

## Global constraints (every PR)

- **No production semantics change** (§0.4). A testability change to `backend/src/` needs its own behavioural argument in the PR body.
- **Never weaken a gate** (§0.5, §0.7, §15): no `COV_FLOOR` change, no coverage-config change, no dropped Python version, Pydantic 1 run, APK, lint ratchet, template check, Istanbul/`all: true` or DOM-settling rule; no timeout raised, no retry, `skip`, `xfail` or deletion to make something faster or greener.
- **Privacy** (`CLAUDE.md`): synthetic stores under `tmp_path` only; no real `~/.grimoire`; the profile JSON carries node IDs and seconds, never paths outside the repo, response bodies, prompts or fixture contents. No real LLM call. `frozen_campaign/home/` and `fixtures/inference_baseline.json` are never regenerated.
- **Measured, not asserted.** Every speed claim cites runs from the same machine, same SHA family, same flags; reported as distributions (n, median, min, max) with the machine named. A lower collected count is never evidence of speed.
- **Three columns** in every PR's results (§3): *reduced work*, *parallelism*, *selection*.
- **Worktree-safe imports**: every new command puts the active checkout's `backend/src` first on `PYTHONPATH` (the `WITH_SRC` idiom in the `Makefile`), on both the Unix and the cmd.exe branch.
- **Doc guards** (finding S4): a `.venv/bin/python` written in CLAUDE.md, README.md, `evals/README.md` or the frozen-campaign README needs its `.venv/Scripts/python` twin within four lines (`test_install_scripts.py`); Makefile recipes stay one line (cmd.exe); every new `check-*` target is named in `CONTRIBUTING.md`; no new document may share a >16-word run with another maintained document (`test_docs_guard.py::MAX_SHARED_RUN`); a new file named `test_*guard*.py` would have to join CONTRIBUTING's guard table, so the harness self-tests are **not** named that way.
- **Linear history**, fast-forward merges, no merge commits.

## Baseline facts this plan rests on

Recorded at `a219190` before any change. The full numbers, the machine and the commands are in `docs/superpowers/validation/2026-10-08-test-performance-baseline.md` (written by PR A); the headline ones that decide the ordering of this plan:

- **CI (12 most recent successful `ci.yml` runs, cancelled and failed excluded):** the critical path *is* the py3.11 backend job — median 58.1 min (min 37.5, p90 62.2, max 62.6). py3.14 backend median 37.9; Pydantic 1.10 (py3.11, **no coverage**) median 27.8; frontend 3.3; APK 3.0; the other four under 1 min each. Median runner-minutes per run 124. The backend jobs' `pip install` step is ~0.2 min — caching it buys nothing (§10.3 answered: do not add it).
- The gap between the two py3.11 jobs (coverage on vs off, ~30 min) is far larger than the "about 8 minutes" the `Makefile` comment on `COV_FLOOR` states. Coverage on 3.11 runs coverage.py's C tracer (`sys.settrace`); on 3.14 coverage.py can use `sys.monitoring` with branch measurement, which is a plausible reason py3.14 is faster *with* coverage than 3.11. PR A measures this locally instead of inferring it; if it holds, it is the single largest lever in the program, and it is a **policy decision for the user** (see *Decisions the user owns*), not something a PR does quietly.

## Decisions the user owns (asked, never assumed)

1. **Which interpreter measures the authoritative coverage.** Today: 3.11 (the `ci.yml` comment: the floor version's reachable set is the smaller one). Option: measure on 3.14 and run 3.11 without coverage. Only raised with PR A's measurements in hand, and only adopted if the 3.11 and 3.14 arc sets are shown to be compared honestly (never conflated, §4.4).
2. **Required-check names**, if a CI rollout (PR G/H) would rename or split a job that branch protection names.
3. **Any serial island** (§8.4), each one named.

## Spec review findings folded in

The stand-in review (a Claude reviewer reading the spec against `a219190`, no edits) confirmed the spec's repo facts (file counts and sizes, `COV_FLOOR`, the `check-pydantic1` wipe, the `client`/`live_server` shapes, `_survey`'s `functools.cache`) and that all 19 test names cited in C1–C8 exist, with the shared setup and action true for C1–C5, C7 and C8. Its findings, and what this plan does about each:

| # | Finding (evidence) | Disposition |
|---|---|---|
| B1 | "No lost covered arc" against **one** run is unmeetable: some production arcs are timing-dependent (the activity fold's second-boundary straddle, `test_routes.py:14651-14660`, #320/#314; persist retry loops; held threaded runs) and will come and go between runs, more so under xdist. | Adopted. PR A records the arc set of **three** serial coverage runs; the **stable set** is their intersection and the **variable set** is union − intersection. Every later comparison is `stable(after) ⊇ stable(before)`, with the variable set listed rather than chased. `coverage_arcs.py` grows a `stable` command. |
| S1 | C6 is 40 state builds, not 32 (`test_the_routing_bundle_reports_what_the_resolver_serves`, `test_inference_resolve.py:610`), and `resolve` is not read-only: `ensure_migrated()` writes in the `pre_connections` state (`resolve.py:346`). | Adopted into PR E: C6 Option A is ruled out for `pre_connections` unless the batched helpers are shown to see the store in the same order; Option B is the default. |
| S2 | Test names are pinned elsewhere: `test_capstone_acceptance.py:116-128` (capstone spec Appendix B) and `test_docs_guard.py:456-473`; `test_observability_routes.py:173` imports helpers from `tests.test_routes`. | Adopted: the PR D/E ledger checks every old node ID against both guards before a rename or merge; splitting `test_routes.py` (if ever) keeps those imports working. |
| S3 | A non-gate `check-py-parallel` breaks `CONTRIBUTING.md`'s "make check runs every target in that table except check-apk" or forces a third suite run into `check:`. | Adopted: the opt-in parallel command is **`test-py-parallel`** (not `check-*`); only PR G, when `check-py` itself adopts xdist, touches gate prose. |
| S4 | More doc guards: `test_install_scripts.py:359-384` (a `.venv/bin/python` needs its `.venv/Scripts/python` twin within 4 lines in CLAUDE.md/README.md/…), `MAX_SHARED_RUN`, single-line recipes for cmd.exe (`Makefile:127-129`). | Adopted into the global constraints. |
| S5 | xdist hazards the spec's audit list misses: wall-clock upper bounds (`test_image_name_locks.py:269`, `test_proclock.py:170,217`, `test_embeddings.py:347,396`, `test_locks_store.py:808,1151`, `test_routes.py:8581`); `test_install_scripts.py:302-305` binds the launcher's fixed port 8173 and skips when busy; `proclock.lock_dir()` writes under the real `~/.local/state/grimoire/locks` (pre-existing, deliberately env-proof, `test_proclock.py:85-93`); `_survey()`'s per-process cache would be recomputed by every worker under `load`/`worksteal`. | Adopted into PR C's audit as named items, each with a decision recorded before the measurement: the timing bounds are run under contention in the stress pass and any that flake are reported, never loosened; skips are an outcome in every manifest comparison; the lock directory is raised with the user as a pre-existing isolation gap (lock files only, no store content); the guard module's scheduling is measured under `loadgroup`/`loadscope` as well as `load`/`worksteal`. |
| S6 | `.coverage.*` (xdist, `--cov-context`, shards) is not git-ignored. | Adopted into PR A (the arc audit already writes them). |
| S7 | The harness self-tests have no placement rule: `check-py` runs `pytest backend`, so any `test_*.py` joins the gate under coverage; `pytester` is not enabled; nested coverage conflicts; §12.3/§12.6 are not pytest-testable. | Adopted: self-tests run the synthetic suite as a **subprocess** over files written into `tmp_path` (never collected by the gate), with `COV_*`/`COVERAGE_*` stripped from its environment; they belong to the gate. §12.3 and §12.6 are verified by named CI evidence in PR G, not by pytest. |
| S8 | Job wall time on hosted runners (37.5–62.2 min for one job) is too noisy for "≥35 % median" from three runs. | Adopted: the primary metric is the profile's in-process `wall_s` (pytest session time) on one machine, n ≥ 3 per configuration; CI job times are reported as a distribution beside it, with n, and never alone. |
| N1 | #314 is a load-sensitive clock straddle, not an order dependence. | Noted; PR C's stress pass is where it would resurface. |
| N2 | C5, C7, C8, C9 are store-level and likely milliseconds. | Adopted: ranked after C1–C4 and C6 by measured seconds. |
| N3 | Siblings to classify: `test_routes.py:4800` (C3's reroll plus `guidance`, class D) and `test_usage_routes.py:760` (overlaps C4 from another file). | Adopted into the PR D ledger. |
| N4 | pytest-cov 7 does not measure subprocesses, so spawned children contribute no arcs today; enabling it would move the denominator. | Noted: out of scope (a denominator change, §1 non-goal). |
| N5 | Every xdist worker re-imports every test module (`test_routes.py` is 835 KB) and `store` is reloaded in many places. | Adopted: PR C records collection time and RSS per worker. |
| N6 | The session privacy floor uses `tmp_path_factory`, which is per worker. | Confirmed; PR C's self-test still proves it per worker. |

Recorded beside these, from the first profile (one file, `test_continuity_reconcile_routes.py`): the `client` fixture's own setup (reload + `create_app()` + lifespan) is 5–8 ms, and the slow tests spend their time in the call -- the absorb run they wait on. App startup is not the hotspot the spec feared; PR A's whole-suite profile decides what is.

---

## PR A — Baseline and profiler (detailed)

**Category:** none of the three columns — this PR changes no test's cost. It adds the instruments and the numbers every later PR is measured against.

### Files

| File | Responsibility |
|---|---|
| `backend/tests/phase_profile.py` (new) | The opt-in pytest plugin: per-node, per-phase seconds; exclusive fixture-setup seconds; counts of expensive operations; xdist-safe (controller writes, workers only annotate reports). Standard library only. |
| `backend/tests/conftest.py` (modify) | `--phase-profile=PATH` option; registers the plugin only when given. Nothing else changes. |
| `scripts/test_profile.py` (new) | Stdlib CLI over the profile JSON: `summary` (top-N per phase, slowest modules, fixture totals, op counts, concentration), `manifest` (sorted node IDs), `compare A B` (manifest diff + outcome changes + timing delta). |
| `scripts/coverage_arcs.py` (new) | Reads a coverage data file through `coverage.CoverageData`, writes the executed **arc and line sets** per production file (repo-relative, every `.py` under `backend/src/grimoire` present, zero-covered ones included); `stable A B C…` writes the intersection and lists the variable remainder (finding B1); `diff BEFORE AFTER` exits non-zero when AFTER lost an arc, line or file BEFORE had. |
| `.gitignore` (modify) | `.coverage.*` beside `.coverage` (finding S6). |
| `backend/tests/test_phase_profile.py` (new) | Self-tests of the plugin against a tiny synthetic suite written into `tmp_path` and run as a **subprocess** with coverage's environment stripped (serial now; `-n 2` once PR C adds xdist): one record per node, three phases, outcome, deterministic serialization, nothing written without the flag, no stdout without the flag, no absolute paths in the output. |
| `backend/tests/test_profile_scripts.py` (new) | Self-tests of the two scripts on synthetic inputs: ranking, concentration arithmetic, manifest diff, arc diff detects a lost arc and a lost zero-coverage file. |
| `docs/superpowers/validation/2026-10-08-test-performance-baseline.md` (new) | Human-readable baseline (§4, §13.1). |
| `docs/superpowers/validation/2026-10-08-test-performance-baseline.json` (new) | Compact machine-readable summary (no per-test bulk; that stays in `build/perf/`, ignored). |
| `docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md` (new) | The spec, verbatim as handed over. |
| this file (new) | The plan. |

No `Makefile`, `ci.yml` or `pyproject.toml` change in PR A — PR B owns the `Makefile` targets, PR C the dependency. Until then the profile is driven by its documented pytest flag.

### Task A1 — the profiler plugin, test first

- [ ] Write `backend/tests/test_phase_profile.py` against a subprocess-run synthetic suite (files written into `tmp_path`, so the gate never collects them; `COV_*`/`COVERAGE_*` stripped from the child's environment; a `conftest.py` there registers the plugin through the same two functions `backend/tests/conftest.py` calls) of four synthetic tests (pass, fail, skip, a test with a function-scoped fixture that sleeps a known minimum) and assert on the JSON: `schema_version == 1`; keys `git_sha`, `python`, `coverage`, `workers`, `distribution`, `wall_s`, `collect_s`, `tests`; one entry per node ID with `setup_s`, `call_s`, `teardown_s`, `outcome`; the sleeping fixture's exclusive setup time is attributed to its name and is ≥ the sleep; the skipped test has `outcome == "skipped"` and no `call_s`; serialization is byte-identical across two writes of the same data (sorted keys, fixed rounding); without `--phase-profile` nothing is written and the terminal output is identical to a run without the plugin loaded; no value in the file is an absolute path.
- [ ] Run it, watch it fail (no plugin).
- [ ] Implement `phase_profile.py`: a class with `pytest_runtest_logreport` (controller side: accumulate `report.duration` by `report.when`; record `outcome` from call, or from setup when setup failed/skipped), a `pytest_fixture_setup` hookwrapper timing the fixture function exclusively and stashing `{argname: seconds}` on the requesting item, a `pytest_runtest_makereport` hookwrapper copying that dict onto the **setup** report as a plain attribute (pytest's `_report_to_json` copies `report.__dict__`, so it crosses the xdist wire), op counters, `pytest_sessionfinish` writing the file **only where `config` has no `workerinput`**.
- [ ] Op counters (§4.3): counted by wrapping class-level entry points so a name imported by value elsewhere is still seen — `fastapi.FastAPI.__init__` (an app built: `create_app` and every ad-hoc `FastAPI()`), `starlette.testclient.TestClient.__enter__` (a lifespan entered), `importlib.reload` (the module attribute every caller reaches), `uvicorn.Server.startup`, `subprocess.Popen.__init__`. Installed in `pytest_configure` only when profiling, restored in `pytest_unconfigure`; per-test deltas attached to the call report. Documented as approximate: a test that builds a `FastAPI()` for a sub-app counts as an app.
- [ ] Register in `conftest.py` through two functions the module exports, `add_option(parser)` and `register(config)`, so the gate's conftest and the self-test's synthetic conftest wire it identically: `--phase-profile` (default `None`), registered only when set. Re-run the self-tests until green.
- [ ] Commit: `test(perf): an opt-in per-phase profile of the backend suite`.

### Task A2 — the report CLI and the arc tools, test first

- [ ] `test_profile_scripts.py`: `summary` on a synthetic profile ranks by total/call/setup/teardown, groups by module (the part of the node ID before `::`), computes the share of total time held by the slowest 1/5/10 % of nodes, sums fixture seconds and op counts; `manifest` is the sorted node IDs; `compare` reports missing, added and outcome-changed nodes and exits non-zero on a missing node; `coverage_arcs.dump` on a hand-built `CoverageData` (written to `tmp_path`) emits repo-relative files, includes a source file with no data as empty, and `diff` exits non-zero on a lost arc, a lost line, and a lost file.
- [ ] Implement the two scripts (stdlib + `coverage`, which the `dev` extra already brings through `pytest-cov`), each importing from module scope, mirroring `ratchet.py`'s `sys.path` import pattern in the tests.
- [ ] Commit: `test(perf): summary, manifest and arc-set tools for the profile`.

### Task A3 — the baseline runs

All on this container (Linux, 4 logical CPUs, 15 GiB; recorded with `platform`, `os.cpu_count()`, package versions). Each run's raw output stays in `build/perf/` (git-ignored). Commands (repo root, `PYTHONPATH=$PWD/backend/src`):

- [ ] **serial, py3.11, no coverage** ×3: `python -m pytest backend -q --phase-profile=build/perf/<label>.json`
- [ ] **serial, py3.11, coverage exactly as `check-py`** ×3 (the `make check-py` command line plus `--phase-profile`), each run's `.coverage` dumped → `coverage_arcs.py stable` over the three: the stable set is the reference, the variable set is published (finding B1).
- [ ] **serial, py3.14, coverage** ×1 (uv-provisioned 3.14; its arc set dumped separately and never merged with 3.11's).
- [ ] **Pydantic 1.10, no coverage** ×1, through `make check-pydantic1 PY=…` so the environment is the gate's own; venv build time and test time recorded apart.
- [ ] **frontend** `make check-web` ×1 and `npx vitest run` (no coverage) ×1, from `frontend/`.
- [ ] **APK**: not buildable here without `make android-bootstrap` (an SDK download); the CI distribution above stands for it and the doc says so.
- [ ] **coverage overhead split**: on py3.11 with coverage, one extra run with `COVERAGE_CORE=sysmon` is *not* possible (3.11 has no `sys.monitoring`); on py3.14 one run with `COVERAGE_CORE=ctrace` vs the default, to attribute the 3.11/3.14 gap to the tracer rather than the interpreter.
- [ ] Repeatability: report n, median, min, max of the profile's in-process `wall_s` per configuration (the primary metric, finding S8); flag any test whose outcome differs between runs (a flake is a finding, recorded, never retried away).

Pre-existing failures on `main` in this environment are recorded with their node IDs and cause, not fixed in this PR unless the fix is a test-isolation defect squarely in scope.

### Task A4 — the baseline document

- [ ] `…-test-performance-baseline.md`: environment; commands; the CI distribution table; local distributions; coverage overhead; top-100 call/setup/teardown/total (as a link-free table in the doc only for the top 30, the rest in the JSON); slowest 30 modules; fixture totals; op counts; concentration; the arc-set fingerprint (SHA-256 of the canonical arc dump, plus line/arc/file counts) for py3.11 and py3.14 separately; the hotspot list ranked for PRs D–F; pre-existing failures.
- [ ] `…-test-performance-baseline.json`: the same summary, machine-readable, schema-versioned.
- [ ] Privacy read of both files before commit: no absolute paths outside the repo, no store contents, no names but the placeholder set.
- [ ] Commit: `docs(perf): the serial baseline at a219190`.

### PR A exit evidence

`make check` targets that PR A touches pass locally (`check-py`, `check-lint`, `check-mypy`, `check-templates`); full CI green on the PR head; the self-tests prove the plugin's contract; the baseline doc names its SHA, machine and n per configuration.

---

## PR B — Fast developer commands (outline)

**Category:** selection (local only) — never a gate.

`Makefile` targets, each one line on both the Unix and cmd.exe branches with `WITH_SRC`, none named `check-*` (so nothing can mistake them for a gate, and the docs guard's target enumeration is untouched): `test-py-fast` (`TESTS=…`, `-x --tb=short -p no:cacheprovider` off, no coverage, prints `NOT A GATE: coverage off, selection only` first), `test-py-failed` (`--lf`, warns when `.pytest_cache` has no last-failed record), `test-py-profile` (serial, `--phase-profile`, optional `COV=1` reusing the `check-py` coverage arguments verbatim). `CONTRIBUTING.md` gains one short subsection pointing at them (no prose shared with `CLAUDE.md`). Exit evidence: run from the main checkout and from a `git worktree` with `PY=` pointing at the main venv, proving by a planted `backend/src` difference that the worktree's source is the one imported; a Windows smoke is recorded as not run if no Windows host is available.

## PR C — pytest-xdist, opt-in (outline)

**Category:** parallelism.

`pytest-xdist` (bounded, verified installable on 3.11, 3.14 and in the Pydantic 1 venv) added to the `dev` extra only. `Makefile`: `WORKERS ?= 0` and `DIST ?= load` threaded into a new **`test-py-parallel`** (not `check-*`: finding S3) whose coverage arguments are byte-identical to `check-py`'s; `WORKERS=0` runs without `-n`. Collection time and RSS recorded per worker (N5). Isolation audit (§8.2, plus finding S5's named items) recorded per item with file:line evidence, defects fixed at the test that leaks. Measurements: full suite at 2 and 4 workers × `loadfile`/`load`/`worksteal`, with and without coverage, three runs for the leading candidates; serial-vs-parallel **manifest equality** and **arc-set equality** (`coverage_arcs.py diff` both ways); peak RSS; per-worker busy time from the profile. Harness self-tests (§12 items 1, 2, 4, 5): a failing test on one worker fails the run; pytest-cov's combined data still lists zero-covered modules and still trips `--cov-fail-under`; the privacy floor holds in every worker. `check-py` and CI are unchanged in this PR.

## PR D — Consolidation: routes and continuity (outline)

**Category:** reduced work.

C1–C4 (§7), plus the siblings N3 names classified, each only after reading the current tests; every old node ID checked against `test_capstone_acceptance.py` and `test_docs_guard.py`'s cited-test lists before it is merged or renamed (S2); and confirming the shared setup and action are really identical. Per candidate: the contract matrix (§6 step 4), the old→new node-ID map, per-test arc diff from a `--cov-context=test` audit run (no lost arc), a targeted mutation probe per merged assertion (applied in a scratch copy, never committed) showing the merged test still fails, and the measured seconds saved on the same machine.

## PR E — Consolidation: context, inference baseline, parsers (outline)

**Category:** reduced work (C5, C6, C8) and case-count only (C7, C9 — reported as such, with their real seconds).

Same evidence as PR D. C6 covers **five** parametrized tests (40 builds, S1); its frozen `inference_baseline.json` is never regenerated; Option A is ruled out for `pre_connections` (`ensure_migrated()` writes) unless shown order-safe, so Option B is the default.

## PR F — Fixture and wait-time optimization (outline)

**Category:** reduced work. Only the hotspots PR A's profile ranks; each change with a no-state-leak test. Real sleeps and polls classified per §9.2.

## PR G — Authoritative parallel CI (outline)

**Category:** parallelism. `check-py` adopts the measured `WORKERS`/`DIST`, keeping a one-variable serial rollback (`WORKERS=0`); CI jobs keep their names; coverage XML and artifact names unchanged; arc-set equality with the serial reference shown on the PR head. Includes the Makefile coverage-cost comment corrected to the measured figure.

## PR H — Sharding (only if G is insufficient) · PR I — Frontend (secondary)

Not planned until G's numbers are in.
