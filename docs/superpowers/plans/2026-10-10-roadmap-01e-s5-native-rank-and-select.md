# 01e-S5: Native `Rank` and `MultiSelect` through pointwise predicates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `Rank` (with `pointwise`) and a `MultiSelect` are answered on a
native stage: each is lowered to one predicate per candidate or option in ONE
request, and lifted back to a `Ranking` by P(true) tiers or to a thresholded
selection, with the reported P(true)s on `Answer.marginals`. What cannot be
lowered (a rank with no `pointwise`, a lowered id that collides) is refused
unsent and moves on down the chain.

**Architecture:** `decisions.py` only, plus tests and evals: `native_form`
gains the two pointwise lowerings (ids `f"{q.id}#{i}"`), `native_lift` the two
lifts, `native_gap` drops the S2/S3 stubs for the two real refusals, and
`NATIVE_SELECT_TEXT` is the fixed per-option wording. The adapters already go
through `native_form`/`native_lift` (S4), so no adapter changes.

**Tech Stack:** Python 3.11 dataclasses, httpx MockTransport, pytest.

**Spec:** §3.2 (marginals), §3.3, §4.3, §4.4, §6.1 (native half), §9 (C1, C3a, C4), §11 (`test_native_decisions.py` lift cases, `native_gap`, `test_inference_decide_native.py`), slice 01e-S5.

## Global Constraints

- A rank's lowered predicate is worded `f"{rank.pointwise}\n\nCandidate {opt.id}: {opt.description}"`; a select's `NATIVE_SELECT_TEXT.format(instructions=..., id=..., description=...)` with `NATIVE_SELECT_TEXT = "{instructions}\n\nOption {id}: {description}\n\nIs this option one of those selected?"`. No default rank wording.
- The lift reads each lowered predicate's `Answer` as `native_answer` produced it — its `answer`, `reason` and `probability` — and never re-thresholds a raw probability: a select's membership is the predicate's own `True`, its 0.5 is the predicate's own `abstained`.
- Rank lift: every lowered answer `refused` → `refused`; any candidate with no usable probability → `unreadable` (no detail), the reported ones riding in `marginals`; else `Ranking(tiers=tiers(marginals), rest=())`. A P(true) of exactly 0.5 places the candidate. A native rank never abstains.
- Select lift: all `refused` → `refused`; any option with no usable probability → `unreadable` + marginals; any option at 0.5 (its predicate `abstained`) → `abstained` with `allow_none`, else `unreadable` + marginals; else every option whose predicate is `True`, in option order, and a count outside `[min, max]` is `unreadable` + marginals — never repaired.
- `marginals` keys are the caller's candidate/option ids; `distribution` stays None on every rank and select answer; structured answers still carry no marginals.
- `native_gap` refuses only a rank with an empty `pointwise` ("…names no pointwise question; a decisions endpoint cannot order them.") and a lowered id equal to another question's id in the item (or to another lowered id), naming the caller's ids; and still the choice-size rule.
- Pre-01e items still lower to themselves: native bodies byte-identical.

## Design decisions (made here, argued)

- **"No usable probability" for the select**: checked before the 0.5 rule, as §6.1 orders them, so a partly refused selection is `unreadable` rather than `abstained`.
- **Empty `marginals`**: when nothing was reported, `marginals` is None (an empty mapping is "nothing reported" said twice).
- **The collision check** builds every lowered id of the item and refuses the first id that appears twice, naming the question it came from — one rule for "collides with another question id" and for two lowerings colliding.
- **Native eval recordings**: `decide-rank` gains `native` (OpenRouter, every candidate scored) and `decide-select` gains `native` (OpenAI) and `native-undecided` (one option at exactly 0.5, no `allow_none`: `unreadable`, failing `decide.answer`). This is what "the live eval runs … are enabled" can be held to offline: the native path of each case now reads, through the production adapters.
- **01 Appendix B re-check**: both decisions references are re-read and the result recorded in 01's Appendix B (what could not be re-read is said there, not assumed).

## Review Focus

- `#` in an id: a caller id containing `#` is legal; only an actual collision is refused.
- A rank whose lowered predicates are answered as a bool `chosen` with no probability (no adapter does this today): `unreadable`, since its place cannot be computed — consistent with "never re-threshold".
- The OpenAI adapter matches answers by `name`: the lowered names must round-trip (`relevant#0`, …).
- A reply that answers none of the lowered predicates is still a `bad_response` (the item falls through), and one that answers some leaves the others `unreadable` → the rank `unreadable`.

---

### Task 1: lowering, lift, gap

**Files:** `backend/src/grimoire/decisions.py`; `backend/tests/test_decisions.py` (replace the S2/S3 stub tests)

