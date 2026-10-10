# 01i-S3: `model_window` in context breakdowns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every context breakdown that names a `model` also names
`model_window` (`{value, source}`), taken from the target that `model` was
read from, and the inspector's bar prefers it over its own catalog lookup.

**Architecture:** The live context read (`GET .../context`) and the prompt
diff's live side add `model_window` from the resolution's primary target.
`routes.common._record_prompt` adds it from the target it is handed (a
chain's primary, or one attempt), so every capture -- chat, director, retry,
regenerate, replay, continuation, opener and a fallback variant -- carries
the window of the attempt it names; `character_turns._capture` adds it from
`sent` as well, so a capture handed no `conn` (a native decision, an
unvouched variant) still names it. `prompt_log.record` spreads the breakdown
into the payload, so nothing changes there. `ContextBreakdown.contextLimit`
prefers `ctx.model_window` when the field exists and falls back to the
catalog lookup only when it is absent (an older snapshot). The stale "the
backend cannot infer the window" sentences in `pack.py` and `store/config.py`
are corrected. Packing is unchanged.

**Tech Stack:** Python 3.11, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01i-context-window-fact-design.md` §6.4, §7 (C3), §9 (breakdown test, byte-identity, `ContextBreakdown`).

## Global Constraints

- No prompt byte changes: `model_window` rides the breakdown and the capture payload, never a message; `test_lore_golden.py` and the frozen-campaign `snapshot.json` must not move.
- One JSON spelling: `limits.limit_body`.
- `_record_prompt` stays never-raising.
- `prompt_log._well_formed` lets unknown extras pass, so an older reader of a new payload is unaffected.

## Review Focus

- A fallback variant's capture names the FALLBACK's window, not the primary's.
- The live read with nothing resolved names `model_window` unknown, not absent (a new breakdown always has the field, so the frontend never falls back to the catalog for one).
- `contextLimit`: `model_window` present with `value: null` means unknown -- no catalog fallback; absent means an older snapshot -- fallback.

---

### Task 1: backend

**Files:** Modify `backend/src/grimoire/routes/scenes.py` (`get_scene_context`, the diff's live side), `backend/src/grimoire/routes/common.py` (`_record_prompt`), `backend/src/grimoire/routes/character_turns.py` (`_capture`), `backend/src/grimoire/store/context/pack.py` (docstring), `backend/src/grimoire/store/config.py` (comment); Test `backend/tests/test_inference_limits.py` or the existing context/prompt-log route tests.

**Interfaces:** `routes.common.model_window(target: wire.Target | None) -> dict` (`limits.limit_body` of its window, unknown for None).

- [ ] Failing tests: `test_the_live_context_names_the_models_window` (catalog row for the chat model → `{"value": N, "source": "catalog"}`; nothing listed → unknown), `test_a_chat_turns_capture_names_the_window_of_the_target_sent` (prompt log on, a chat turn through `llm_fakes`, the snapshot's `model_window`), `test_a_decision_capture_names_its_window` (`character_turns._capture` with a lone target and `vouched=False`), `test_a_fallback_variants_capture_names_the_fallbacks_window` (`_record_prompt` with a chain whose fallback has another window, then `on_variant`).
- [ ] Implement; correct the two sentences.
- [ ] Run the touched route tests, `test_prompt_log*.py`, `test_lore_golden.py`, frozen-campaign tests → PASS.

### Task 2: frontend

**Files:** Modify `frontend/src/api/types.ts` (`SceneContext.model_window?: ModelLimit`), `frontend/src/components/ContextBreakdown.tsx` (`contextLimit`); Test `frontend/src/components/ContextBreakdown.test.tsx`.

- [ ] Failing vitest: `the breakdown's own model window is preferred over the catalog`, `an older snapshot falls back to the catalog lookup`, `a window the server says is unknown is not looked up`, `the smaller of budget and window still bounds the bar`.
- [ ] Implement; `tsc -b`; vitest on the file; eslint ratchet.

### Task 3: verify and commit

- [ ] Full backend suite; ratchets.
- [ ] Commit `01i-S3: name the model's window in every context breakdown`.

## Plan gate (substitute review, 2026-10-10)

Adversarial self-review against spec 6.4 and the code (no Codex CLI, no
subagent tool).

- **Folded: which callers.** The spec names three sites; the code has eight
  `_record_prompt` callers, every one passing `conn`. Adding the field inside
  `_record_prompt` covers them all (and the fallback variant, which it
  re-enters with the fallback's target), rather than editing each caller.
  `_capture` adds it too because two of its paths pass `conn=None`.
- **Folded: the prompt diff's live side** also names a model, so it carries
  `model_window` as the live read does.
- **Folded: present-but-unknown versus absent.** The spec says the panel
  falls back to the catalog only for an older snapshot; a new breakdown with
  `value: null` is the server saying "unknown", so the panel draws no window
  rather than second-guessing the server with the same cache it read.
- **Checked:** `prompt_log`'s index row (`_META_TYPES`) is unchanged; the
  field lives only in the payload, which accepts extras.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff against spec 6.4 and 9 stood in
for `/codex:review` and the final spec gate (no Codex CLI, no subagent tool).
Section 9's breakdown case is `test_a_chat_turns_capture_names_the_window_of_
the_target_sent` (with the live read, the fallback variant and the diff's
live side beside it in `test_sampler_presets_routes.py`), plus
`test_a_native_picks_capture_names_its_models_window` for a capture handed no
`conn`; the `ContextBreakdown` vitest cases cover the preference, the older
snapshot's fallback, a server-unknown window and the budget/window minimum.
Byte-identity: `test_lore_golden.py` and the frozen-campaign sweep pass with
`snapshot.json` untouched -- `model_window` rides the breakdown and the
capture payload, never a message.

- **Found by the suite, folded:** the prompt diff's sides are pinned field by
  field (`test_prompt_log_routes.py::test_both_sides_report_the_budget_they_
  were_packed_to`), and the slice added `model_window` to them; the pin now
  names it and its shape, and `PromptDiffSide` in `api/types.ts` carries it.
- **Checked:** the fallback variant's capture re-enters `_record_prompt`
  with the fallback's own target, so it names the fallback's window (test).
- **Checked:** `prompt_log`'s index row is unchanged; the payload accepts
  extras, so a build before this one reads a new capture unharmed.
- **Not a finding:** `test_parallel_harness.py::test_a_failure_on_one_
  worker_fails_the_run` timed out once (its child run has a 180 s budget)
  while the machine was loaded by other worktrees; it passes on a re-run and
  touches nothing in this slice.

Results: full backend suite 16372 passed, 5 skipped, with the two cases
above (one folded, one load-only) re-run green; `ContextBreakdown.test.tsx`
13 passed; `tsc -b` clean; ruff, mypy and eslint ratchets all at baseline.
