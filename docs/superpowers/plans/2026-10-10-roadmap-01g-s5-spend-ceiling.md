# 01g-S5: The spend ceiling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: not run (speed mode); the Codex gates are owed.**

**Goal:** `RunBudget.spend_ceiling_usd` bounds a tool run's projected spend,
checked before every send (spec 3.9; 01g-C4 full). It is off unless a
caller sets it. With it set, a chain on which any attempt is unpriceable is
refused before anything is sent (`RunRefused("unpriceable")`), and
`inference.tool_run_refusal` is the same check as a preflight a `def` route
runs before reserving.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.9 ("How spend is estimated before a send"), §4 (C4), §6 (spend tests and
gate additions), Slices (01g-S5).

## Design

### Price (`inference.price_for(attempt) -> Price | None`)

This is the model-test preview's rule (`routes/config.py`'s
`post_connection_test_preview`), tried in this order:

1. **The attempt's cached catalog row** (`llm_connections.cached_row`):
   - its per-token `prompt` and `completion` prices, read with
     `probes._rate`;
   - a stated `0` is a free model, which is a reported price.
2. **The user's rates** (`pricing.rate_for_call`), but only when the
   attempt's provider preset does not `reports_price`.
3. Otherwise **unpriceable** (None).

`prices_for(resolved)` reads every attempt once, off the loop, keyed by
`(provider_id, model)`.

### `tool_run_refusal(task, resolved, budget, prices=None) -> RunRefused | None`

- It answers None when no ceiling is set.
- Otherwise it returns `RunRefused("unpriceable")` for the first attempt on
  `call_chain(resolved, max_tokens=budget.max_output_tokens)` that:
  - has no price; or
  - whose cap is not sent (`cap_sent`; spec 3.9 says "uncapped is
    unpriceable").
- Its message names the model, and says to set rates for it or to run
  without a ceiling.
- `run_tools` computes the prices in a worker thread and raises the refusal
  at entry. No meter opens and nothing is sent.

### The projection: a guard, never accounting

**Prompt tokens (P):**
- On the first send, P is `tokens.count_if_loaded` over the serialised
  outgoing messages plus the tool definitions. It is counted in a worker
  thread and multiplied by `tool_calls.PROJECTION_MARGIN` (1.5, a structural
  margin to be tuned against real runs).
- From then on, P is the last turn's reported `prompt_tokens` (else the
  P projected for it), plus the margin times a count of what has been
  appended since.

**Completion tokens (C):** the turn's output cap, held to each attempt's
maximum (`clamp_to_max_output`).

**Projection:** for each attempt that could serve the turn, price times
P and C; the turn's projection is the largest.

**Spent so far:** each sent turn's row is priced the same way from its
reported or estimated counts. A row that cannot be priced is replaced by its
turn's projection, and so is a failed turn that filed no row.

**Never read:** `cost_usd`, `estimated_usd` and `modelled_usd` are never
read, and nothing about the projection is written to the ledger.

### The check

- It runs before every send, the finalize turn included:
  `spent + projection > ceiling` stops the turn before its meter opens.
- On a tool turn that stop is the limit `spend`: the reserved finalize turn
  runs if it passes the same check, and otherwise the run stops
  `budget_exhausted / spend`.

## Decisions

- **The decide-tool spend check** is 01g-S7's. `Decision.usage` rows join
  "spent so far" there.
- **The ceiling may be 0**, which refuses every send. A ceiling that is
  negative or not finite is a `ValueError`.

## Files

- **Modify:** `tool_calls.py` (`RunBudget.spend_ceiling_usd`,
  `PROJECTION_MARGIN`) and `inference.py` (`Price`, `price_for`,
  `prices_for`, `tool_run_refusal`, and the loop's projection and check).
- **Tests:** `test_tool_spend.py`, which covers:
  - an attempt priced from its catalog, not refused;
  - a `"": 0` default not pricing a billed (`reports_price`) attempt;
  - uncapped being unpriceable;
  - an unpriced chain refused with nothing sent;
  - a priced chain stopping before the turn that would cross the ceiling;
  - a reported `cost_usd` never read;
  - the maximum taken over the chain;
  - the preflight answering before anything is reserved.
