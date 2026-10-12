# 02-S8: Decision as a tool from play, dark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: substitute review (no Codex CLI in this environment); the Codex gates are owed.**

**Goal:** with `decide_play_tool=on`, a fresh NPC contribution, or a reroll
of one, may call `decide_choice` once, before any text its reader would see.
The model gets the selection, never the distribution. The decision is
recorded per variant. Delivers 02-C4 (full).

**Spec:** §8, §11 (02-C4), §13 ("Tool"), Slices (02-S8).

## Design

- **01g, extended (no behaviour change for existing callers).**
  - `stream_tools(visibility=)`: a factory of per-turn predicates fed each
    delta. `decline_after_text` reads the turn's `visible` (the predicate's
    answer) rather than any non-empty delta, so a hidden perception fence is
    not visible text (§8.2). Without a predicate, `visible` is `shown`.
  - `ResponseWatcher.next_turn()`: after a tool turn that showed nothing,
    the next turn's own leading fence stays hidden. Both fences' bodies make
    one preparation note.
- **Routing.**
  - `TOOLS_OPTIONAL = {"scene"}`.
  - `turn-tool-decision` joins the `turn_plan` route; its policy is
    `question="choice", samples=True`, never escalated (the decide tool
    takes no escalator).
- **The call site** (`routes/character_turns.py`).
  - `_offers_tool` requires the switch on, an NPC, and a scene primary not
    known to lack `tools`.
  - `_play_tool` resolves softly (a refusal is recorded `skipped`) and
    builds `decision_tool("turn-tool-decision", ...)` with
    `PLAY_TOOL_SHAPE`: `decide_choice`, no model context, at most 300
    characters, 2..6 options, the selection only, a cap answered as a
    result. Its item context is `turn_plan/tool_item.j2` over the observable
    window. It is captured through one `decision_capture` scope held open
    for the loop.
  - The tool's fn records the question and options, and bounds the whole
    decision by `plan_ceiling()` (§3.4); an overrun is `unanswered`.
  - `_tool_selection` draws the selection (§6.5's rule, the none excluded
    only where it was offered) and records the draw and backend.
  - `_opened` runs the loop's entry checks up front, with literal tasks
    (`chat` for a fresh contribution, `regenerate` for a reroll). A refusal
    falls back to a plain generation.
  - `PLAY_TOOL_BUDGET` is 2 turns, 1 call and 1 decision.
  - `_tool_events` yields `llm_reasoning.stream`-shaped events, resets the
    watcher between turns, and records `called` and `declined: after_text`.
    A failed loop raises its `LLMError`, as any stream failure does.
  - `_LoopMeter` stands in for the contribution's meter (the loop meters
    each turn under the run id) and takes the serving stamp from the last
    row.
  - Offered on a fresh contribution (`fresh`: no pending response, no
    appended block, no continuation) and on a reroll; never on a retry, a
    roll resume or Keep writing. The record goes to `save_variant(tool=)`
    through `_save`, `_pause`, `_rescue` and `_land_variant`.
- **Eval.** `decide-play-tool` covers compliant, undecodable, off-option,
  abstained and native. The play gate grades `gate.legal_tool`, reports the
  call and declined rates, and counts "on" as offered to every NPC
  contribution.

## Tests

`tests/test_play_tool.py`:
- off: no tools and a plain row;
- a call before text: the selection only goes back, only the final text is
  transcribed, the record, loop turns 1 and 2 under one run id, and the
  decision row's `response_id`;
- a call after visible text is declined;
- a call after a hidden perception fence is honoured, and both fences reach
  the reasoning;
- the narrator and a known-no model are never offered the tool;
- a reroll is offered it (the earlier variant's record is untouched) and a
  retry is not;
- the budget and shape;
- an invalid request is a result, not a failure.

`test_play_gate.py` covers the tool feature's offers.
