# 02-S2: Turn intent, dark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** one closed question, asked before a contribution is composed,
about how the assigned actor answers (their stance), with the answer used as
given (the draw is 02-S3). Off by default and shown in no UI. Delivers 02-C2b
(part).

**Spec:** §3.1–3.5, §6.1–6.5, §12, Slices (02-S2).

## Design

- **Routing.** `routing.NO_LEGACY` (the checklist's shared structure) marks
  a route born at format 2: `legacy_key` answers "", `_legacy_routes` skips
  it, and `legacy_plan._pins_and_presets` reads nothing for it.
  `routing.NO_LEGACY_TASKS` lists its tasks; every frozen inference baseline
  enumerates `TASK_ROUTE` less them. The `turn_plan` route (Decision role,
  `decide`) carries `turn-intent`, landed with its call site.
- **`store/turn_plan.py`.** `INTENT_BRIEF_TOKENS = 600`; `brief_of(card)`
  (description then personality, clipped by token count); `intent_item(name,
  conversation, brief)`: context `turn_plan/item.j2`, one `Choice`
  `stance` over `STANCES`, null allowed; `stance_of(result)`: a known stance,
  or None with the answer's reason.
- **The call site** (`routes/character_turns.py`).
  - `_plan_mode` answers "" for off, the narrator, a pending response, a roll
    continuation or a typed note, and swallows an unreadable switch.
  - `_round_frames` mints the response id and calls `_turn_plan` before
    `_prepare`.
  - `_turn_plan` resolves through `_soft_resolved` and gathers the item in
    the threadpool (`_intent_item`: the actor's own card through the locked
    version, the observable window through the prompt-phase regex view).
  - It decides inside a `decision_capture` scope with `plan_deadline()` and
    `response_id`. It never raises an `Exception`: `skipped` is the refusal
    kind, the `LLMError` kind (a spent deadline is `timeout`),
    `invalid_request`, or `error:<Class>` logged at ERROR by class only.
  - `_prepare` stores the record as `intent` on the new response and composes
    with it. Its roll-resume branch recomposes with the pending response's
    own `intent`, so the resume snapshot and Keep writing carry the block.
- **Eval.** `decide-turn-intent`: compliant, undecodable, off-option,
  abstained and native (OpenAI, with a distribution) recordings; the grader
  checks the question, options, context, the actor's own brief, no other
  actor's material, the answer and the distribution.
- **Templates.** `turn_plan/item.j2` and `turn_plan/stance_question.j2` are
  added. `verify_templates.py` holds the item, the options and the block.
  The templates README gains a `turn_plan/` section.

## Tests

`tests/test_turn_intent.py`:
- off: one call, no key, no row;
- on: one schema'd call before `response_start`, the block right after the
  last post, the record, the ledger row's `response_id`, `post` and
  `round_id`, the snapshot and the capture;
- abstained and off-option: no block;
- the narrator and a typed director note never ask;
- `missing_key` skips without calling `decide`; an `LLMError` skips with its
  kind;
- a spent deadline is `timeout`, unsent, and nothing is noted against the
  connection;
- a `RuntimeError` skips as `error:RuntimeError`, the reply is complete and
  no round is left open;
- a reroll and a retry reuse the snapshot with no call;
- a roll resume and Keep writing carry the block with no second call;
- `_plan_mode`'s round rules, a replay's round included;
- a format-1 store resolves `turn-intent` through its role.

Guards updated: `test_routing.py`, `test_task_policy.py` (the stance
vocabulary), `test_llm_fakes.py` (`NOT_IN_CASSETTE`), `test_evals.py`, and
the frozen baselines' task lists.
