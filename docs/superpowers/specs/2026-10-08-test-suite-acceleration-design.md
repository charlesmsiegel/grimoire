# Grimoire Test-Suite Acceleration and Consolidation

**Implementation specification and handoff for Claude Code**  
**Status:** Proposed; analysis/design only; no repository changes made while preparing this specification  
**Prepared:** 2026-10-08  
**Repository:** https://github.com/charlesmsiegel/grimoire  
**Reference main SHA inspected:** `a219190a2c3cebfe89dcf13eaf6c641a75391d7d`  
**Suggested eventual repository location:** `docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`

> **Directive to Claude Code:** The objective is **shorter trustworthy feedback loops**, not fewer green checkmarks. Optimize actual wall-clock time while preserving fault detection, coverage, compatibility, isolation, and the project's established review gates. Execute incrementally; measure before and after; do not silently relax checks. Re-read the current repository before implementing this design because `main` changes frequently. Any specific candidate identified below is a *review target*, not permission to delete assertions.

---

## 0. Instructions for the implementing agent

1. **Read `AGENTS.md`, all of `CLAUDE.md`, `CONTRIBUTING.md`, and `docs/store-guarantees.md` before editing.** `CLAUDE.md` is authoritative. Keep its mandatory **spec → plan → implementation → completed diff** Codex review checkpoints (`/codex:adversarial-review`, `/codex:review`) unless the user explicitly authorizes an exception. Use the existing project workflows for branches, PRs, and linear Git history; do not create merge commits.
2. Treat this as an **engineering program of small, independently reviewable PRs**, not a single opaque rewrite of thousands of tests. Produce a concrete implementation plan after the required adversarial review and before editing. Each PR must include reproducible measurements and meaningful tests of the test harness itself.
3. **Preserve privacy.** Do not read or profile an actual user data library or put any of its paths, character names, campaign contents, keys, transcripts, or statistics in code, artifacts, docs, or CI logs. Use the repository's existing synthetic names (Realm, Saltmarch, Mara, Winifred, Seraphine) and `tmp_path` isolation. Do not run real LLM calls. Do not regenerate `backend/tests/fixtures/frozen_campaign/home/`.
4. **Do not change production application semantics** as part of test acceleration. If profiling discovers a production performance defect, document it separately. Only make production-code changes when necessary for *testability*, with explicit behavioral review and independent evidence of unchanged app behavior.
5. **Never replace the authoritative full suite with selected tests** without an explicit, separately approved policy change. A fast developer check is supplemental. Preserve Python 3.11, Python 3.14, Pydantic 1.10 compatibility, frontend typecheck/tests/coverage, lint/mypy/ESLint baselines, template verification, and the Android APK check.
6. Before applying examples below, inspect the current version of each cited test. A recent refactor may have moved or superseded it. Record the actual Git SHA being optimized.
7. Report failures instead of making them disappear: do not increase timeouts, suppress flakes with retries, weaken fixtures, raise coverage exclusions, add `xfail`/`skip`, or delete tests merely because they are slow. Changes of that sort need independently justified defect fixes.
8. When there is a tradeoff between a modest speedup and reliable behavioral coverage, choose reliability. Keep a serial reference runner and an easy rollback switch for any new parallel runner.

## 1. Goal, success criteria, and scope

### Primary goals

- Make the typical **local edit → relevant tests → useful verdict** loop substantially shorter.
- Shorten the **CI critical path**—especially Python backend tests—without dropping behaviors currently protected by tests.
- **Eliminate repeated expensive fixture construction, HTTP requests, full generation/reconciliation executions, and repeated scans** when one well-structured test can check all the same outcomes.
- Run independent tests concurrently using `pytest-xdist`; only add cross-runner sharding if needed after measuring.
- Make the suite easier to profile and maintain so that new tests cannot gradually recreate the problem.
- Preserve understandable failure messages and explicit coverage of historical regressions.

### Success criteria (proposed; validate against baseline)

| Dimension | Minimum acceptance | Target / stretch |
|---|---|---|
| Full CI critical path | ≥35% reduction in **median** wall time against matched baseline; no meaningful regression in p90 | Median ≤20 min; stretch ≤10–15 min if supported by evidence |
| Local focused loop | A supported one-command way to run just relevant backend/frontend tests **without coverage** | Typical focused change gets feedback in ≤1–2 min, depending on selected tests |
| Full backend suite | All collected test identities accounted for; deliberate consolidation ledger for removed identities | Parallel run significantly faster than serial on same hardware |
| Coverage | No regression in executed production statement/branch **sets** for test-only consolidation on a fixed source snapshot; existing combined branch coverage and `COV_FLOOR=93` retained | More useful per-test / per-area diagnostics without always-on instrumentation |
| Fault detection | Every behavioral invariant retained; targeted mutants/regression probes still fail | Equal or stronger fault detection with fewer expensive executions |
| Determinism | Repeatability comparable to or better than serial, with zero unexplained new flakes | Stable across Linux CI, Python 3.11/3.14/Pydantic 1; Windows development smoke |
| Compatibility | Android/Pydantic 1, snapshot fixtures, lint ratchets, frontend Istanbul coverage, CI artifacts intact | Faster but equivalent required gates |
| Total resource cost | Report runner-minutes, peak memory, process count, and artifact overhead as well as wall time | Avoid large cost increases merely to make a dashboard turn green sooner |

**Do not present the targets above as measured outcomes.** They are objectives. The acceptance report must replace them with actual distributions, machine details, and confidence/limitations.

### Explicit non-goals

- Reducing the 93% coverage threshold or changing its denominator.
- Removing the second Python version or Pydantic 1 compatibility run by default.
- Replacing `pytest-cov`'s real source/branch measurement with a less honest metric.
- Globally making the `client` fixture session-scoped, sharing mutable campaigns between tests, or bypassing the FastAPI lifespan.
- Disabling concurrency/locking/background-run tests because they are expensive.
- Switching the frontend from Istanbul to V8, dropping `coverage.all`, or disabling frontend settling rules.
- Switching tests to a real LLM or network provider for performance characterization.
- Building an elaborate test-impact prediction system before basic profiling, xdist, and duplication savings are measured.

## 2. Repository baseline and why this matters

The inspected `main` tree has roughly **377 backend files named `test_*.py`** (plus test helpers) and **194 frontend `.test.ts/.test.tsx` files**. `backend/tests/test_routes.py` is about **835 KB** and has more than 800 explicit test functions; `test_context.py`, `test_lock_domain_guard.py`, and `test_absorb_store.py` are also unusually large. These facts matter for fixture cost and for xdist's `loadfile` scheduler.

The full backend run has grown to **about 14,000+ collected cases**; the exact count is a moving target and must be recaptured. Example successful GitHub Actions runs show:

