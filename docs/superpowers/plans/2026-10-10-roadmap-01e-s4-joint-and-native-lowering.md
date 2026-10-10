# 01e-S4: `Joint` on both paths, and the native lowering framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A caller can ask one action plus a target conditioned on it as ONE
question, answered by a `Pair`, on a structured stage and on a native one —
where its answer carries a joint distribution over legal pairs keyed by the
pair's one spelling. Both adapters gain the lowering seam (`native_form` /
`native_lift`) S5's pointwise lowerings plug into.

**Architecture:** `decisions.py` gains `JOINT_SEP`, `Pair` (`.key`),
`joint_key`, `split_joint`, `Joint` (`KIND = "joint"`, `.choice` the
flattened `Choice`), `joint_choice`, `head_marginal`, `head_first`, `Lift`,
`native_form` and `native_lift`. A joint's schema, parse and `enum_values`
all go through its flattened choice. Both adapters' `decision_body` iterate
`native_questions(native_form(item)[0])`, and `decision_result` reads the
reply against the lowered item and returns `native_lift(item, result, lift)`.
`native_gap` checks a joint's flattened choice against `NATIVE_MAX_OPTIONS`.

**Tech Stack:** Python 3.11 dataclasses, Jinja2, httpx MockTransport, pytest.

**Spec:** §3.1, §3.3, §6.2, §7, §8, §9 (C3b), §11 (joint tests, `chunks`, `test_native_decisions.py`), slice 01e-S4.

## Global Constraints

- `decisions.py` imports nothing from the package.
- Every pre-01e item lowers to itself (`native_form(item)[0] == item`), so every existing native body is byte-identical (`test_native_decisions.py`'s and `test_adapter_wire_golden.py`'s pinned bodies, unedited).
- `Pair.key` / `joint_key` / `split_joint` are the one spelling of a pair: the option id, the distribution key, the capture, the render. A `Pair` is never written as a two-element list.
- `validate` refuses: `JOINT_SEP` in a head or tail id; any alias on a head or tail; a `tails` key naming no head; a head listed twice in `tails`; a flattened option count outside 2-255 (1-255 with `allow_none`); a malformed `tails` shape. Everything else is `_check_choice` on the flattened choice.
- `head_marginal` and `head_first` exclude `NONE_KEY`; `head_first` applies `regrouped`'s rule to the pair's key and does not call `regrouped` on the `Pair` itself.
- Until S5, `native_form` raises `ValueError` for a `Rank` or `MultiSelect` (never reached: `native_gap` refuses both first).

## Design decisions (made here, argued)

- **`Lift`** is a frozen dataclass holding, per original question in order, the ids of the lowered questions that answer it (`((q.id, (lowered ids...)), ...)`). A pass-through and a joint each map to their own id; S5's pointwise lowerings map to `f"{q.id}#{i}"`. `native_lift` keeps the lowered result's `backend` and `rationale` (`""`, native).
- **`Joint.choice`** is a property, so `user.j2` renders the pairs (`q.choice.options`) without a second flattening rule in Jinja, and validation, schema, parse, `enum_values` and `native_gap` all read the same flattening.
- **`_check_choice(q, noun="choice")`**: the flattened bound's message says `joint`, the caller's word, rather than `choice`.
- **`head_first` via `regrouped`**: `regrouped(replace(answer, answer=pair.key), group=head-of-key, order=head order)` — exactly "`regrouped`'s rule applied to `answer.answer.key`", with one implementation of the tie rule.
- **`head_marginal`** sums reported mass per head in the joint's head order (heads with no reported pair are absent, never `0.0`: unreported is not zero).
- **Eval case** `decide-joint` (task `response-selector`): three actions, one of them tailless, and their legal targets; its counterexamples are the joint's analogues of the spec's list — an illegal pair (an unknown id), a head alone where the head takes a target (too short), a list (no single answer), and a null — each `unreadable`. One `native` (OpenAI) recording proves the lift through the production adapter.

## Review Focus

- A tailless head's key is the head id alone; `split_joint("withdraw") == Pair("withdraw", None)`.
- A head mapped to an empty tuple takes no tail (same as missing from `tails`).
- `split_joint` raises on `""`, `"=>x"` and `"a=>"`.
- Three 255-pair joints (765 enum values) fit one chunk; a fourth opens the next.
- A native joint with 255 pairs and `allow_none` is 256 options: `native_gap` names it.
- The capture (`llm.native_body`) records the lowered body — what was sent.

---

