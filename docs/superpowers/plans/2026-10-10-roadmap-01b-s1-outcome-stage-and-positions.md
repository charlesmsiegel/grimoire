# 01b-S1: Stage and batch positions on the decide outcome — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every capture outcome a decide call hands its `capture` names the
stage that made the call and the batch indices it carried, so one entry can
tell a fallback stage's answer from the primary's failure.

**Architecture:** 01a-S1 already landed `_Call.stage`, `_Call.positions` and
`_Call.batch()`, set per stage in `run_stages`. This slice only stamps them:
`inference._outcome` gains the call and the unit's stage positions and adds
`stage` and `at` to the `decisions.outcome` dict it returns. `decisions.py`
does not move (01e edits it concurrently). The decide-chain golden is
re-recorded once, argued by spec §3.2, and its diff is checked to add only the
two keys.

**Tech Stack:** Python 3.11, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01b-decision-capture-design.md` §3.2, Slices (01b-S1), §6 test 11.

## Global Constraints

- Reuse 01a's `_Call.stage/positions/batch()`; add no new `_Call` field.
- `at` is a JSON list of BATCH indices (`call.batch(unit)`), never stage
  positions and never named `items` (the outcome's per-item results).
- A failed call's outcome gains the same two keys; its `error` is untouched
  here (sanitising is S2's, in `decision_capture`, so the golden does not move
  for it).
- A capture call record (one per structured chunk, re-send folded in; one per
  native item) and a 01a `CallRecord` (one per metered request) count
  different things; both stay.
- Nothing else in the golden may move: it is re-recorded under §3.2 only.

## Review Focus

- A native item of stage 1: `at == [batch index]`, not `[0]`.
- A structured chunk past the first (offset 8): `at == [8]`.
- A call refused unsent (`messages=[]`) still names stage and `at`.

---

### Task 1: stamp `stage` and `at` in `_outcome`

**Files:**
- Modify: `backend/src/grimoire/inference.py` (`_outcome`, its two `partial` call sites in `_structured` and `_native`)
- Modify: `backend/tests/fixtures/decide_chain_golden.json` (re-recorded)
- Test: `backend/tests/test_inference_decide.py`, `backend/tests/test_inference_decide_native.py`, `backend/tests/test_character_turns.py`

**Interfaces:**
- Produces: the capture outcome dict gains `"stage": int` and `"at": list[int]` (01b-C1 part).

- [x] **Step 1: Failing tests.**
  - structured: `test_each_chunks_capture_names_its_stage_and_batch_indices` — 9 items through a recording capture → two outcomes, `stage == 0`, `at == [0..7]` and `[8]`.
  - native: `test_a_fallback_stages_capture_names_batch_indices` — the mixed chain of 01a's `test_a_mixed_chain_names_each_calls_stage_and_batch_items` with a capture → the four sent stage-0 items' outcomes `at == [i]`; the stage-1 outcome `stage == 1`, `at == [0, 4, 5, 6, 7, 8, 9]` (the failed item and those held back).
  - `test_the_selector_capture_records_the_decision` expects `"stage": 0, "at": [0]`.
- [x] **Step 2: Run** them → fail (`KeyError`/mismatch).
- [x] **Step 3: Implement.** `_outcome(call, unit, mode, target, holder, results, error)` returns `{**decisions.outcome(...), "stage": call.stage, "at": list(call.batch(unit))}`; `_structured` passes `call, unit`, `_native` passes `call, (index,)`.
- [x] **Step 4: Re-record the golden.** Save the old file aside, delete it, run `GRIMOIRE_RECORD_DECIDE_GOLDEN=1 pytest tests/test_decide_chain_golden.py -k record`, then a scratch script decodes both, strips `stage`/`at` from every new capture outcome and asserts equality with the old run by run (the digest keys change; the runs must not).
- [x] **Step 5: Run** decide tests, golden, character_turns, eval tests, guards; ruff/mypy ratchets.
- [x] **Step 6: Commit** `01b-S1: stage and batch indices on each decide capture outcome`.

## Plan gate (substitute review, 2026-10-10)

The Codex CLI is not installed and no subagent tool is available to this
session, so a rigorous adversarial self-review against the spec and the code
stood in for `/codex:adversarial-review`. Findings folded:

- The golden's captures are stored as a multiset (`_counted`): a nine-item
  native run used to file nine identical captures, which now differ by `at`.
  "The diff shows only `stage` and `at`" is therefore checked by stripping
  both keys AND re-counting each run's captures, not by a line diff (Step 4).
- `_golden()` shares one decoded record between runs with the same digest, so
  the check deep-copies before stripping.
- Six existing tests pin a capture outcome by exact equality
  (`test_inference_decide*.py`, `test_character_turns.py`); each gains the two
  keys and nothing else, as §3.2 says every existing capture does.
- No finding against reusing 01a's fields: `_Call.batch()` already maps a
  stage's positions to batch indices, so S1 adds no field.

## Review and final gate (substitute review, 2026-10-10)

Self-review of the diff against §3.2 and the slice's acceptance (no subagent
or Codex available):

- Acceptance "the golden's diff shows only `stage` and `at`": a scratch check
  decoded the old and new files, stripped the two keys from every capture
  outcome, re-counted, and found all 108 runs identical (every run moved, as
  each has at least one capture). The golden's docstring records the one
  re-recording and its argument.
- Acceptance "the selector capture expectations gain the two keys": done
  (`"stage": 0, "at": [0]`).
- Acceptance "the two continuity sites capture nothing yet": unchanged code.
- `decisions.py` untouched (01e edits it concurrently); the inference diff is
  `_outcome` and its two `partial` call sites only.
- Results: decide, native, selector, golden, evals and the guard tests pass
  (637 passed); ruff and mypy ratchets all at baseline.
