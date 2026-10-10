# 01e-S2: `Rank` on the structured path, and the template switch to `KIND` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A caller can ask a `Rank` and get a `Ranking` back from a
structured stage; a native stage refuses it unsent (the stub until S5); and
the decide templates branch on a `KIND` class constant, so a new question type
can never render as a choice by accident.

**Architecture:** `decisions.py` gains `Rank`, `Ranking`, `KIND` `ClassVar`s
on every question class, `MAX_RANK_CANDIDATES`, the array-of-enum schema,
`_read_rank`, an `enum_values` branch, the `outcome`/`render` spellings, the
`native_gap` stub, and `kinds(items)`. `inference.structured_messages` passes
`kinds` to `decide/system.j2` (one keyword; nothing else in `inference.py`
moves), whose ranking bullet renders only when the batch holds a rank.
`decide/user.j2` branches on `q.KIND`. One offline eval case.

**Tech Stack:** Python 3.11 dataclasses, Jinja2, pytest.

**Spec:** §3.1, §4.1, §4.2, §5.3, §7, §8, §10, §11 (`validate`, `schema`, `parse`, `Ranking.flat`, `enum_values`, `outcome`/`render`, prompts), slice 01e-S2.

## Global Constraints

- `decisions.py` imports nothing from the package; `Rank`/`Ranking` are frozen stdlib dataclasses.
- The schema uses only the documented shared subset plus `array`/`items`; never `minItems`, `maxItems` or `uniqueItems` (Anthropic refuses array constraints beyond `minItems` 0/1; OpenAI documents no `uniqueItems`). The parser enforces count and uniqueness.
- A structured rank sets no `stated` (as a structured choice sets none) and no `marginals`/`expected`.
- An unknown entry is `unreadable` + `NOT_AN_OPTION` and is never dropped; a duplicate entry, or fewer distinct candidates than `top` (or all, when `top` is None), is `unreadable` with no detail. Nothing is repaired.
- Every existing decide prompt (predicates, choices, scores) renders byte-identically: verified by diffing a render of every branch of `verify_templates.py`'s `_DECIDE_ITEMS` before and after (`scratchpad/render_decide.py`), and pinned by a test that an old-kind batch's prompt carries no new bullet.
- `KIND` is `ClassVar[str]`: neither a constructor nor an equality field.

## Design decisions (made here, argued)

- **`NativeQuestion = Predicate | Choice | Score`** is the type a decisions endpoint asks (`native_answer`, `_distribution`, `_legal_keys`). `Question` widens; the native readers keep the narrow type, so mypy proves a rank never reaches `native_answer`.
- **`kinds(items)`** is the tuple of `KIND`s the batch asks, first appearance first; `system.j2` tests membership. Passed as a template variable rather than derived from the schema in Jinja, which would re-implement the type switch in a template.
- **`Ranking.flat` excludes `rest`.** `rest` is the explicit unranked bottom group (§5.3), not a tier; folding it in by input order would be exactly the silent tie-break §3.1 forbids. A caller that wants the unranked candidates appends `rest` in an order it names. `position` is the tier index; None for a `rest` candidate and for a non-candidate.
- **The ranking bullet goes between the choice and scale bullets**, ending `;`, so the scale bullet stays last with its `.` and the text around it does not move when no rank is asked.
- **The eval case's task** is `continuity-reconcile`: no call site asks a `Rank`, a live run has to resolve a decide task through the Decision role, and a route may not be added for an eval (`test_routing_guard.py`). The task names only where the call is metered.
- **`_option_named(options, value)`** is the exact-then-normalised match, now shared by `_read_choice` (behaviour unchanged) and the rank's entries.

## Review Focus

- Unknown and duplicate in one reply: the unknown wins (`NOT_AN_OPTION`) — a value naming no candidate was given, which is the more specific reading.
- `top` given and the model ranks more than `top`: a valid answer; everything listed is a tier, `rest` is what was left out.
- A non-list (`"a, b"`, an object) is `unreadable`; an entry that is not a string is `NOT_AN_OPTION` (a value present, naming no candidate), as `_read_choice` reads a non-string.
- `top` bounds: a bool or 0 or more than the candidate count is refused.
- `test_llm_fakes.py` cassettes are keyed on the decide system prompt: no bullet may render beside their predicates/choices.

---

### Task 1: types, validation, schema, `enum_values`

**Files:** `backend/src/grimoire/decisions.py`; `backend/tests/test_decisions.py`

- [ ] **Step 1: Failing tests**
  - `test_question_kinds_are_class_constants`: each class's `KIND`; not a dataclass field; `kinds()` of a mixed batch.
  - `test_validate_rank_bounds`: 2 and 32 candidates pass; 1 and 33 refused; `top` 0, 4 of 3, `True` refused; 1 and n pass; a candidate alias colliding once normalised refused; `NONE_KEY` as a candidate refused.
  - `test_rank_schema_is_an_array_of_its_ids` (plain and nullable `anyOf`); `test_schema_uses_only_the_shared_subset` extended with a rank (the walker admits `array` and walks `items`; no array bounds keyword anywhere).
  - `test_enum_values_counts_rank_candidates`; `schema_chars` counts candidate ids.
- [ ] **Step 2–4:** implement; run.

### Task 2: parse, `Ranking`, `outcome`/`render`, `native_gap`

