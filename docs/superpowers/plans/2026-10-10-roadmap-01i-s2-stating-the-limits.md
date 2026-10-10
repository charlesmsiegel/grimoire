# 01i-S2: Stating the limits — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user can state a model's context window and max output on its
provider (`facts.state`, `PUT /llm-connections/{id}/facts`), the facts body
returns the resolved `limits` with their sources, and the model facts panel
shows them (a **Size** section) and edits them, with a `?edit=limits` deep
link built by `providerPaths.modelLimitsPath`.

**Architecture:** `facts.state` gains `context_window` / `max_output`
(`None` leave, `0` remove, positive int below `2**31` set, else
`ValueError` before the file is touched); the output-above-window check runs
inside `change`, on the merged entry, under `_write_existing`'s hold.
`FactsUpdate` gains two `Any` fields; `put_connection_facts` passes them;
`_facts_body` adds `limits` from `limits.of` over the same cached row and
facts it reads, plus each value's catalog figure (`catalog`) so the panel can
show a disagreement. `limits.body` / `limits.limit_body` are the one JSON
spelling, reused by S3 and S4. Frontend: types, `providerPaths`, and the
panel.

**Tech Stack:** Python 3.11, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01i-context-window-fact-design.md` §6.1, §6.2, §7 (C3), §8, §9 (facts route tests, `ModelFactsPanel`, `providerPaths`).

## Global Constraints

- The write goes through `facts._write_existing` (atomic, facts lock + connection lock); no new file, no campaign lock.
- `FactsUpdate` fields are plain `Any`, dumped by `_dump`; the store decides, so `"8192"` is a 400 under either pydantic.
- Existing refusals unchanged: `not_migrated`, `newer_format`, 404, 503, 409 `facts_unreadable`. No `confirm_embedding` is asked for a limit; `registry.forget_model` is not called for one.
- The panel keeps the list/detail rule: read-only view by default, Edit reveals the form, Save/Cancel return to view; the `?edit=` param opens the form once per arrival and is cleared on leaving it.
- `modelLimitsPath` encodes the model segment by segment (the `models/*` splat).
- The hint text is the spec's, verbatim.

## Review Focus

- Two requests: a stated window of 8192, then `max_output: 16000` alone → 400, file byte-identical; the reverse order likewise.
- `0` on a value never stated is a no-op, not an error; an entry left empty is dropped (`_write_existing`).
- `limits` in the body follows the same rules as a resolved attempt (row cached for the current rev; stated outranks).
- The form sends a value only when it changed; clearing a stated value sends `0`; an empty box never stated sends nothing.
- `?edit=rates` still focuses the Input box; `?edit=limits` focuses the window box.

---

### Task 1: `facts.state` takes the limits

**Files:** Modify `backend/src/grimoire/store/inference/facts.py`; Test `backend/tests/test_inference_facts.py`.

**Interfaces:** `facts.state(..., context_window: object = None, max_output: object = None)`; `facts.check_limit(name: str, value: object) -> int | None` (None leave, 0 remove, positive int set; `ValueError` otherwise); `facts.LIMIT_ABOVE_WINDOW` message builder inside `change`.

- [ ] Failing tests: `test_a_stated_limit_is_set_removed_and_left`, `test_a_limit_that_is_not_a_positive_int_is_refused_before_the_file` (param `"8192", True, -1, 2**31, 1.5`), `test_a_max_output_above_the_window_is_refused_on_the_merged_entry` (both one request and the two-request orders, file bytes unchanged), `test_a_stated_limit_survives_a_rev_change`, `test_removing_an_unstated_limit_writes_nothing`.
- [ ] Implement; run `tests/test_inference_facts.py` → PASS.

### Task 2: the route and the body

**Files:** Modify `backend/src/grimoire/routes/models.py` (`FactsUpdate`), `backend/src/grimoire/routes/config.py` (`_facts_body`, `put_connection_facts` docstring + call), `backend/src/grimoire/store/inference/limits.py` (`limit_body`, `body`); Test `backend/tests/test_provider_api.py`.

**Interfaces:** `limits.limit_body(limit: wire.Limit) -> {"value", "source"}`; `limits.body(sizes: wire.Limits) -> {"window": …, "max_output": …}`. Facts body: `limits: {"window": {"value", "source", "catalog"}, "max_output": {…}}`, plus top-level stated `context_window` / `max_output` (already via `**known`).

- [ ] Failing tests: `test_facts_state_and_return_the_models_limits` (catalog row cached at the current rev; GET shows catalog sources; PUT a window → source `user`, `catalog` figure kept; PUT `0` → back to catalog), `test_a_limit_write_is_refused_as_every_facts_write_is` (`"8192"`, `true`, `-1`, `2**31`, output above window → 400; two-request refusal both orders, file unchanged), `test_a_limit_write_before_the_switch_is_409_not_migrated`, `test_a_limit_write_asks_no_confirm_embedding` (on the Embedding role's own model).
- [ ] Implement; run `tests/test_provider_api.py tests/test_inference_facts.py` → PASS.

### Task 3: frontend types, paths and the panel

**Files:** Modify `frontend/src/api/types.ts`, `frontend/src/providerPaths.ts`, `frontend/src/routes/ProvidersView.tsx`; Test `frontend/src/routes/ProvidersView.test.tsx`, `frontend/src/providerPaths.test.ts` (new if absent).

**Interfaces:** `type LimitSource = "user" | "catalog" | "unknown"`; `type ModelLimit = { value: number | null; source: LimitSource; catalog?: number | null }`; `ModelFacts.context_window: number | null; max_output: number | null; limits: { window: ModelLimit; max_output: ModelLimit }`; `ModelFactsUpdate.context_window?: number; max_output?: number`; `EDIT_LIMITS = "limits"`; `modelLimitsPath(id, model)`.

- [ ] Failing vitest cases: `modelLimitsPath encodes the model per segment`; panel: `shows Size with sources and the catalog's figure beside a stated one`, `Edit shows the two limit fields with the hint`, `clearing a stated window sends 0 and an untouched box sends nothing`, `?edit=limits opens the form once with the caret in the window box`.
- [ ] Implement: a **Size** `.side-section` (`Window 128,000 tokens (catalog)` / `Max output 16,000 tokens (you)` / `unknown`, the catalog figure as a `.field-hint` when it differs from a stated one); two number inputs (`Context window`, `Max output`) under a **Size** heading with the spec's hint; save diff rule; the `?edit=` effect generalised over `EDIT_RATES` / `EDIT_LIMITS` with a ref per focus target.
- [ ] `cd frontend && npx tsc --noEmit -p . && npx vitest run src/routes/ProvidersView.test.tsx src/providerPaths.test.ts`, eslint ratchet → PASS.

### Task 4: verify and commit

- [ ] Backend guards (`test_pydantic_guard`, `test_atomic_guard`, `test_paths_guard`, `test_import_guard`), full backend suite; ruff/mypy/eslint ratchets at baseline.
- [ ] Commit `01i-S2: state a model's window and max output, and show them`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review of the plan against spec 6.1/6.2/9 and the code
(no Codex CLI, no subagent tool).

- **Folded: the catalog's figure.** Spec 6.2 asks the view to show the
  catalog's figure beside a stated one, but 6.1's `limits` shape
  (`{value, source}`) cannot carry it once the user's word wins. The facts
  body's two entries therefore also carry `catalog` (the cached row's value,
  `null` when it states none). Additive: `value`/`source` are exactly 6.1's,
  and the settings view (S4) keeps the plain shape.
- **Folded: one JSON spelling.** `limits.limit_body` / `limits.body` are added
  here and reused by S3 (`model_window`) and S4, so the three surfaces cannot
  drift.
- **Checked: a write that lowers the window under a stated output.** It is
  refused like the reverse (the check is on the merged entry), and the
  sentence says to lower the output first or clear it.
- **Checked: `0` for a value never stated** pops nothing; with no other
  change `_write_existing` still runs (the entry may be created then dropped
  as empty), and the file is unchanged in content.
- **Checked: `forget_model`.** The route forgets a verdict only when
  `prefill` moved; a limit never moves it, so nothing changes there.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec 6.1, 6.2, 7 (C3)
and 9 stood in for `/codex:review` and the final spec gate (no Codex CLI, no
subagent tool). Every section 9 case for S2 maps to a test: the store write
(`test_inference_facts.py`: set/remove/leave, the bad values, both orders
of the two-request refusal with the file byte-identical, survival across a
rev change), the route (`test_provider_api.py`: GET's resolved `limits` with
sources, 400s for `"8192"`, `true`, `-1`, `2**31`, an output above the
window and both two-request orders, `not_migrated`, no `confirm_embedding`
on the Embedding role's own model), and vitest (`ProvidersView.test.tsx`:
Size with sources and the catalog's figure beside a stated one, `unknown`,
Edit's two fields with the hint, clearing sends `0` and an untouched box
sends nothing, `?edit=limits` once with the caret in the window box;
`providerPaths.test.ts`: per-segment encoding).

- **Folded (found in review):** the output-above-window check ran on every
  facts write, so a pair a hand left inconsistent in the file would have
  refused an unrelated `vision` change. It now runs only for a write that
  states a size (`_apply_sizes`), still on the merged entry inside the hold.
  New test: `test_a_hand_edited_size_pair_does_not_refuse_an_unrelated_write`.
- **Folded:** the form reads a stated size only when positive, so a value of
  `0` (never stored, but possible in a mock or a hand edit) is an empty box
  rather than one the whole-number check refuses.
- **Folded (ratchet):** the size merge moved out of `state` into
  `_apply_sizes`, keeping `state` under the complexity bound and its loop
  variable from shadowing `change`'s.
- **Deviation, additive:** the facts body's `limits` entries carry `catalog`
  beside 6.1's `value`/`source` (see the plan gate); nothing else reads it.

Results: full backend suite 16368 passed, 5 skipped (the root-only
`test_atomic.py` case deselected); the facts/route files re-run green after
the review fold (178 passed); full vitest 209 files / 4555 tests passed;
`tsc -b` clean; ruff, mypy and eslint ratchets all at baseline.
