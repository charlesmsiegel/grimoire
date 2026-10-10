# 01a-S3: Cost, latency and token reporting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each live case reports wall time, tokens and the three money columns
(never added), per case, per task and aggregated by route / backend / hop,
without ever rendering an absent price or count as zero.

**Architecture:** A new `evals/costs.py` folds rows with `usage._add` (called
through the module, so a patched `_add` is the one used) plus two eval-only
coverage tallies, and renders buckets by `cost.tsx`'s rules. `runner.live`
times the model work, keeps `Decision.calls` and per-item records, and the
report prints a metrics line, a `by task` block and the aggregate block.

**Tech Stack:** Python, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01a-eval-cost-latency-reporting-design.md` §6 (folding, coverage, time, per item), §7, §8 (report), C1, §11 tests 1 (bucket), 2–6, 9, 13–16, 20, 21.

## Global Constraints

- `money`: `>= 0.01` or `== 0` → two decimals, grouped; `>= 0.0001` → four decimals; else `<$0.0001`.
- Labels: `billed $x`, `sub-equiv ~$x`, `modelled ~$x`; a column with no calls is omitted; billed calls = `priced_calls - subscription_calls`.
- `incomplete: N unpriced`, `N had no token counts`, `cost: not reported`, `tokens: not reported`, `(N of M counted)`, `(N estimated)`.
- No total of the three columns anywhere. Wall time and summed `duration_ms` are never conflated; per-hop time is labelled `call time`.
- `route` = `routing.route(task).key`, `embed` for `routing.EMBED_TASKS`, else the task. `backend` = `decision_mode` for a decide row, else the operation. `hop` = row's `hop`, `-` when absent.
- A structured chunk's figures are never apportioned to its items.

## Review Focus

- A billed row at exactly `$0` (a stated free model): prints `billed $0.00`, never `not reported`.
- A bucket with only an unpriced native row and a rate available: stays unpriced (`unpriced_native_calls`).
- A row with a non-string `task` or `operation` from a hand-edited file: aggregates under its text, never crashes.
- A decide case that raised (no `Decision`): metrics from rows alone, no stage breakdown.
- A replay result: no metrics line, no aggregate block entry.

---

### Task 1: `evals/costs.py`

**Files:**
- Create: `evals/costs.py`
- Test: `backend/tests/test_eval_costs.py`

**Interfaces:**
- Produces: `money(usd: float) -> str`; `billed_calls(bucket) -> int`; `column(bucket, key: str) -> str | None`; `cost_text(bucket) -> str`; `tokens_text(bucket) -> str`; `bucket_line(bucket) -> str`; `fold(rows, rates: usage.Rates | None = None) -> dict` (a `_rounded` bucket plus `prompt_counted_calls`, `completion_counted_calls`); `merge(buckets) -> dict`; `route_of(row) -> str`, `backend_of(row) -> str`, `hop_of(row) -> str`; `aggregate(rows, rates) -> list[dict]` of `{"route", "backend", "hop", "bucket"}`.

- [ ] **Step 1: Failing tests** — `money(0.0042) == "$0.0042"`, `money(4e-7) == "<$0.0001"`, `money(0.0001) == "$0.0001"`, `money(1234.5) == "$1,234.50"`; an all-unpriced bucket → `cost: not reported`; billed count derived; subscription `equivalent` row → `sub-equiv` only, billed row on a subscription provider → `billed`; native row with a rate → unpriced and `unpriced_native_calls == 1`; coverage cases of test 16; `usage._add` patched → called (test 9); hop / unrouted-task aggregation (tests 14, 21); no `$0.00` for an unpriced bucket.
- [ ] **Step 2: Run** → fail (`ModuleNotFoundError`). **Step 3: Implement.** **Step 4: Run** → PASS.

### Task 2: Report wiring in `runner`

**Files:**
- Modify: `evals/runner.py` (`Result.wall_ms`, `calls`, `items`, `bucket`, `by_task`; `item_records(decision)`; metrics line; `by task`; aggregate block)
- Test: `backend/tests/test_evals.py`

**Interfaces:**
- Consumes: `costs.*`, `decisions.CallRecord` (S1), `Result.rows` (S2).
- Produces: `Result.wall_ms: int | None`, `Result.calls: tuple[CallRecord, ...]`, `Result.items: tuple[dict, ...]` (`index`, `backend`, `stage`, `reasons`, `distribution`, `escalated`, `call`), `Result.bucket: dict | None`, `Result.by_task: dict[str, dict]`; `item_records(decision) -> tuple[dict, ...]`.

- [ ] **Step 1: Failing tests** — test 1's `bucket["cost_usd"] == 0.0042`; test 2 (no rates: `cost: not reported`, no `$0.00` in the report; with `Rates({"*": ...})`: `modelled ~$`, no `billed`); test 5 (mixed chain item lines name stage 1 for items 1 and 2); test 6 (three structured items share `call`, no money key on an item); test 15 (`by task` block for three tasks); the aggregate block lines.
- [ ] **Step 2: Run** → fail. **Step 3: Implement** — `wall_ms` from `time.monotonic()` around `asyncio.run(run())`; metrics line `wall Xs  calls N (stage S: mode ok/n; …)  tokens …  <cost>`. **Step 4: Run** `tests/test_evals.py tests/test_eval_costs.py` → PASS. Commit `01a-S3: eval cost, latency and token reporting`.

## Plan gate (substitute review, 2026-10-10)

Folded into this slice:

- **B2** The aggregate block re-folds rows and must price them against the
  run's rates. `Result.rates` carries the rates the case was folded with, and
  the aggregate folds each result's rows with its own `rates`.
- **M1** `wall_ms` times the `_ask` coroutine alone, not the client's open
  and close.
- **M5** An item's `call` is the record that answered it: the last record
  carrying it with no error (else the last record carrying it).
- A case with `partial` rows or an unreadable ledger is counted in the
  aggregate as `N case(s) not fully costed`, never silently.