- [ ] **Step 1: Failing tests** — `test_native_form_lowers_a_rank_and_a_select_to_predicates` (ids, wording, `Lift`); `test_native_gap_refuses_a_rank_without_pointwise_and_a_collision`; `test_native_gap_takes_a_pointwise_rank_and_a_select`.
- [ ] **Step 2–4:** implement; run.

### Task 2: both adapters, canned bodies

**Files:** `backend/tests/test_native_decisions.py`

- [ ] Per provider: `decision_body` carries the lowered predicates for a rank and a select; lifts: a rank with a candidate missing → `unreadable` with marginals; all refused (OpenAI) → `refused`; equal P(true) → one tier; 0.5 on a select → `abstained` with `allow_none`, `unreadable` (marginals kept) without; a select outside bounds → `unreadable`, not repaired; a select's membership by its predicates' answers.

### Task 3: the chain

**Files:** `backend/tests/test_inference_decide_native.py`

- [ ] A rank with no `pointwise` on a native primary: refused unsent, answered by the structured fallback stage; with no fallback, `Decision.errors`/the raised error is `native_unrepresentable` naming the rank. A pointwise rank answered natively end to end (FakeLLM's scripted `ItemResult` stands for the adapter, so this one goes through a real `LLMClient` over a `MockTransport`) is out of scope for the chain tests — the adapters' tests hold the lift.

### Task 4: evals

- [ ] Native recordings for `decide-rank` (`native`, OpenRouter) and `decide-select` (`native`, `native-undecided`, OpenAI); README rows updated; run the decide suites, guards, `verify_templates.py`, ratchets, the full backend suite; commit `01e-S5: native Rank and MultiSelect through pointwise predicates`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review of the plan against §3.3, §4.3, §4.4, §6.1, §9 and §11,
and the code at `66735d5` (no Codex CLI, no subagent tool). Findings folded:

- **§11 names `Decision.errors`**, which a one-item batch never reaches: a
  lone failed item makes `decide` raise. Task 3's no-fallback case is a
  two-item batch — a plain predicate the native stage answers beside the
  rank with no `pointwise` — so the `Decision` comes back and its one
  `errors` entry is the rank's `native_unrepresentable`; the one-item raise
  is asserted as well.
- **Task 3's last sentence** read as scope creep; the end-to-end native rank
  is held by the adapter tests over `MockTransport` (Task 2), and the chain
  tests use `FakeLLM`, whose scripted `ItemResult` stands where the adapter
  would. Kept as a statement of where each property is tested.
- **The select's 0.5 rule must read the predicate's own `abstained`**, not
  `probability == 0.5`, to keep M8's "never re-threshold" — stated in the
  constraints; a test feeds a P(true) of exactly 0.5 through each adapter.

## Review and final gate (substitute review, 2026-10-10)

Adversarial self-review of the slice diff against §3.3, §4.3, §4.4, §6.1, §9
(C1, C3a, C4) and §11 stood in for `/codex:review` and the final spec gate
(no Codex CLI, no subagent tool). Each C1/C3a/C4 guarantee was traced to a
test: no tie broken (equal P(true) is one tier, and `flat()` raises on it);
a native rank with a candidate unanswered or partly refused is `unreadable`
with the reported marginals, a fully refused one `refused`; a rank with no
`pointwise` is refused unsent through both adapters and in the chain, where
the structured fallback answers it, and with no fallback the
`Decision.errors` entry (two-item batch) and the lone raise are
`native_unrepresentable`; a select at 0.5 is `abstained` only with
`allow_none`; an out-of-bounds select is never repaired; marginals never
appear on a structured answer, and a rank or select never carries a
distribution.

Folded:

- **`test_lowering_retired_guard.py` failed the full suite**: the helper
  that builds a question's lowered form was named `_lowered`, a name the guard
  reserves for the retired connection-dict lowering. Renamed `_asked_as`.
- The spec's implementation rule asks for both decisions references to be
  re-read: done 2026-10-10 and recorded in 01's Appendix B (OpenRouter: still
  three types, no per-request bound; OpenAI's guide: still three types, no
  limits stated; its API reference URL now 404s, so the 255 bound stands on
  the 2026-10-08 reading). The structured-output references were not re-read.

Not folded, for the coordinator: `CLAUDE.md` still says `native_gap` refuses
"today a nullable choice whose options plus the reserved none pass 255"; it
now also refuses a rank with no `pointwise` and a lowered-id collision. This
agent does not edit `CLAUDE.md`.

Results: decide suites, the eval suites and `test_llm_fakes.py` 957 passed,
2 skipped (before the rename; the renamed module's suites re-run green);
full backend suite `-n 4`: 16454 passed, 5 skipped, 2 failed — the known
root-container `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`
and the lowering guard, fixed above and re-run green. `verify_templates.py`
439 checks; ruff and mypy at baseline.
