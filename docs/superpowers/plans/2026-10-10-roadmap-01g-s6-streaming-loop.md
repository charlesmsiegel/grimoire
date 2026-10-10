# 01g-S6: Streaming the loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: not run (speed mode); the Codex gates are owed.**

**Goal:** `inference.stream_tools` yields the loop's events as they happen.
Every turn goes through `client.stream`, the `generate(stream=True)` path.
`run_tools` becomes `stream_tools` drained, so the loop has one
implementation. Delivers 01g-C6 (full).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.8 and §3.9 ("The per-turn wall bound", the `stream_tools` case), §4
(C6), §6 (the 01g-C6 tests and the streaming gate additions), Slices
(01g-S6).

## Design

**The driver task.** `_Loop.events` runs the steps in a task of its own,
`_drive`, which emits every event onto a queue.
- The consumer yields each event as it arrives.
- After `llm.HEARTBEAT_INTERVAL` of silence it yields an empty `text`
  instead. That is how heartbeats arrive between turns and while a tool
  runs, the decide tool included.
- An exception that ends the run is handed across the queue and raised in
  the consumer.
- Closing or cancelling the consumer cancels the driver, and waits for it to
  unwind for at most `_DRIVER_UNWIND_S`, so the turn's open meter files
  `aborted`.

**Streamed turns.**
- `client.stream` is used for every turn, and through
  `_streamed_structured` with its re-send for a finalize turn that carries
  a schema.
- Each delta, `""` included, becomes a `text` event, so
  `tool_calls.text_deltas(events)` yields exactly what
  `generate(stream=True)` would.
- The joined path keeps `client.complete` and emits each turn's text whole.

**The wall on a streamed turn** (spec 3.9). The per-turn bound is checked at
each delta *until* the first visible one. A turn that has shown text is
never cut; the bound only refuses the next send. The check's granularity is
the provider's lines, or the facade's own heartbeat.

**Reasoning.** The caller's `llm_reasoning.Buffer` (`reasoning=`) is placed
in each turn's holder. `llm._stamp` resets it at each turn's first attempt,
so no continuity across turns is promised.

**`decline_after_text`.** A turn that showed text and ended in calls ends
the run `completed` with that text:
- its calls are never executed and no turn follows;
- they are listed in `LoopResult.declined`;
- the trace records each as `declined: after_text`.

A call that comes before any visible text runs normally.

**Entry refusals.** `stream_tools` is a plain function: its entry checks
raise before an iterator is returned. The spend preflight runs on the first
step, because pricing reads the store off the loop.

## Files

**Modify:**
- `inference.py`: the driver, the streamed turn, and `stream_tools`, with
  `run_tools` over it;
- `tool_calls.py`: `text_deltas`;
- `tests/llm_fakes.py`: `FakeToolTurns.stream`, and `ToolTurn`'s `deltas`,
  `pause` and `thinking`.

**Tests in `test_tool_stream.py`:**
- the event order and the `interstitial` flag;
- a call declined after visible text with no further turn, and a call
  before text that runs;
- `text_deltas` equal to `generate(stream=True)`'s deltas and heartbeats;
- heartbeats during a slow tool;
- the reasoning buffer on every turn;
- the wall never cutting shown text, and bounding a turn before it;
- the entry checks raised eagerly, and a closed stream stopping the run.
