# 02-S1: Record and plumbing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** the plumbing every 02 play decision stands on, with nothing asking
a decision yet. Delivers 02-C1 (full) and 02-C6 (part: 3.1's off rule, 3.2,
3.3's catch-all helper home, 3.4's deadline, 3.5's attribution, 3.6's readers
with no UI).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-02-decision-integration-design.md`
§1, §3, Slices (02-S1).

## Design

- **02-C1.** §1.5 records what changed since the spec's baseline. 01g-S3
  already gave `decide`, `run_stages` and `_Call` `response_id=`, so this
  slice reuses it.
- **`store/responses.py`.** `mint_id()` hands out an id with no write.
  `prepare(..., *, response_id="", intent=None)` takes it (refusing an id
  already in the scope) and stores `intent` only when given. Readers use
  `.get("intent")`.
- **`store/play_decisions.py`.** `speaker_sampling(cid)`,
  `turn_plan_mode(cid)` and `play_tool(cid)`. A campaign's own value in its
  vocabulary wins; otherwise the global's, when it is in the vocabulary;
  otherwise `off`. Read-only, so `store/locks.py` is unchanged. Imported by
  the store facade so `dir(store)` does not depend on import order (the
  baseline gains `play_decisions` and `turn_plan`).
- **Config.** `decide_speaker_sampling`, `decide_turn_plan` and
  `decide_play_tool` join `_CONFIG_KEYS`, the defaults (`off`) and
  `ConfigUpdate`. `PUT /config` refuses a value outside each vocabulary.
  `GET /config` does not report them: no UI shows one before 02-S6.
- **`routes/common.py`.** `PLAN_CEILING_S = 20.0`, `plan_ceiling()` (lower of
  it and a positive `llm_call_budget`), `plan_deadline()` (one monotonic
  deadline per decision; a call is bounded by what is left through
  `_bounded_call`, a call with nothing left is closed unsent and raises
  `DeadlineRefused`, whose `llm_call_failed` is False). No `_noting`.
  `_plan_clock` is the test seam.
- **The block.** `store/turn_plan.py` holds the vocabularies
  (stance, disclosure, tension) and `block(plan, name)`, which renders
  `scene/turn_intent.j2` from the answers that were read, or "".
  `compose_turn` and `compose_director_turn` gain `plan=`; the block is sent
  as one system message right after the packed history (before a director
  note), reserved and reported as a "Turn intent" row after every appended
  row. None, a skipped record, or a narrator's call adds nothing.

## Tests

`tests/test_play_decisions.py`: defaults off; global and campaign precedence
and the mistake rule; `PUT /config` round trip and refusals; the deadline's
in-time call, unsent refusal (closed coroutine, `_refused_unsent`) and
overrun; the ceiling never above the call budget; `mint_id` + `prepare`;
the block is byte-identical when absent, follows the last post, precedes a
director note, is absent for the narrator, survives a layout that drops
every section, and renders only known values. `test_lore_golden.py`,
`test_authors_note_turns.py` and `test_context.py` are untouched and pass.