| CI workflow run | Python 3.11 backend | Python 3.14 backend | Pydantic 1 | Frontend | APK |
|---|---:|---:|---:|---:|---:|
| [37719630577](https://github.com/charlesmsiegel/grimoire/actions/runs/37719630577) | 37.5 min | 35.5 min | 29.6 min | 3.4 min | 3.0 min |
| [37733037397](https://github.com/charlesmsiegel/grimoire/actions/runs/37733037397) | 62.2 min | 36.4 min | 20.3 min | 3.3 min | 3.0 min |
| [37703035430](https://github.com/charlesmsiegel/grimoire/actions/runs/37703035430) | 40.3 min | 33.8 min | 25.4 min | 3.4 min | 3.1 min |

Jobs already run concurrently, so the full CI critical path is set by the slowest Python job. Existing `concurrency.cancel-in-progress: true` already avoids allowing old PR pushes to finish needlessly, but means a long-running job often never yields a verdict before the next push.

Current implementation details to respect:

- `.github/workflows/ci.yml` runs separate backend **3.11 and 3.14** jobs, each invoking `make check-py PY=python`; a **Pydantic 1.10** job invokes `make check-pydantic1 PY=python` and runs the full backend suite again; other jobs cover frontend, lint, mypy, ESLint, templates, APK.
- `Makefile` `check-py` measures source `grimoire`, with branch coverage, `COV_FLOOR ?= 93`, text and XML output to `backend/coverage.xml`. Its comments estimate that coverage instrumentation adds **approximately eight minutes** on Python 3.11. `check-pydantic1` runs without coverage but destroys/recreates `build/venv-pydantic1` on every invocation.
- `backend/pyproject.toml` has `asyncio_mode="auto"`, `testpaths=["tests"]`, and `pythonpath=["src"]`; development dependencies include pytest, pytest-asyncio, pytest-cov, Ruff, and mypy, but **not pytest-xdist** at the inspected SHA.
- `backend/tests/conftest.py` has autouse privacy isolation fixtures, tracker/reconcile opt-in defaults, and a `client` fixture which changes `GRIMOIRE_HOME`, reloads `grimoire.store`, constructs a new FastAPI app, injects a fake provider, and enters a real `TestClient` lifespan. A separate `live_server` fixture starts Uvicorn on **port zero** for genuine streaming/concurrency tests.
- `CLAUDE.md` and `CONTRIBUTING.md` require correct `PYTHONPATH` inside worktrees so tests do not import the editable install pointing at another checkout; frontend Vitest must run **from `frontend/`**.
- The frozen-campaign fixture and architectural AST guards are intentional invariants, not cruft. The 3 lint gates use strict baselines and may require a deliberate `make baseline` update when a finding disappears.

### Baseline caveats

Job duration != pure test time; capture both. Recent CI results vary markedly between Python versions and runs, so a **single run is insufficient** to prove a speedup. Profiling below must distinguish repeatable expensive test work from runner contention, filesystem/cache effects, and occasional delays. Never infer that tests named similarly are redundant without inspecting assertions and mutation behavior.

## 3. Architecture: three independent kinds of optimization

Maintain three separate result columns in all reports:

1. **Reduced work:** fewer app startups, fixtures, I/O operations, generation attempts, sweeps, compilations, repeated parameterized setup, and redundant test executions.
2. **Parallelism:** equal total work executed concurrently on worker processes or separate CI runners. This reduces wall time but might increase memory/CPU and Actions consumption.
3. **Selection:** run a relevant *subset* during development for rapid feedback while preserving an authoritative complete gate. Selection alone is **not** equivalent full verification.

Each PR should label its improvements by these categories. Do not claim a test deletion saved time merely because the collected case count decreased.

### Dependency order

```text
Read rules / capture clean main SHA
            |
Baseline: collection, timing, phase timings, coverage sets
            |
    +-------+----------------+------------------+
    |                        |                  |
Local fast commands   Consolidation audit   xdist isolation experiment
    |                        |                  |
    |                 Safe test merges    Stable parallel implementation
    |                        |                  |
    +---------------+--------+------------------+
                    |
          Optimize costly fixtures
                    |
         CI rollout and guard design
                    |
      Optional sharding, optional smarter selection
                    |
         Sustained metrics / maintenance
```

The profiling work is a prerequisite for claiming speedups, but cheap local commands, static consolidation analysis, and xdist experiments may be implemented on **separate branches or worktrees** once baseline measurements are captured. Avoid overlapping edits to `conftest.py`, `pyproject.toml`, `Makefile`, or the CI workflow without explicit coordination.

## 4. Phase 0 — Measure and freeze a reproducible baseline

**Deliverables:** machine-readable `test-performance-baseline.json`, a human-readable `docs/.../test-performance-baseline.md`, a coverage-set reference, and a list of test identities. Generated bulk raw data should live in an ignored local directory or CI artifact; commit only compact privacy-safe summaries, not machine-specific noise.

### 4.1 Record the environment

For each measurement, record:

- Git SHA, branch/PR, OS, CPU logical/available cores, memory, Python and package versions, Pydantic version, pytest and pytest-cov versions, GitHub runner class, and workflow run ID.
- Exact command, flags, `PYTHONPATH`, worker count, scheduler, coverage enabled/disabled, warm vs cold dependency cache, wall time, CPU time if practical, max RSS, test counts, skips, errors, and warnings.
- Which test data/root directory was used (only that it is synthetic/temporary; don't publish private or personal absolute paths).
- Time spent in collection, per-test **setup/call/teardown**, coverage postprocessing, and reporting.

Capture reference data for: serial full backend on 3.11 **with and without** coverage; serial backend on 3.14 with coverage; Pydantic 1 without coverage; frontend current gate; APK; full CI end-to-end. Use at least three controlled comparable local/CI runs of the important baseline; prefer five when feasible. Include a CI distribution over recent successful runs, excluding cancelled and failed attempts.

### 4.2 Profiling interface

Use pytest's built-in `--durations=100 --durations-min=0` for a first pass. If that isn't sufficient, implement a small, opt-in, **standard-library-only** pytest reporting plugin/hook using `pytest_runtest_logreport` (or equivalent). It should output JSON with one record per node ID and phase:

```json
{
  "schema_version": 1,
  "git_sha": "...",
  "python": "3.11.x",
  "coverage": false,
  "workers": 0,
  "distribution": "serial",
  "tests": {
    "tests/test_example.py::test_thing": {
      "setup_s": 0.002,
      "call_s": 0.017,
      "teardown_s": 0.004,
      "outcome": "passed"
    }
  }
}
```

Design constraints: no additional hidden fixtures; opt in through a flag/environment variable; deterministic serialization; stable node IDs; separate worker/controller handling; no stdout noise in normal runs; never capture response bodies, LLM prompts, fixture contents, or private paths. For xdist, the controller must collect worker phase reports rather than workers racing to write the same file. Test this behavior with a tiny synthetic test suite.

### 4.3 Aggregate useful diagnostics

Produce top-100 **individual call, setup, teardown and total** durations; top 30 slowest modules; total fixture-cost by fixture name (when reliably measurable); number of calls to `create_app()`, `importlib.reload(store)`, real `TestClient`, Uvicorn startup, `subprocess.run`, filesystem copies, and replay evaluation, if safely measurable; concentration (e.g. share of total wall time among slowest 1%, 5%, and 10% of cases). Attribute expensive work to the responsible suite instead of assuming that a big source file is necessarily slow.

Special scrutiny: `test_routes.py`; `test_context.py`; `test_absorb_store.py`; `test_lock_domain_guard.py` (AST scanning); timeout/lock/subprocess suites; continuity reconcile routes; inference baseline tests; image migration and bundle tests. Do not remove a single test as part of this phase.

### 4.4 Baseline coverage proof

For a fixed production source snapshot, record the **union of executed production lines and branches/arcs** from a complete serial run on Python 3.11, using coverage's raw data (and a stable source-path mapping), plus the displayed percentage and XML report. Include files measured with zero coverage; preserve `source=["grimoire"]` and branch measurement. If need be, also capture a separate Python 3.14 set—never conflate arcs from different interpreters.

To analyze individual test overlap, take a **separate audit run** with `--cov-context=test`. Do **not** turn test contexts on permanently in CI: per-test context measurement itself can be costly. Prefer coverage.py's `CoverageData` API (`measured_files()`, `arcs()`, `lines()`/`contexts_by_lineno()`, `set_query_contexts()`) to fragile text parsing.

Do not compare raw coverage percentages alone: replacing two tests with one can maintain the percentage while losing a distinctive behavior. Nor does identical branch/line coverage prove identical *assertion strength*. That is addressed in Phase 3.

**Exit gate:** baseline collected, timings and raw coverage traceable to an exact SHA, repeatability characterized, and prioritized hotspot list published.

## 5. Phase 1 — Fast local developer and agent commands (without weakening CI)

**Goal:** a reliable command for each iteration scale, preserving the existing full `make check` unless deliberately changed with updated documentation and tests.

Provide at least these ergonomics (exact target names may be adjusted to conventions after reviewing `test_docs_guard.py`):

| Proposed interface | Behavior | Coverage | Gate? |
|---|---|---|---|
| `make test-py-fast PY=... TESTS=...` | Runs explicit paths/node IDs / `-k` selection, short traceback, stop on first failure | Off | Development only |
| `make test-py-failed PY=...` | Runs pytest last failures (`--lf`), with a clear warning if no failures are recorded | Off | Development only |
| `make test-py-profile PY=...` | Serial profiling report on selected tests or whole backend | Configurable | Diagnostic only |
| `make check-py` | Complete authoritative backend suite | On, branch + floor + XML | Required |
| `make check-py-parallel` | Same complete suite via validated xdist policy | On, same floor + XML | Becomes authoritative only after Phase 4 |
| `make check-pydantic1` | Real Android-version compatibility environment | As current (off) | Required |

**Crucial:** use repo-root `backend/src` on `PYTHONPATH` **for all new commands**, and correctly quote Windows PowerShell/cmd paths. Do not implement a new target by doing `cd backend && pytest` in a worktree without protecting import resolution. Existing commands in `CONTRIBUTING.md` are the reference. Make script/target help reflect the current checkout and state clearly when coverage is skipped.

Expected agent loop: run changed tests and direct neighbors with coverage off; run a relevant subsystem battery; use authoritative `make check` or CI before PR completion. `--lf`, `--ff`, `-x`, and `--tb=short` are development aids, not substitutes for complete verification. If implementing change-impact selection from coverage contexts, label it *advisory* and conservatively fall back to broader tests when dependency graph data is missing, stale, or ambiguous. Avoid mandatory testmon-style infrastructure until this basic improvement is measured.

**Exit gate:** correct import path from main checkout and an isolated worktree on Linux/Windows; fast targets cannot accidentally masquerade as CI's passing full suite; contributor docs and docs guards updated.

## 6. Phase 2 — Inventory duplicate and unnecessarily repeated test work

Produce a **machine-generated candidate ledger** from the current suite, then manually review each candidate. It must distinguish:

### Candidate classes

- **A. Exact or effectively identical operation, different assertions.** Two tests build the same fixture and trigger the same expensive route call, then inspect different effects. High-value safe merging candidate.
- **B. Same expensive initialized state, several independent *read-only* checks.** Batch checks under one setup while keeping named diagnostic sections. High value if setup dominates.
- **C. Cartesian-product parameterization with independent fields.** Replace separate invocations with batch assertions where independence is proven; retain a mixed-validity case and clear failure identification. Moderate test-count benefit; often small wall-time benefit.
- **D. Tests with almost identical behavior but distinct scenarios/entry points.** Share helpers or turn into *multiple* parameterized scenarios; do not collapse the distinct execution merely to reduce function definitions.
- **E. Multiple tests that exercise the same production arcs but different **oracles** or regressions.** Normally **retain**: coverage alone cannot justify removal.
- **F. Expensive fixture repeated across a natural suite.** Convert repetitive setup into a safe immutable blueprint or combine checks; never casually change fixture scope.
- **G. Diagnostic/contract tests (AST guards, snapshots, grading negative cases).** Their unique error-detection properties are particularly important even if coverage overlaps.

### Detection algorithm

1. Inventory all `test_*.py`, class tests, `pytest.mark.parametrize`, parametrized node IDs, `tests/fixtures`, and frontend `.test.*` cases. Use AST rather than just regex for candidate discovery; normalize obvious variable names, but **never treat normalization as proof of redundancy**.
2. Identify likely repeated setup by comparing fixture names, shared helper calls, setup statement prefixes, payloads and route endpoints. Identify repeated expensive action by exact relevant call: same URL/method/condition or same `absorb.materialize`, `context.build_messages`, `inf.resolve`, etc.
3. Link node IDs to profiling results and optional per-test coverage contexts. Rank by: `potential_savings_seconds × confidence_of_equivalence / behavioral_risk`. Avoid focusing on a cheap pure-function test merely because it looks similar.
4. For each candidate, generate a **test contract matrix**:

   | Old test node ID | Initial state | Action under test | Assertion/side-effect | Unique negative case? | Proposed replacement assertion | Evidence |
   |---|---|---|---|---|---|---|

5. Before removal, ensure all old assertions or equivalent **stronger** checks exist; compare test execution arcs; run targeted adversarial mutation probes; check that failure reporting remains specific. Create traceable mapping of old node IDs to replacement IDs.
6. Track not just removed cases but **avoided fixture setups, avoided route calls, avoided real-time waits**, and actual before/after wall seconds on identical environment.

### Safeguards against over-consolidation

- One giant test can obscure three independent failures: when the first assert fails, the other assertions are not evaluated. Group related **one-operation** invariants, not every assertion in a subsystem. Use named asserts or structured comparison messages. If loss of independent diagnostics is material, keep separate tests and optimize a shared immutable setup instead.
- A table-driven test with `@pytest.mark.parametrize` still creates separate pytest cases and may **not** save setup time. Parameterization improves maintainability; true runtime optimization requires moving reusable work outside repeated executions safely, or batching **independent inputs** in one execution.
- Never share a mutable `TestClient`, campaign directory, app event loop, mock call counter, or global singleton across tests unless a specific isolation proof and teardown discipline justify it. Session-scoped mutable fixtures are explicitly discouraged.
- Do not replace route-level checks with store-level tests only: HTTP status/error envelope, lifespan, mutation ordering, authorization, headers and metering are separate contracts.
- Do not remove both sides of a positive/negative pair or a regression test with a historical issue reference. For architecture guards, a test proving *the guard itself rejects a bad program* is as important as the guard's normal positive scan.

## 7. Phase 3 — Initial **concrete** consolidation candidates

These are examples from the inspected repository, not a blanket deletion request. Start with the highest measured savings. For each, preserve every assertion and confirm equivalent failure-detection on the actual current tree.

### C1. Continuity reconciliation network failure — three executions → one

**File:** `backend/tests/test_continuity_reconcile_routes.py`

Candidates:
- `test_an_llm_failure_keeps_deterministic_candidates`
- `test_a_failed_run_carries_its_sweep_in_the_error`
- `test_a_failed_model_call_says_the_findings_were_saved`

Each sets up threads, keys and the same simulated network failure, executes `refresh`, waits for settlement, then checks one aspect of the failure. Combine into one test that checks the run state, error kind, error sweep, saved flag, candidate cache, and absent proposal. Keep one `refresh`/wait. Preserve its network-failure diagnostic and evidence that model failure does **not** erase deterministic discoveries.

**Why high-value:** removes two full app/client setups and two reconciliation attempts. **Caution:** ensure no test intentionally verifies fresh-process state after a previous run, or tests different timing points.

### C2. Continuity undecodable reply — two executions → one

Same file:
- `test_an_undecodable_reply_fails_the_run_and_keeps_candidates`
- `test_an_undecodable_reply_says_the_findings_were_saved`

One unparseable LLM response should check `undecodable` 502 classification, `sweep`, failed model status, deterministic candidates retained, no proposal, and `saved=True`. Keep distinct from network failure because error paths differ.

### C3. Reroll model attribution — two regenerations → one

**File:** `backend/tests/test_routes.py`
- `test_a_rerolls_snapshot_names_the_model_the_reroll_was_sent_to`
- `test_a_rerolls_cost_is_billed_to_the_model_it_ran_on`

Both use `_rerollable`, `_local_endpoint(..., model="llama3")`, then POST `regenerate` with `connection_id=other`. Do one regeneration, then assert **both** the `prompts` entry and the `usage` ledger are attributed to `llama3`. Check that reading one doesn't mutate the other.

### C4. Director note and accounting — two identical sends → one

**File:** `backend/tests/test_routes.py`
- `test_director_note_in_a_normal_scene_is_recorded_but_is_not_a_post`
- `test_a_director_turn_is_charged_to_its_own_note`

Both create an ordinary scene and send `{"content": "the storm intensifies", "director": true}` to `/chat`. One test can verify exactly-once prompt inclusion, saved note marker, non-user-post semantics, and the usage `by_post` index. Keep `test_a_director_turn_with_no_note_is_charged_to_nothing` and reroll tests **separate**; they check materially different cases.

### C5. Empty context section emission — four setups → one

**File:** `backend/tests/test_context.py`
- `test_story_so_far_absent_when_empty`
- `test_plot_threads_absent_when_none`
- `test_character_state_absent_when_none`
- `test_relationships_absent_when_none`

Create one empty campaign/scene, build `context_sections` **once**, check that all four named sections are absent. Retain separate garbled-file and nonempty-section tests; they exercise different behavior. Use diagnostics listing unexpected labels.

### C6. Inference baseline repeated state build — 32 builds → eight (candidate)

**Files:** `backend/tests/test_inference_resolve.py`, `backend/tests/inference_baseline.py`, `backend/tests/test_inference_equivalence.py`

Four tests are each parametrized over the same eight `baseline.STATES`:
- `test_every_task_resolves_as_the_baseline_recorded`
- `test_the_resolved_fallback_is_what_the_facade_sent`
- `test_every_override_resolves_as_the_baseline_recorded`
- `test_own_sampling_reports_what_the_baseline_recorded`

**Option A (prefer if read-only):** per-state test initializes one store/client once and runs four named assertion helpers against it. Keep the full task/scope/override loops and connection-sampling checks. Retain `test_inference_equivalence.py` as independent end-to-end characterization against the frozen baseline.

**Option B:** if any helper changes state or asserting order independently matters, cache only *immutable* construction inputs/blueprints, keep separate tests, and avoid state reuse. Do not regenerate the frozen inference baseline to silence a failure. Carefully inspect that the new resolver refactor's assertions are all preserved and no hidden route writes invalidate later reads.

### C7. Absorb non-list section matrix — 75 cases → ~5–6 batched cases

**File:** `backend/tests/test_absorb_store.py`  
**Test:** `test_parse_output_treats_a_non_list_section_as_empty`

It currently cross-multiplies five invalid JSON value representations with all **15** list-valued sections derived from `absorb.parse_output("{}")`, creating **75 pytest cases**. Each list section is independently handled via `_rows()` in `backend/src/grimoire/store/absorb/parse.py`.

Proposed: for each invalid value, supply **all 15 section keys** in one JSON input and assert each response section becomes `[]`, with the section name in failure messages. Add at least one **mixed valid/invalid** input to prove an invalid section does not suppress valid unrelated sections. Keep derivation of the section names from the parsed contract rather than hardcoding them. Check that source/branch execution includes every old normalization path.

This saves pytest case overhead, not necessarily much execution time; lower priority than expensive route and fixture cases. Explicitly preserve distinctions between missing, JSON null, false, number, string, and non-list object wherever tested elsewhere.

### C8. Absorb commitment title normalization (conditional merge)

**File:** `backend/tests/test_absorb_store.py`
- `test_a_new_commitment_still_lands_on_an_open_one_of_the_same_title`
- `test_the_same_title_still_lands_on_its_open_record`

These share most of their setup and assertions; the latter introduces case/whitespace normalization. Verify whether the plain match adds a distinct branch or regression. If it does not, merge/batch without losing the check that an existing open commitment is reused and `before` is the standing record. **Do not conflate** with resolved-commitment reopening, distinct slug collisions, explicit-ID priority, or two-record-in-one-batch tests: those are separate failure modes.

### C9. Character-generation and sheet validation (maintenance first)

`backend/tests/test_sheets_store.py` has several rejection tests differing primarily in invalid creation payload. `backend/tests/test_characters_store.py` has malformed direct-Chub import cases. These can become table-driven tests with shared local helpers, but **mere parametrization doesn't reduce setups**. Batch independent rejections into one initialized store only if rejected operations are proved nonmutating and independent; verify unchanged store contents after each rejected input to establish that property.

### Explicitly **do not** conflate these

- `retry` vs `regenerate` endpoints (even when response roles are identical): both paths must execute somewhere. A parameterized test preserves both scenarios but does not halve route executions.
- `test_eval_graders.py` vs `test_evals.py`: the former tests individual graders, including negative cases; the latter exercises whole-case replay. An aggregate failure can hide a broken specific grader.
- Pydantic v1 vs v2 full-suite runs: version compatibility is a separate execution dimension, not test duplication unless separately and explicitly established at the *gate-policy* level.
- `test_lock_domain_guard.py`, atomic/overlay/import/pydantic/paths/usage guards: the AST scanner and negative tests of the *scanner itself* serve independent purposes. Optimize scans/caching only after profiling; do not strip the deliberately explanatory regression tests.
- Timeout cancellation, stream detachment, lock ordering, duplicate callbacks, and saved-review transaction tests: different interleavings, fault locations, and recovery semantics are not equivalent just because they have the same setup.
- Historical fixtures, baseline files, and golden snapshots: their intentionally frozen contents are part of coverage of backward compatibility.

**Exit gate:** approved consolidation ledger; reduced repeated heavy operations; all prior test contracts mapped; no lost covered production arcs on the stable source snapshot; targeted mutation probes demonstrate retained detection; real wall-time delta measured.

## 8. Phase 4 — Parallel Python testing with pytest-xdist

**Do not just add `-n auto` to every target.** xdist process count, test scheduling, filesystem isolation, fixture scope, coverage combination, and port use must be validated. Keep the serial reference during rollout.

### 8.1 Dependency and configuration

- Add `pytest-xdist` to the **dev-only** dependencies (choose and record a verified compatible pinned or bounded version; do not change Android base dependencies). Confirm Python 3.11/3.14 and the Pydantic 1 environment can install it. Preserve `pytest-cov` and existing branch coverage settings.
- Expose worker count and scheduler through explicit Make variables/flags, with **serial as a straightforward fallback**, e.g. `WORKERS=0`, `WORKERS=2`, `WORKERS=4` and `DIST=loadfile|load|worksteal`.
- Avoid defaulting to `-n auto` on powerful Windows desktops: spawning a huge number of Python workers can degrade performance, consume substantial memory, or exhaust handles. Prefer a measured local limit and a conservative CI value.
- Start an experiment with `-n 2` and `-n 4`, each with `--dist=loadfile`, compared with serial using **the same complete suite**, same Python, same coverage settings, and exact SHA.
- Then measure `--dist=load` and `--dist=worksteal` once isolation is stable. These usually balance uneven durations more effectively, whereas `loadfile` places **all >800 tests in `test_routes.py`** on one worker. The huge file may become a critical-path straggler. Measure before choosing, and consider splitting that test module for scheduling reasons only after ensuring imports/helpers/conventions remain correct.

### 8.2 Isolation audit before enabling parallelism

Inspect test code and fixtures for:

1. **Filesystem:** all writes under each test's `tmp_path` / fake `GRIMOIRE_HOME`; per-worker `tmp_path_factory` paths unique; immutable shared files only; no common `build/`, repo-root fixtures, generated export paths, or static `/tmp` collisions. Existing root-pointer/DEFAULT_HOME safety must remain in every worker, including after `monkeypatch.undo()`.
2. **Networking:** `live_server` binds port 0; look for hard-coded ports or global process names, server threads left alive after failed assertions, SSE sockets leaked on cancellation.
3. **Processes and concurrency:** lock files, child processes, git repositories initialized under test temp roots, timeout tests, and subprocess environment variables. Ensure all spawned children terminate and don't outlive their worker.
4. **Global state:** importlib.reload, cached `store.home()`, singleton locks, fake provider counters, `os.environ`, temporary hook monkeypatches, `@functools.cache` survey functions, clock/random controls, and shared parser state. xdist isolates *processes* but not mutable state between tests **within** one worker.
5. **Fixture lifecycle:** existing per-test `TestClient(app)` lifespans are intentional; never optimize parallelism by skipping them. Autouse `tracker`/`reconcile` fixtures must behave identically in every worker. xdist session fixtures run once **per worker**, not globally once; don't assume otherwise.
6. **Collection:** each worker must collect the same test IDs/order. Avoid nondeterministically-generated params based on temporary paths or environment/worker IDs. Ensure IDs and test counts match serial. No `--ignore`, collection hooks or automatic marker filtering that silently omit tests.
7. **Test order dependencies:** run shuffled order in controlled experiments and examine cases that fail only under the whole suite. A previous issue, [#314](https://github.com/charlesmsiegel/grimoire/issues/314), documented an isolated-passing/full-suite-failing test. Fix leaks; do not make order-based assumptions part of the new scheduler.

Use test-specific fixture isolation and `pytest-xdist` worker IDs only where needed. Don't introduce shared inter-worker state merely to save setup time. When a test truly requires exclusive cross-process access and cannot easily be isolated, investigate it; if unavoidable, assign it to an explicitly documented **serial island** run outside the worker pool rather than hiding it with an `xdist_group` mark. A group mark ensures placement on one worker, not universal exclusivity against the other workers.

### 8.3 Coverage under xdist

- `pytest-cov` supports xdist, but **verify the implementation** produces the same combined `backend/coverage.xml` and reports all production modules (including untouched 0%-covered files), with branch measurement and the same `--cov-fail-under=$(COV_FLOOR)` behavior.
- Distinguish the default single xdist invocation (pytest-cov combines its workers) from **multiple independently launched pytest invocations or CI shards** (which require distinct coverage data files and an explicit final union/report).
- Test a deliberately failing test and verify the job goes red, coverage artifact behavior remains useful, and another passing worker cannot turn it green.
- Do not loosen the 93% floor or omit unreachable/untested modules to make distributed coverage look healthier. If branch/line totals differ, find the source of the discrepancy rather than accepting a percentage within tolerance.

### 8.4 Serial islands (only if proven necessary)

If a small subset cannot run alongside workers because it uses truly shared external resources, register a documented `serial` marker and run those tests in a **separate stage**. Requirements:

- The parallel stage runs the complement; the serial stage runs exactly those marked tests; a collected-node-ID manifest proves their disjoint **union equals the complete suite**.
- The two stages' coverage data files are unique and combined in a final trusted coverage report. Neither partial stage alone is allowed to claim it met the full-coverage floor.
- No `skip` or `xfail`; both must fail the gate on failure.
- Keep serial islands few and justify each by a reproducible, non-local resource conflict. Prefer fixing the test isolation instead.

### 8.5 Repeatability and selection of scheduler

For each `(worker_count, scheduler)` candidate, report full test count, runtime, max RSS and flaky failures under coverage and no coverage. Use three complete runs of promising candidates; repeat high-risk concurrency/streaming suites in many shorter targeted iterations under stress. Do at least one complete Windows smoke run if practical, because process spawning and file locking differ from Linux.

The chosen scheduler must optimize the *longest worker*, not simply average test-case speed. Compare elapsed profile distributions and worker idleness. `loadfile` is a good isolation experiment; **do not freeze it as the default** without confirming that `test_routes.py` is not the overwhelming straggler. Splitting that file by stable route/test areas may be preferable to unsafe test-level balancing when heavy module-scoped fixtures exist.

**Exit gate:** all tests run; same source coverage and output contract; repeated green runs; no new unresolved flakes; deterministic fallback command; median wall time demonstrably shorter.

## 9. Phase 5 — Reduce fixture and test-execution cost safely

Profile first; only change proven hotspots.

### 9.1 App/TestClient setup

The `client` fixture currently reloads `store`, builds `create_app()`, enters lifespan, and patches the LLM provider **per test**. This is an obvious place to *measure*, but **not permission to make it session scoped**. Background runs, global lock registries and app lifecycle are precisely the behavior many tests validate.

Allowed optimizations (conditional on proof):

- Tests that do not require HTTP semantics should call pure store functions or pure helpers rather than create and discard a `TestClient`, **provided no route-level contract is lost elsewhere**.
- Extract safe, **immutable setup recipes** (static cards, module layouts, fixture input constants, parsed templates) and reuse them without sharing a mutable data directory or event loop.
- For several read-only assertions against one constructed state, combine into one test execution, as in inference baseline C6 and empty context sections C5.
- If app startup legitimately dominates, investigate constructing route tables or immutable schema caches efficiently **while preserving a real lifespan for tests requiring it**. Put such work behind explicit tests verifying no state contamination.

### 9.2 Real time and polling

Inspect tests that use real `sleep`, `asyncio.sleep`, `anyio.sleep`, deadlines, child process waits, and socket polls. Decide for each:

- Is **actual elapsed time or cancellation** part of the behavior? Keep a real-time integration test for it.
- Is the sleep merely waiting for a controllable event? Replace with existing synchronization primitives (events, barriers, `HeldOpenRouter`, fake clock where appropriate), still with a bounded fail-safe deadline.
- Does polling wait a fixed interval after a condition could already be signaled? Prefer event-driven waiting if the production API exposes a suitable synchronization point. Don't create brittle monkeypatches of timers which falsely pass the behavior under test.
- Are duplicate timeout tests all exercising the same failure path? Retain each materially different timeout/cancellation/cleanup condition; combine only repeated setup/action plus independent assertions.

Never solve an intermittent failure by blindly increasing the sleep or timeout. Avoid using tiny sleeps that only pass on unloaded machines; favor determinism.

### 9.3 AST guards and repeated scans

Measure the expensive guards (notably `test_lock_domain_guard.py`). That file already caches its `_survey()` with `functools.cache`; retain that protection. Investigate duplicate AST parses across independent guards only if the profile justifies it. A read-only parse cache keyed by source content/SHA and Python version **might** help, but must invalidate on test mutations and support guard tests which deliberately write temporary synthetic files. Avoid cross-run stale caches of source code. Keep every negative probe proving guards fail in representative bad cases.

### 9.4 Fixtures, temp files, image tests

Only cache immutable images/assets or generated fixture content if this does not alter the expected test environment. Test first-run behavior, corruption detection, migration, atomic write, image identity, and content-addressed storage must still exercise fresh-state paths. Avoid symlinks/hardlinks or copy-on-write sharing where mutation could bleed across tests. On Windows, verify temp path and file-handle closure; preserve security of pointer paths and real data isolation.

### 9.5 Pydantic 1 environment setup

`check-pydantic1` currently wipes and rebuilds a venv every time. Measure the fraction of wall time spent creating the environment, installing dependencies, and **running tests**; current CI logs suggest running tests dominates, so treat caching as low priority. If optimizing local setup, use an environment fingerprint covering Python interpreter/version, package requirements, pins, Pydantic and FastAPI constraints, and relevant dependency resolution. On any mismatch, rebuild. Keep `pip check` and the explicit `pydantic.VERSION.startswith("1.10.")` assertion; never contaminate the standard development venv. CI's clean install remains a valuable compatibility proof.

**Exit gate:** quantified reductions in costly setup or waits; no new per-test state leakage, no loss of first-run migration/initialization behavior.

## 10. Phase 6 — CI design that gives fast feedback **and** an authoritative full gate

### 10.1 Preserve the fast and slow roles

Keep existing required checks green/red with equivalent semantics. Add a **fast feedback layer** that can complete before the full Python matrix, without replacing the full matrix on the latest PR head commit:

- Fast: lint, mypy, ESLint, templates, frontend check, targeted Python smoke/contract groups (subject to proven classification), optionally a quick `check-py-no-cov` subset.
- Full: complete Python 3.11 branch-coverage suite, Python 3.14 complete suite, Pydantic 1.10 complete suite, frontend coverage, Android debug APK, plus existing lint/ratchet/template gates.

Fast check success **must not imply full validation**. If GitHub branch protection or a merge queue uses named required checks, preserve their names or coordinate an explicit update; avoid splitting workflows in a way that makes a required check never report. On PR updates, cancel *obsolete* runs, not the definitive current one. Required checks should still execute against the current merge candidate, not only against an arbitrary older commit or nightly main.

Optional dependency-aware targeted selection is a speed hint, not a reason to omit full verification. It may be wrong about transitive effects in `store`, templates, the shared test fixture and route imports.

### 10.2 Coverage and artifacts

Maintain `backend/coverage.xml` from the 3.11 authoritative run and `frontend/coverage/lcov.info` from Istanbul with `all: true`. Preserve upload-on-failure behavior when useful, but not misleading upload failures on cancelled runs. Every report must identify the exact head SHA. Coverage per worker and/or shard must be combined **before** the 93% floor is evaluated. Audit warnings, skipped tests and unexpected test-count changes; a job with zero executed tests must fail, not silently pass. Keep artifact names collision-free.

### 10.3 Cache only what is worth caching

The frontend already uses the setup-node npm download cache; its 3-minute job is not the main bottleneck. Python currently installs `backend[dev]` anew in several independent jobs. Consider setup-python's built-in `cache: pip` and `cache-dependency-path: backend/pyproject.toml` (or the project's eventual lockfile) only **after** confirming meaningful setup time savings. This caches downloads, not a magically reusable resolved virtualenv; unpinned dependencies may still resolve differently. Don't cache a working tree's mutable source, test stores, or Pydantic venv blindly.

Keep GitHub Actions action versions pinned by immutable SHA, in accordance with the existing workflow. Any action upgrade, cache mechanism or dependency lock change should be separately verified.

### 10.4 Optional cross-runner sharding: only if xdist is insufficient

If a validated xdist run remains the CI bottleneck, consider **time-balanced CI shards** across GitHub-hosted runners. Requirements:

- Stable deterministic partition of the **entire** collected test-node manifest (each node exactly once), based on measured durations; explicit validation for missing, duplicate and empty shards.
- Separate coverage raw-data artifact per Python version/shard; paths normalized to one source tree; a dedicated aggregator performs union and emits `backend/coverage.xml`, then enforces the original 93% floor and no missing source files.
- Do not combine 3.11 and 3.14 coverage to artificially raise the main 3.11 floor; retain distinct result semantics.
- The aggregation job fails if any shard fails, is cancelled unexpectedly, or does not upload its expected data. No soft-fail `continue-on-error`.
- Rebalance shards using observed duration data, not alphabetical counts. Cost-estimate extra Actions runner-minutes and maintenance overhead; retain an unsharded reproducible local equivalent.

Prefer keeping the complete Pydantic 1 environment check until a separate, explicit coverage/compatibility study proves that a smaller selection is semantically sufficient. That is a *policy decision*, not an automatic test refactor.

**Exit gate:** full required CI faster while preserving authoritative signal, original reporting contracts, and reproducibility.

## 11. Phase 7 — Frontend tests (secondary priority)

The existing frontend job usually finishes in roughly 2–4 minutes; do not spend the primary effort here until backend improvements have landed. For any frontend acceleration:

- Preserve Vitest invocation from `frontend/`, JSDOM settings, and `src/test-setup.ts`'s two-tick/settled-DOM behavior; issue [#351](https://github.com/charlesmsiegel/grimoire/issues/351) documents prior load-sensitive flakes.
- Preserve `provider: "istanbul"`, `coverage.all: true`, exclusions for test scaffolding, and `lcov.info`; the repository explicitly explains why switching to V8 produced misleading 100% claims for unimported files.
- Profile slow component suites and duplicate render/mocks, particularly heavy campaign-play tests; share immutable mock factories in `frontend/src/testkit/`, without hoist-order or dynamic-import cycles. Don't move testkit code back into the coverage denominator.
- A no-coverage single-test development command is valuable; full frontend typecheck/coverage remains required.
- Do not increase Vitest worker count without testing under CPU contention; faster unloaded runs can be flakier on shared runners.

## 12. Robustness and deliberate failure tests for the new harness

Create a small **harness self-test** suite which deliberately demonstrates:

1. A passing test plus a failing test in separate xdist workers makes the overall job fail.
2. Coverage union is correct for disjointly exercised branches; unexecuted production modules still appear as uncovered; lowering actual coverage below `COV_FLOOR` fails.
3. Cancelled CI attempts do not produce false artifact failures, while completed successful jobs with a missing required coverage file fail.
4. The node-ID manifest is stable between serial and parallel collection, including parametrized cases, and after any intentional consolidation **old→new mapping** is documented.
5. The privacy fixtures protect the real user store and bootstrap pointer inside **every** worker, including after `monkeypatch.undo()` and in subprocess-backed tests.
6. A worker crash, hang, or lost child process does not result in a false green. Set a conservative CI job timeout and report which test was running; do not globally silently rerun and mask the failure.
7. `make` from Windows cmd/PowerShell and worktrees imports source from **the active checkout**, not from an editable install of another worktree.
8. A true Pydantic-2-only code path still fails the Pydantic 1.10 gate and isn't falsely masked by a synthetic stub.
9. Guard test negative cases still detect forbidden raw writes, missing locks, bypassed overlays, prohibited pydantic API, and forbidden import forms.
10. A consolidation mutant targeting a supposedly redundant branch or outcome fails at least one retained test, not merely an unrelated aggregate assertion.

**Mutation-probe examples (targeted, temporary, and automatically restored):** flip `error.saved` in continuity reconcile; omit model attribution from reroll usage; stop persisting director-note marker; accept a scalar where `_rows` must return `[]`; reopen a fulfilled commitment by title; treat wrong-campaign lock as valid; make the pointer resolver use the real home. A targeted test must catch its assigned fault. Do not commit deliberate mutants, use the real data store, or use mutation results as a substitute for the full gate.

Do not paper over intermittent failures via `pytest-rerunfailures` as a required default. Record and fix the underlying race. Targeted stress repetition is for diagnosis, not for hiding flakes.

## 13. Measurement, reporting and continuous regression prevention

### Required final report structure

1. **Baseline:** source SHA, machine/runtime dimensions, passed/skipped counts, Python versions, coverage totals and covered arcs, top slow test/modules/fixtures, full CI times, runner-minutes.
2. **Work done by PR:** linked PRs and exact commands, which expensive operations were removed, xdist strategy/worker count, fixture changes and their isolation evidence.
3. **Behavior preservation:** before/after contract matrix; unique branches lost or added; mutation probes; test ID mapping; screenshots/logs of failure diagnostics where relevant; snapshots/guards unaffected.
4. **Results:** median, minimum, p90 and maximum wall times from comparable runs; variance; parallel worker utilization; peak RSS; coverage tracing overhead; CI total runner-minutes.
5. **Unresolved risks:** flaky cases, deliberately serial islands, memory/Windows limitations, Pydantic 1 deviations, target misses.
6. **Rollback:** exact Make flag/workflow configuration to return to serial without losing a required check or forgetting a set of tests.

### Lightweight performance budget

Add or preserve a recurring performance summary for CI, without making flaky timing assertions in unit tests. Warn if the 5-run median of the 3.11 backend job or total CI critical path regresses by >20% at comparable runner/dependency settings; investigate environment changes before failing a PR for noise. Also warn if new tests add many test cases with a huge shared expensive setup, or if a test suddenly takes many real seconds. Baselines should version with intentional behavior changes rather than being regenerated to hide slowdowns.

The performance report should distinguish **fast pass/fail verdict**, **complete merge-ready verdict**, and **total cloud compute consumed**. These answer different productivity questions.

## 14. Proposed PR sequence (small and independently reviewable)

| PR | Work | Depends on | Minimum exit evidence |
|---|---|---|---|
| **A — Baseline & profiler** | Timing/phase instrumentation, collection manifest, source/arc snapshot, ranking report | Current main | Repeatable serial baseline; profiler's own tiny tests |
| **B — Fast developer commands** | Targeted no-cov and profiling commands, worktree-safe PYTHONPATH, updated docs | A | Local fast commands work on Linux and Windows and cannot claim full CI pass |
| **C — xdist proof of concept** | Dev dependency, optional workers/schedulers, isolation audit, parallel coverage harness tests | A | 2/4-worker full suite stable; serial fallback intact |
| **D — High-value consolidation: routes/continuity** | C1–C4; explicit assertion mapping and operations avoided | A | All old contracts preserved; line/arc comparison; measured seconds saved |
| **E — Baseline & parser/context consolidation** | C5–C9 after risk review | A | No frozen baseline changed; independent scenarios still asserted; improved setup count |
| **F — Fixture/runtime optimization** | Profile-guided app startup, immutable blueprint, polling improvements, AST scans | A, C and relevant D/E | Proven hotspot reductions, no state leak, stress passes |
| **G — Authoritative parallel CI rollout** | Pick scheduler/workers, modify `make check-py`/CI safely, full artifacts, docs | C, D/E as relevant | All required gates; same source/branch set and coverage floor; measurable critical-path speedup |
| **H — Optional CI sharding** | Time-balanced manifests, coverage union aggregator, artifact audit | G; only if needed | Exact suite union; coverage held; cheaper/faster tradeoff explicit |
| **I — Optional frontend pass** | Profile and simplify costly frontend suites | A; independent of C–H | No Istanbul/all/settling regression; measured frontend gain |

PR numbering is suggested, not a requirement to land all of them. If B/C/D/E can proceed in parallel, give each agent its **own worktree/branch** and declare ownership of overlapping files. Rebase and resolve concurrent changes explicitly; do not silently overwrite each other's configuration.

### Per-PR requirements

- Capture old and new test counts and runtimes on an equivalent environment.
- Include before/after JSON/summary and a statement of how correctness was checked.
- Run targeted tests immediately, then full required tests as appropriate; CI must pass on the resulting head.
- Follow `CLAUDE.md`'s mandatory Codex review checkpoints at the documented transitions. Do not claim a review happened unless it did.
- Update `CLAUDE.md`/`CONTRIBUTING.md`/Makefile descriptions if operational commands or gate semantics genuinely changed; `test_docs_guard.py` may enforce cross-document consistency. No duplicate drifting copies of gate definitions.
- Do not generate unreviewed changes to `lint-baselines`, the frozen-campaign `home/`, or inference fixture baseline.

## 15. Acceptance / rejection checklist

A change **must be rejected or revised** if any of the following holds:

- It makes CI faster by not running a formerly required behavior or environment check, unless explicitly authorized as a new policy.
- It deletes a test on the sole grounds that it has no unique line coverage.
- It moves mutable state into a broad session fixture without a concrete isolation proof.
- It treats a lower collected-test count as evidence of speed without wall-clock timing.
- It changes coverage settings/`COV_FLOOR`, reports only a subset's coverage as full-suite coverage, or loses raw branches/arcs without explanation.
- It compromises filesystem isolation, attempts to touch `~/.grimoire`, drops an app lifespan needed for background runs, or introduces real provider calls.
- It uses retries/skips/xfails to hide flaky tests, or serializes everything unnecessarily while claiming xdist success.
- It reports “all checks pass” without test result evidence or hides a non-running/cancelled shard.
- It alters the frontend Istanbul/`all: true` or DOM-settling guarantees to shorten tests.
- It creates an unmaintainable single megatest whose early assertion masks unrelated critical invariants.
- It violates the repository's Codex review gates, privacy rules, baseline ratchets, or fast-forward Git practices.

Successful completion needs the **full matrix** on the latest tested head, verified coverage and behavior, and a reproducible timing report. Optimization patches may be stopped early when additional complexity yields diminishing returns; in that case, document results and leave the unoptimized authoritative check intact.

## 16. Copy/paste handoff to Claude Code

> You are optimizing `charlesmsiegel/grimoire`'s test suite. Read the attached **Grimoire Test-Suite Acceleration and Consolidation** specification **in full**, then all of the repository's `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, and relevant architecture docs. The goal is dramatically faster **trustworthy** iteration, through measured profiling, removing duplicated expensive test work without losing assertions, safe pytest-xdist parallelism, selective local commands, and carefully controlled CI rollout. Do not alter application behavior or drop the authoritative Python 3.11/3.14/Pydantic 1, Android, coverage or lint gates. Capture an exact-SHA serial baseline and per-test setup/call/teardown profile first, including a raw production branch/arc baseline. Follow the repository's mandatory Codex review checkpoints; write a granular plan and implement in reviewable slices with real before/after measurements, isolation tests, mutation probes, and serial rollback. Treat all proposed consolidation pairs as candidates to verify, not orders to delete tests. Use only the synthetic store fixtures, never real user data or real LLM calls. Do not report a speedup, green gate, or completed review without evidence. Preserve existing coverage.xml/lcov artifacts, the 93% branch-coverage floor, worktree PYTHONPATH correctness, and Windows compatibility. Ask only when a genuine policy decision or repository review gate requires user intervention; otherwise proceed through approved phases independently and document blockers precisely.

## 17. Primary references

### Repository sources

- [`CLAUDE.md`](https://github.com/charlesmsiegel/grimoire/blob/main/CLAUDE.md) — authoritative agent conventions and review gates.
- [`AGENTS.md`](https://github.com/charlesmsiegel/grimoire/blob/main/AGENTS.md) — routing to project conventions and privacy.
- [`CONTRIBUTING.md`](https://github.com/charlesmsiegel/grimoire/blob/main/CONTRIBUTING.md) — gate definitions, worktree caveats, test policies.
- [`Makefile`](https://github.com/charlesmsiegel/grimoire/blob/main/Makefile) — actual targets, coverage floor, Pydantic environment.
- [`.github/workflows/ci.yml`](https://github.com/charlesmsiegel/grimoire/blob/main/.github/workflows/ci.yml) — authoritative CI matrix and artifacts.
- [`backend/pyproject.toml`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/pyproject.toml) — pytest, dev dependencies and coverage settings.
- [`backend/tests/conftest.py`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/tests/conftest.py) — privacy fixture, tracker/reconcile defaults, client/live-server lifecycle.
- [`backend/tests/test_continuity_reconcile_routes.py`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/tests/test_continuity_reconcile_routes.py), [`test_routes.py`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/tests/test_routes.py), [`test_context.py`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/tests/test_context.py), [`test_absorb_store.py`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/tests/test_absorb_store.py), [`test_inference_resolve.py`](https://github.com/charlesmsiegel/grimoire/blob/main/backend/tests/test_inference_resolve.py) — initial consolidation candidates.
- [`frontend/vite.config.ts`](https://github.com/charlesmsiegel/grimoire/blob/main/frontend/vite.config.ts) — frontend coverage provider and exclusions.
- [CI run 37733037397](https://github.com/charlesmsiegel/grimoire/actions/runs/37733037397), [37719630577](https://github.com/charlesmsiegel/grimoire/actions/runs/37719630577), [37703035430](https://github.com/charlesmsiegel/grimoire/actions/runs/37703035430) — wall-time examples.

### Tool documentation

- [pytest durations reference](https://docs.pytest.org/en/stable/reference/reference.html) — `--durations`, `--durations-min`.
- [pytest-xdist distribution strategies](https://pytest-xdist.readthedocs.io/en/stable/distribution.html) — `load`, `loadfile`, `loadscope`, `loadgroup`, `worksteal`.
- [pytest-xdist fixture / shared-worker guidance](https://pytest-xdist.readthedocs.io/en/stable/how-to.html) — session fixtures per worker, temp roots, isolated logs.
- [pytest-cov overview](https://pytest-cov.readthedocs.io/en/latest/readme.html) — xdist integration and combined measurement.
- [pytest-cov test contexts](https://pytest-cov.readthedocs.io/en/latest/contexts.html) — `--cov-context=test` and phase-labeled node IDs.
- [CoverageData API](https://coverage.readthedocs.io/en/7.13.5/api_coveragedata.html) — per-file lines/arcs and contexts.
- [coverage combine](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html) — independent-run/shard combination and path mapping.
- [GitHub Actions `setup-python`](https://github.com/actions/setup-python) — dependency-cache semantics.

---

**Final instruction:** Speedups are only accepted if the same critical mistakes would still make the test suite red. Verify that claim rather than treating it as an aspiration.
