# 01a-S2: Metered live runs in an eval scope — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every call a live eval case sends is metered by the production meter
into the case's throwaway home, harvested from there before the home is
deleted, and never reaches the library's ledger.

**Architecture:** `runner.live` meters a generate case with `store.usage.meter`.
A tripwire compares `paths.home()` with the real home recorded by `run_live`,
first thing inside each isolate and again before the harvest. A drain waits for
any app a case handed over (`ctx["app"]`) to have no live run. The harvest is a
strict `usage._read_rows` from the run's start day; each row is copied onto
`Result.rows` stamped `scope`, `eval_run` and `case`. Rates are read once in the
real store and threaded through for S3.

**Tech Stack:** Python, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01a-eval-cost-latency-reporting-design.md` §3, §4, §6 (harvest, drain), C2, §10, §11 tests 1 (rows), 7, 8, 10, 17, 18.

## Global Constraints

- The isolation message is exactly `eval isolate is not a throwaway home`; the drain failure says `follow-ups still running`.
- `DRAIN_CEILING_S = 120`.
- The stamped fields go on the *copy*: `scope: "eval"`, `eval_run: <run id>`, `case: <case id>`. `store.usage.record` is unchanged.
- `live(..., real_home=None)` skips the tripwire; `live_all` always passes it from `run_live`.
- The harvest reads `strict=True`; an `OSError` is reported as `cost: not reported (ledger unreadable)`, never `calls 0`. Metrics never change pass/fail.
- `evals.runner` leaves `UNMETERED_OUTSIDE`; the cap drops to 1.
- No money or token figure is printed in this slice (S3 adds them).

## Review Focus

- A drain that times out: the isolate directory survives and `GRIMOIRE_HOME` still names it; no later case runs into it.
- The tripwire with a home spelled differently (trailing slash, symlink): compare resolved paths.
- A replay result has no harvested rows and must print no metrics line.
- An `LLMError` case still harvests the error row it filed (the failure cost something).
- A run crossing UTC midnight keeps the earlier day's rows (`run_day` is fixed at run start).

---

### Task 1: Meter, tripwire, drain, harvest

**Files:**
- Modify: `evals/runner.py` (`Result`, `live`, `live_all`, new `harvest`, `drain`, `FollowUpsRunningError`, `ISOLATE_ERROR`, `DRAIN_CEILING_S`, `report`)
- Modify: `evals/run.py` (`temp_home` keeps the isolate on `FollowUpsRunningError`; `run_live` records `real_home`, reads rates, mints the run id and day)
- Modify: `backend/tests/test_usage_guard.py` (`UNMETERED_OUTSIDE`, cap 1)
- Modify: `evals/README.md` ("What a live run reads": rates)
- Test: `backend/tests/test_evals.py`

**Interfaces:**
- Produces: `Result.rows: tuple[dict, ...] | None = None` (None: nothing harvested — replay), `Result.ledger_error: str = ""`; `live(case, target, record=False, *, client=None, backend=CHAIN, real_home: Path | None = None, run_id: str = "", run_day: str = "", rates: usage.Rates | None = None) -> Result`; `live_all(..., real_home=None, run_id="", run_day="", rates=None)`; `harvest(run_day: str, run_id: str, case_id: str) -> tuple[dict, ...]` (raises `OSError`); `drain(ctx: dict, ceiling: float = DRAIN_CEILING_S) -> None` (raises `FollowUpsRunningError`); `class FollowUpsRunningError(RuntimeError)` with `.result: Result`.

- [ ] **Step 1: Failing tests** in `test_evals.py`:
  - `test_a_live_generate_case_files_one_eval_row`: `FakeLLM(usage={"prompt_tokens": 12, "completion_tokens": 3, "cost_usd": 0.0042})` → one row, `task == case.task`, no `campaign`, `scope == "eval"`, `eval_run` and `case` stamped; the isolate's own ledger row has no `scope`.
  - `test_the_tripwire_sends_and_builds_nothing`: isolate yields the real home → `fake.calls == 0`, no `campaigns/`/`worlds/` in it, error is the isolate message; `live(..., real_home=None)` in the same home sends.
  - `test_no_eval_row_reaches_the_real_home`: `live_all` over two cases with per-case `tmp_path` isolates → the real home has no `usage/`.
  - `test_a_run_crossing_midnight_keeps_its_rows`: `usage._now` stamps day 1, `usage._today` says day 2, `run_day` day 1 → row harvested.
  - `test_an_unreadable_ledger_is_reported_not_zero`: month file replaced by invalid UTF-8 → `ledger_error`, report says `cost: not reported (ledger unreadable)` and not `calls 0`; the case still passes.
  - `test_the_drain_waits_for_a_follow_up` and `test_a_follow_up_past_the_ceiling_keeps_the_isolate` (fake app whose `state.runs.any_live()` is truthy while a thread is pending; `DRAIN_CEILING_S` patched to 0.1; the second through `run.temp_home`).
  - `test_rates_are_read_in_the_real_store`: `usage.Rates.current` patched to note `paths.home()` → called once, with the real home.
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_evals.py -q` → new tests fail.
- [ ] **Step 3: Implement.** In `live`: tripwire before `prepare`; generate inside `with store.usage.meter(case.task) as m:` passing `usage=m.usage`; after the model work, `drain(ctx)`, the tripwire again, then `harvest`. `live_all` catches `FollowUpsRunningError` *outside* `with isolate()`, appends its result, and reports every remaining case `not run: an earlier case's follow-ups are still running`. `temp_home` neither restores the environment nor deletes the directory when `FollowUpsRunningError` passes through it. The report prints `           calls N` for a harvested result, or the ledger sentence.
- [ ] **Step 4: Guard.** Remove `evals.runner` from `UNMETERED_OUTSIDE` and set the cap to 1.
- [ ] **Step 5: README** — add "and the per-token rates" and the throwaway-store note (§10).
- [ ] **Step 6: Run** `tests/test_evals.py tests/test_usage_guard.py` → PASS. Commit `01a-S2: meter live evals in the isolate and harvest their rows`.

## Plan gate (substitute review, 2026-10-10)

The Codex CLI is not installed; a hostile subagent review stood in. Folded:

- **B1** A non-`LLMError` from the model work skipped the drain and let
  `temp_home` restore the real home under a running follow-up. `live` now
  drains on any exception and turns a timeout into `FollowUpsRunningError`
  (test: `test_an_unexpected_exception_is_drained_before_the_isolate_restores`).
- **S1** A drain timeout dropped the case's spend. The kept isolate is read
  (reading writes nothing) and the rows are kept, marked `partial`.
- **S2** `live_all` with no `real_home` checked nothing. It now takes the home
  active before the first isolate (test:
  `test_live_all_trips_on_the_home_active_when_it_starts`).
- **S5** The lint ratchet runs before each commit; `live` is split
  (`_ask`, `_model_work`, `_settle`) to stay under C901.
- **S6** The drain's case contract is `ctx["app"]` (polled through
  `app.state.runs.any_live()`) and `ctx["shutdown"]` (ends the lifespan,
  which stops the threads a run registry does not see), stated on `drain`.
- **M7** `test_usage_guard.py`'s docstring updated for one outside caller.

Not folded: **M3** (the no-real-home test passes before S2: it is a
regression guard, not a red-first test); **M4** (a row `record` failed to
append is undetectable from the holder without changing `Meter`, which this
spec does not touch); **M8** (a strict read still skips a torn line, as every
reader does; "strict" is about the file).