- [ ] **Step 1: Failing tests**
  - `test_parse_rank_*`: exact, aliased and normalised entries; unknown → `NOT_AN_OPTION` with no `stated`, `was_read`; duplicate → `unreadable` no detail, `was_read`; under `top` / under all → `unreadable`; null with `allow_none` → `abstained`, without → `unreadable`; a string → `unreadable`; `top` with extra listed; rest in input order.
  - `test_ranking_flat_and_position`: singleton tiers flatten; a tie without tiebreak raises `ValueError`, with one sorts the tier; `rest` excluded; `position`.
  - `test_outcome_and_render_spell_a_ranking`: outcome `{"tiers": [[...]], "rest": [...]}`; render a singleton ranking as its id list (round-trips through `parse`) and a tied one as null.
  - `test_native_gap_names_a_rank`.
- [ ] **Step 2–4:** implement; run.

### Task 3: templates

**Files:** `templates/decide/user.j2`, `templates/decide/system.j2`, `backend/src/grimoire/inference.py` (`structured_messages` passes `kinds`), `scripts/verify_templates.py`

- [ ] **Step 1:** render every `_DECIDE_ITEMS` branch before the change (saved).
- [ ] **Step 2: Failing tests** (`test_inference_decide.py`): `test_a_rank_renders_its_line_candidates_and_bullet`; `test_an_old_kind_batch_carries_no_new_bullet`.
- [ ] **Step 3:** switch `user.j2` to `q.KIND`, add the rank branch; add the conditional bullet to `system.j2`; pass `kinds`; `verify_templates.py` passes `kinds` to its direct render and gains a second set (`_DECIDE_NEW_ITEMS`) whose system render must hold the ranking bullet.
- [ ] **Step 4:** re-render and diff against Step 1 (byte-identical); run `verify_templates.py`, `test_llm_fakes.py`, `test_inference_decide.py`.

### Task 4: the offline `Rank` eval case

**Files:** `evals/cases.py`, `evals/recordings/decide-rank.*.json`, `evals/README.md`, `backend/tests/test_evals.py`

- [ ] **Step 1:** `decide-rank`: five Saltmarch scenes (synthetic, placeholder names) for a turn about Mara, `top=3`, no `allow_none`. Prompt checks: the question line, every candidate line, the ranking bullet, the schema. `decide.answer`: a `Ranking` whose first tier is the expected best and whose first three ranked candidates are the expected three. Recordings: compliant; `duplicate`, `unknown-id`, `short`, `null` each failing exactly `decide.answer`.
- [ ] **Step 2:** `test_evals.py`: the decide-case roster names `decide-rank`; a test reads each counterexample through `decisions.parse` and asserts its reading (`unreadable`/no detail, `NOT_AN_OPTION`, `unreadable`, `unreadable`).
- [ ] **Step 3:** run `test_evals.py`; ratchets; commit `01e-S2: Rank on the structured path, and the decide templates branch on KIND`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review of the plan against the spec (§3.1, §4, §7, §8, §10,
§11, slice S2) and the code at `d08b9bb` (no Codex CLI, no subagent tool).
Findings folded:

- **Missing scope item**: the slice also updates the module docstring's
  schema-subset rule (`array`, `items`) and nesting note (five levels: batch,
  item, answers, array, item string) — added to Task 1.
- **Typing hole**: widening `Question` makes the adapters'
  `_TYPES[type(q)]` and `native_answer(q, ...)` calls accept a rank in the
  type system. The narrow `NativeQuestion` alias is the answer for
  `decisions.py`; the adapters must still type-check at the mypy baseline
  (added to Review Focus: `scripts/ratchet.py mypy` all at baseline).
- **Silent flatten via `rest`**: folded as the `Ranking.flat` decision above.
- Checked, no change: the capture (`inference._native_request`) never builds
  a body for an item `native_gap` refused (`m.usage` is empty before the
  stamp), so an adapter never meets a rank before S4's lowering.

## Review and final gate (substitute review, 2026-10-10)

Adversarial self-review of the slice diff against §3.1, §4.1-4.2, §7, §8, §10
and §11 stood in for `/codex:review` and the final spec gate (no Codex CLI, no
subagent tool). Folded during review:

- **mypy caught the typing hole the plan gate predicted**: both adapters'
  `_answer` passed a `Question` (now possibly a `Rank`) to `native_answer` and
  `_score_distribution`. Fixed with `decisions.native_questions(item)`, which
  both adapters' `decision_body` and `decision_result` iterate, typed
  `NativeQuestion` and raising `ValueError` on any other kind (an invariant:
  `native_gap` refuses such an item unsent). This touches the adapters one
  slice before §3.3's two-line change; S4 builds on the same call. The wire
  bodies are unchanged (`test_adapter_wire_golden.py`, `test_native_decisions.py`
  green and unedited).
- ISC004 on the eval case's question line (an implicit concatenation inside a
  list) — parenthesised.
- Byte-identity: every branch of `verify_templates.py`'s `_DECIDE_ITEMS` plus
  a choice-only batch, rendered through `structured_messages` before and after
  the template change, compared equal with `cmp`; `verify_templates.py` now
  also requires each 01e bullet to be absent beside old kinds and present
  beside its own (417 checks pass).

Checked and left: an entry `null` inside a ranking list reads `NOT_AN_OPTION`
(a present entry naming no candidate) rather than plain `unreadable`; an
unknown entry beside a duplicate reads `NOT_AN_OPTION`. Neither is specified
by §4.2 beyond its separate rules, and both are pinned by tests.

**Integration note for the coordinator:** 01f adds a portable-schema module
(`grimoire/schemas.py`). The decide batch schema now uses `type: "array"` with
`items` (no array bounds); if 01f's module validates decide schemas against a
subset, it must admit exactly that.

Results: the decide suites plus `test_llm_fakes.py` and the routing,
operation, import, pydantic, paths and atomic guards: 811 passed, 2 skipped;
`verify_templates.py` 417 checks pass; ruff and mypy all at baseline.
