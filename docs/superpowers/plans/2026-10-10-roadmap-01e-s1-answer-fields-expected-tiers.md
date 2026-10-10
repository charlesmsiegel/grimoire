# 01e-S1: Answer fields, `expected` and `tiers` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Answer` can carry the two reports the new vocabulary needs
(`marginals`, `expected`); a native `Score` answer carries its
probability-weighted level; and one grouping rule, `decisions.tiers`, turns any
mapping of scores or probabilities into tied tiers without ever breaking a tie.

**Architecture:** `decisions.py` only. `Answer` gains `marginals` and
`expected`, checked in `__post_init__`. `native_answer` computes `expected`
for a `Score` from the validated distribution. `tiers` is a pure function next
to `MASS_TIE`. `outcome` writes the two fields under `_present`'s rule.

**Tech Stack:** Python 3.11 stdlib dataclasses, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01e-decision-vocabulary-design.md` §3.2, §5.1, §5.2, §8 (`outcome`), §11 (`tiers`, `expected`, `outcome`), slice 01e-S1.

## Global Constraints

- `decisions.py` imports nothing from the package.
- No adapter, template or call site changes; `test_native_decisions.py` and the decide gate stay unchanged.
- `expected` is computed from the reported distribution only, never from a provider's `score` field; normalised by the reported mass (an omitted level contributes to neither sum); `None` without a valid distribution or with zero mass; it rides on an abstained (tied) answer.
- Every structured answer has `marginals is None` and `expected is None` (`parse` never sets them).

## Design decisions (made here, argued)

- **`expected` is `field(compare=False)`**, as `stated` is. It is a pure function of `distribution` (which *is* compared) and the question, so it adds no information to equality — and comparing it would change every existing native score answer's equality (`Answer(2, distribution=d)` in `test_native_decisions.py`), which the slice's acceptance forbids. `marginals` is compared: it is independent information.
- `marginals` validation: a mapping whose keys are strings and whose values are probabilities (`_probability`), else `ValueError`. Stored as a plain `dict`.
- `expected` validation: a real number (not a bool), finite, else `ValueError`. "Only on a `Score`" is `native_answer`'s rule, since `Answer` does not know its question.
- `tiers` refuses a value that is not a finite real number (`ValueError`): a NaN cannot be placed, and a silent placement would be a fabricated order. Comparison is inclusive (`top - v <= tolerance`), as `regrouped`'s `>= top - MASS_TIE` is. Within a tier, keys keep the mapping's order.

## Review Focus

- A distribution that omits a level: `expected` divides by the reported mass, never by 1.
- A zero-mass distribution (`{"0": 0.0, "1": 0.0}`) is valid for `_distribution` but has no `expected`; the answer is still `abstained` (a tie).
- `refused=True`: no report at all, so no `expected`.
- `tiers` greedy anchor: `{a: 3, b: 2.1, c: 1.2}` at tolerance 1 → `((a, b), (c,))`, never one tier.

---

### Task 1: `Answer.marginals` / `Answer.expected`, `native_answer`'s `expected`

**Files:**
- Modify: `backend/src/grimoire/decisions.py` (`Answer`, `native_answer`, new `_expected`)
- Test: `backend/tests/test_decisions.py`

- [ ] **Step 1: Failing tests**
  - `test_answer_checks_its_new_reports`: marginals with a value > 1, a bool, NaN or a non-string key raise; `expected` NaN / inf / bool raise; valid ones construct; `expected` does not take part in equality, `marginals` does.
  - `test_native_score_expected_is_the_mass_normalised_level`: `{"0": .1, "1": .2, "2": .7}` → 1.6; `{"1": .2, "2": .2}` (level 0 omitted) → 1.5 and abstained; zero mass → None; no distribution → None; `chosen=1.4` (a provider weighted score) with a distribution → expected from the distribution; predicate/choice answers never carry `expected`; refused carries none.
  - `test_a_structured_answer_carries_no_marginals_or_expected`: every answer `parse` returns for `_item()`.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.** **Step 4: Run** → pass.

### Task 2: `tiers`

- [ ] **Step 1: Failing tests** `test_tiers_*`: exact ties keep mapping order; ties within `MASS_TIE`; the greedy anchor rule (three values each just inside tolerance of the next do not collapse); integer levels; empty mapping → `()`; NaN / bool → `ValueError`.
- [ ] **Step 2–4:** implement `tiers(values, *, tolerance=MASS_TIE)`; run.

### Task 3: `outcome` writes the new fields

- [ ] **Step 1: Failing test** `test_outcome_writes_marginals_and_expected`: a native score answer with `expected` writes it beside `distribution`; an answer with `marginals` writes them; `test_outcome_omits_empty_fields` stays unchanged (its answers carry neither).
- [ ] **Step 2–4:** add both under `_present`; run `tests/test_decisions.py tests/test_native_decisions.py tests/test_decide_gate.py tests/test_inference_decide*.py`.
- [ ] **Step 5: Commit** `01e-S1: Answer.marginals and expected, native Score expected, decisions.tiers`.

## Plan gate (substitute review, 2026-10-10)

The Codex CLI is not installed and this session has no subagent tool, so a
rigorous adversarial self-review of the plan against the spec (§3.2, §5, §8,
§11, slice S1) and the code at `414c7eb` stood in for
`/codex:adversarial-review`. Findings folded:

- **The acceptance line "`test_native_decisions.py` unchanged" collides with
  setting `expected` on every native score**: `Answer(2, distribution=d)` is
  compared by equality there and in `test_decisions.py`. Folded as the
  `compare=False` decision above, argued from `expected` being a pure function
  of the compared `distribution`.
- `refused=True` returns before any report is read, so a refused score has no
  `expected` — stated as a review focus and tested.
- A zero-mass distribution passes `_distribution` (every value is a
  probability); `expected` must not divide by zero — tested.
- No finding on `tiers`: the spec's greedy top anchor, the inclusive
  tolerance (matching `regrouped`) and mapping order within a tier are each
  pinned by a test.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against §3.2, §5.1, §5.2, §8 and
§11 stood in for `/codex:review` (no Codex CLI, no subagent tool). Folded: a
level index of an int too large for a float made `math.isfinite` raise, so the
finiteness check (`_finite`) converts under `OverflowError` and reads such a
value as not finite, for `expected` and for `tiers` alike. Checked and left:
`outcome` keeps an `expected` of `0.0` (only None and empties are dropped);
`expected` rides on the abstained tie and on a chosen level, never on a refusal;
no existing answer's equality or capture changes (no call site asks a `Score`).
Results: the decide suites (`test_decisions.py`, `test_native_decisions.py`,
`test_inference_decide*.py`, `test_decide_gate.py`,
`test_decide_chain_golden.py`, `test_adapter_wire_golden.py`,
`test_evals.py`) 540 passed, 2 skipped; ruff and mypy all at baseline.
