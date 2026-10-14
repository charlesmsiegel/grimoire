# Roadmap 12-S2: the loop driver, budgets and trace

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** One bounded investigation from a seed to a checked `Outcome`, with
per-entry-point budgets and a stored-shape trace.
**Architecture:** `routes/investigation.investigate` runs 01g's `run_tools`
(literal task per call site) with `tool_choice="required"`; the store holds
the budgets (`store/investigation/budget.py`), the trace
(`store/investigation/trace.py`) and the final-call checks
(`store/investigation/final.py`).
**Tech Stack:** Python 3.11+, 01g tool loop, 01i `prompt_ceiling`.
**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-12-bounded-agentic-investigation-design.md`,
12-S2, sections 5, 7, 8.1, 14 "Loop and budgets".

## Decisions taken against the spec

- **The context stop lives in the loop.** 01g's `run_tools` exposes no
  per-turn hook, so `tool_calls.RunBudget` gains `max_prompt_tokens` and the
  loop refuses a send whose counted prompt (messages and tool definitions,
  `tokens.count_if_loaded`) passes it, stopping `budget_exhausted` on
  `context` (added to `tool_calls.LIMITS`). `investigate` sets it from
  `prompt_ceiling(resolved, max_tokens=budget.max_output_tokens)`, else
  `FALLBACK_PROMPT_TOKENS = 8192`.
- **No reserved finalize turn** (`reserve_final=False`): every entry point ends
  on a terminal tool and a finalize turn's prose is dropped, so reserving one
  would only cost the model a turn it could finish in.
- **The pre-reservation priceable check** is 01g's existing
  `inference.tool_run_refusal(task, resolved, budget)` (spec 15.3).
- **Executor:** `tool_calls.registered(toolset)` -- sync tools on its bounded
  pool (never the event loop, never queued past capacity), async ones
  (`search_history`) awaited. Each tool notes its own trace step on the
  run's state, so the trace needs nothing from the executor.
- **Money** is the run's ledger rows folded by a new public
  `usage.fold_rows` (the Costs page's `_add`), each column `None` where no
  call landed in it.
- **Fake:** `llm_fakes.FakeToolTurns` already scripts tool turns by call order;
  no new fake class was needed.

## Tasks

- [x] `RunBudget.max_prompt_tokens` and the loop's `context` stop.
- [x] `budget.py`: `for_entry(entry)`, `wall_seconds`, `spend_ceiling`, `prompt_tokens`, `with_context`; the 7.1 table verbatim.
- [x] `trace.py`: `step`, `usage_of`, `build`, `valid` (shape owned by `continuity.candidates.investigation_trace`).
- [x] `final.py`: `settle(run, call, candidate=)`.
- [x] `investigate` and `Outcome`; `test_investigation_loop.py` (each 01g limit by name, wall, context with prompt_ceiling and fallback, prose-only final turn, cancel, failed by kind only, unpriced refused having sent nothing, spend).
