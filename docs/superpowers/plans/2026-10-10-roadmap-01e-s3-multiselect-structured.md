# 01e-S3: `MultiSelect` on the structured path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A caller can ask for any subset of given options and get the chosen
ids back in its own option order, with the empty selection a real answer and
`None` + `abstained` the only "cannot say"; a native stage refuses it unsent
until S5.

**Architecture:** `decisions.py` gains `MultiSelect` (`KIND = "select"`),
`MAX_SELECT_OPTIONS`, `_check_select`, the array schema S2 built for a rank,
`_read_select` over S2's `_read_listed`, the `enum_values` branch, the
`outcome`/`render` spelling of a selection (a list) and the `native_gap` stub.
`decide/user.j2` gains the select branch, `decide/system.j2` the selection
bullet beside a select only. One offline eval case.

**Tech Stack:** Python 3.11 dataclasses, Jinja2, pytest.

**Spec:** §3.1 (a selection's answer value and option order), §6.1 (structured half), §7, §8, §11, slice 01e-S3.

## Global Constraints

- `max=None` means up to the option count; `max=0` means zero, never "all": the upper bound is `hi = n if max is None else max`, checked `0 <= min <= hi <= n`.
- `()` is an answer ("none of these") and is never `None`; `None` + `abstained` only with `allow_none`; without it a null is `unreadable`.
- An unknown entry is `NOT_AN_OPTION` (no `stated`); a duplicate, or a count outside `[min, hi]`, is `unreadable` with no detail — never repaired (no truncation to `max`, no padding to `min`).
- The answer is the named ids in the CALLER's option order, whatever order the reply listed them in.
- Existing decide prompts byte-identical (re-diffed); the selection bullet renders only beside a select.

## Design decisions (made here, argued)

- The field names `min`/`max` are the spec's; they shadow builtins only as dataclass attributes (`self.min`), and `_check_select` reads them as `q.min`/`q.max`.
- The bounds render in the question line as `, at least {min}` (only when `min > 0`) and `, at most {max}` (only when `max` is set, `0` included, so `max=0` says so) — "plus the bounds when they are set" (§6.1).
- The eval case's task is `continuity-identity`, on the same reasoning as S2's.
- The case sets `min=1` so its under-length counterexample (`[]`) is a real bound violation; the empty selection as an answer is pinned in `test_decisions.py`.

## Review Focus

- `max=0`: `[]` is `()`; `["x"]` is `unreadable`.
- A reply listing options out of order returns them in option order.
- `min > max`, `max > n`, a bool or negative bound, 0 or 33 options: each refused.
- `render` of `()` is `[]` (not null), and parses back to `()`.

---

### Task 1: type, validation, schema, parse, spellings, stub

**Files:** `backend/src/grimoire/decisions.py`; `backend/tests/test_decisions.py`

- [ ] **Step 1: Failing tests** `test_validate_select_bounds`, `test_select_schema_is_an_array_of_its_ids`, `test_enum_values_counts_select_options`, `test_parse_select_*` (option order; empty is `()`; `max=0`; out of bounds; duplicate; unknown; null with/without `allow_none`; non-list), `test_outcome_and_render_spell_a_selection`, `test_native_gap_names_a_select`; extend `test_question_kinds_are_class_constants` and `test_schema_uses_only_the_shared_subset`.
- [ ] **Step 2–4:** implement; run.

### Task 2: templates

- [ ] `user.j2` select branch; `system.j2` selection bullet under `"select" in kinds`; `verify_templates.py` `_DECIDE_BULLETS["select"]` and a select in `_DECIDE_NEW_ITEMS`; `test_inference_decide.py` line/bullet test and `NEW_BULLETS["select"]`; re-diff the old renders.

### Task 3: the offline select eval case

- [ ] `decide-select`: the characters present who witnessed Seraphine palm the harbour key (placeholder names only), `min=1`; recordings compliant, `duplicate`, `unknown-id`, `short` (`[]`), `null`, each counterexample failing exactly `decide.answer`; `VOCABULARY_READINGS["decide-select"]`; README row.
- [ ] Run the decide suites, `verify_templates.py`, ratchets; commit `01e-S3: MultiSelect on the structured path`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review of the plan against §3.1, §6.1, §7, §8, §11 and slice
S3, and the code at `4e21bd7` (no Codex CLI, no subagent tool). Findings
folded:

- **`max=0` rendering**: a Jinja truthiness test (`{% if q.max %}`) would
  drop a `max` of 0 from the question line and tell the model nothing about
  the bound it is held to; the line tests `q.max is not none`.
- **`render` of the empty selection**: `_rendered` must pass `()` through as
  `[]`, not treat an empty value as missing — a null would read back as
  `unreadable`/`abstained` and turn "none of these" into "cannot say", the
  exact confusion §6.1 forbids. Added to Review Focus and to the spellings
  test.
- No finding on the bounds arithmetic or option order.

## Review and final gate (substitute review, 2026-10-10)

Adversarial self-review of the slice diff against §3.1, §6.1, §7, §8 and §11
(no Codex CLI, no subagent tool). Folded: two test spellings that assumed
`normalise` rewrites a space into the `:` of a ref (it only collapses spaces
and hyphens to `_`) were rewritten to exercise casefolding, which is what the
alias collision and the normalised match actually go through; ISC004 on the
select grader's detail string. Checked and left: `_rendered`/`_recorded`
spell `()` as `[]` and it parses back to `()` (tested); `max=0` says
`at most 0` in the line (tested); the old-kind renders are still
byte-identical (`cmp` against the S2 baseline). Results: decide suites plus
`test_llm_fakes.py` 616 passed, 2 skipped; `verify_templates.py` 431 checks;
ruff and mypy at baseline.
