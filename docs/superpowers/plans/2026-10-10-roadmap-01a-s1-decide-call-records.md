# 01a-S1: Decide call records — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every metered request a decide chain makes is described by a
`decisions.CallRecord` on `Decision.calls`, so a ledger row can be read
against the batch items it answered.

**Architecture:** `inference._Call` gains `stage` and `positions` (shared with
01b). `_once` and the native `one` each append one `CallRecord` per metered
request; `_Answered.calls` carries them out of a stage and `run_stages`
concatenates them onto `Decision.calls` in settle order. Nothing in
production reads the new field.

**Tech Stack:** Python 3.11 stdlib dataclasses, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01a-eval-cost-latency-reporting-design.md` §5, §9 (Shared structures), §11 (`test_inference_decide*.py`).

## Global Constraints

- `decisions.py` imports nothing from the package; `CallRecord` is a plain frozen stdlib dataclass.
- A failed call keeps `error_kind` and `error_status` only, never `LLMError.detail`.
- `hop` is reserved and always `""` in this slice (01d sets `"escalation"`).
- `Decision.usage` and every caller stay unchanged; the decide-chain golden does not move.
- One record per *metered request*: a schema-refusal re-send is a record of its own.

## Review Focus

- A call refused unsent (`native_unrepresentable`, a refused clock): a record with `row=None` and its error kind/status.
- An item held back after a connection-wide stop: no record at all.
- A native item of a later stage: `items` are batch indices (`positions`), not stage positions.
- Concurrent native items: records are in settle order, which need not be item order — tests must not assume item order there.
- A cancelled batch: no `Decision`, so nothing to assert; records must not break the cancel path (append only after the meter settles without a non-`LLMError` exception).

---

### Task 1: `CallRecord`, `_Call.stage/positions`, `Decision.calls`

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (new `CallRecord` before `Decision`; `Decision.calls`)
- Modify: `backend/src/grimoire/inference.py` (`_Call`, `_Answered`, `_once`, `_ask`, `_structured`, `_native`, `run_stages`)
- Test: `backend/tests/test_inference_decide.py`, `backend/tests/test_inference_decide_native.py`

**Interfaces:**
- Produces: `decisions.CallRecord(stage: int, mode: str, items: tuple[int, ...], row: dict | None, error_kind: str = "", error_status: int | None = None, hop: str = "")`; `Decision.calls: tuple[CallRecord, ...] = ()`; `_Call.stage: int = 0`, `_Call.positions: tuple[int, ...] = ()`; `_Answered.calls: tuple[CallRecord, ...] = ()`.

- [ ] **Step 1: Failing tests** (structured file)
  - `test_each_structured_chunk_is_one_call_record`: 9 items → two records, `stage == 0`, `mode == "structured"`, `items == (0..7)` and `(8,)`, `row` equal to the two ledger rows, `hop == ""`, no error.
  - `test_a_schema_refusal_re_send_is_its_own_call_record`: the refused-schema setup → two records over `(0,)`: the first `error_kind == "bad_response"` with the provider's status, `row["status"] == "error"`; the second no error.
  - `test_a_failed_call_record_carries_kind_and_status_but_no_detail`: a failed chunk beside an answered one; the failed record's `error_kind == "network"`, and `"connection reset"` appears in no field of `dataclasses.asdict(record)`.
- [ ] **Step 2: Failing tests** (native file)
  - `test_each_native_item_is_one_call_record`: three items → three records, `mode == "native"`, `sorted(r.items for r in calls) == [(0,), (1,), (2,)]`.
  - `test_a_native_item_refused_unsent_has_no_row`: an item past `NATIVE_MAX_OPTIONS` beside an answerable one → its record has `row is None`, `error_kind == "bad_response"`.
  - `test_no_call_record_for_an_item_held_back`: a connection-wide failure (`auth`) on item 0 with `NATIVE_CONCURRENCY` patched to 1 → one record only.
  - `test_a_mixed_chain_names_each_calls_stage_and_batch_items`: native stage 0 fails items 1 and 2 of 3, structured stage 1 answers them → stage-1 record has `stage == 1`, `items == (1, 2)`.
- [ ] **Step 3: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_inference_decide.py tests/test_inference_decide_native.py -q` → the new tests fail (`AttributeError: calls`).
- [ ] **Step 4: Implement.** `_once` takes keyword `records: list` and `items: tuple[int, ...]` and appends one record after its meter settles (kind/status from the `LLMError`, `row=m.row`); `_ask` threads them through. `_structured` maps each chunk's stage positions through `call.positions` (identity when empty). `_native`'s `one` appends after its `try/finally` (never on a non-`LLMError` exception or cancel). `run_stages` builds each stage's call with `replace(call, chain=..., retries=..., stage=i, positions=tuple(pending))` and passes `calls=` to `Decision`.
- [ ] **Step 5: Run** the two files plus `tests/test_decide_*` and `tests/test_evals.py` → PASS.
- [ ] **Step 6: Commit** `01a-S1: decide call records on Decision.calls`.