### Task 1: the pair spelling, `Joint`, validation, schema, parse

**Files:** `backend/src/grimoire/decisions.py`; `backend/tests/test_decisions.py`

- [ ] **Step 1: Failing tests**: `test_joint_key_and_split_joint_are_inverse`; `test_validate_joint_refuses_*` (separator, aliases, unknown tails key, head twice, too many / too few flattened, malformed shape); `test_joint_schema_is_a_flat_enum_of_pair_keys`; `test_parse_joint_*` (exact, normalised, tailless, unknown → `NOT_AN_OPTION`, null with/without `allow_none`, `split_joint(pair.key) == pair`); `test_chunks_hold_three_full_joints`; `test_outcome_and_render_spell_a_pair_as_its_key`; `test_head_marginal_and_head_first`.
- [ ] **Step 2–4:** implement; run.

### Task 2: `native_form` / `native_lift`, both adapters, `native_gap`

**Files:** `decisions.py`, `openrouter.py`, `openai_compatible.py`; tests `test_decisions.py`, `test_native_decisions.py`

- [ ] **Step 1: Failing tests**: `test_native_form_passes_old_items_through` (identity on `ITEM`-like items; `Lift`); `test_native_gap_counts_a_joints_pairs_with_none`; in `test_native_decisions.py`, per provider: `test_a_joint_is_sent_as_one_flattened_choice`, `test_a_native_joint_answer_is_a_pair_with_its_distribution_by_key` (and `head_first` regrouping with `NONE_KEY` excluded).
- [ ] **Step 2–4:** implement; switch both adapters; run `test_native_decisions.py`, `test_adapter_wire_golden.py` (unedited, green).

### Task 3: templates and the eval case

- [ ] `user.j2` joint branch over `q.choice.options`; `system.j2` joint bullet under `"joint" in kinds`; `verify_templates.py` bullet + a joint in `_DECIDE_NEW_ITEMS`; `test_inference_decide.py` line/bullet test; re-diff old renders.
- [ ] `decide-joint` case, recordings (compliant, `illegal-pair`, `head-only`, `list`, `null`, `native` via OpenAI), `VOCABULARY_READINGS`, README row.
- [ ] Run decide suites, `verify_templates.py`, ratchets; commit `01e-S4: Joint on both paths, and the native lowering framework`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review of the plan against §3.3, §6.2, §7, §8, §9 (C3b), §11
and slice S4, and the code at `a68f34d` (no Codex CLI, no subagent tool).
Findings folded:

- **The capture spelling**: `inference._outcome` writes a native answer
  through `decisions.outcome`, so a joint answer reaches the prompt log as
  whatever `_recorded` makes of a `Pair`. Without a branch it would be a
  dataclass `json.dumps` cannot write (the capture is guarded, so it would be
  silently lost). `_recorded` and `_rendered` both spell a `Pair` as its key —
  in Task 1's spellings test.
- **The spec's eval counterexample list** (a duplicate, an under-length one)
  does not apply to a single-answer joint; the plan names the joint's
  analogues explicitly rather than inventing a list-valued joint.
- **`native_gap` on the original item** (§3.3) — the joint's own flattened
  choice is what is counted, so a refusal names the caller's question id.
- Checked, no change: no lowered-id collision exists for a joint (its lowered
  choice keeps the joint's own id); that rule arrives with S5's predicates.

## Review and final gate (substitute review, 2026-10-10)

Adversarial self-review of the slice diff against §3.3, §6.2, §7, §8, §9 (C3b)
and §11 (no Codex CLI, no subagent tool). Checked, each pinned by a test: a
pre-01e item lowers to itself (`native_form(item)[0] == item`) and every
existing native test and the wire golden pass unedited; a joint's native
distribution is kept by the flattened key, the reserved none included as
`NONE_KEY`, and `head_marginal`/`head_first` leave it out of every head;
`native_gap` counts a joint's pairs plus the reserved none on the ORIGINAL
item and names the caller's id, refused before any request; the capture
(`llm.native_body`) records the lowered body; a structured joint answer
carries no distribution, so neither head reading says anything. Folded: the
offline readings test skips a native recording (its body is the adapter's to
read) and a dedicated test reads the joint's native recording through
`runner.native_output`, the production mapping. Left as designed: `Joint`'s
`tails` has no default, as §6.2 writes it.

Results: decide suites plus `test_llm_fakes.py` 644 passed, 2 skipped;
`verify_templates.py` 439 checks; ruff and mypy at baseline; the old-kind
renders still byte-identical (`cmp`).
