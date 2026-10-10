# 01g-S7: The decide tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: not run (speed mode); the Codex gates are owed.**

**Goal:** a generating model in a tool loop can ask the Decision role one
bounded closed question as a tool. That question is capped per run,
checked against the spend ceiling, metered under the caller's task with
the run's ids, and never recursive. Delivers 01g-C5 (full).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.12, §3.9 (the decide spend check), §4 (C5), §6 ("Decision-as-tool" and
the decide-tool gate additions), Slices (01g-S7).

## Design

### `routes/tool_decision.decision_tool(task, resolved, client, *, ...)`

Returns `(ToolSpec, "")`, or `(None, why)` in these cases:
- no resolution (the caller's `why` is passed through);
- a resolution of another task;
- under a `SpendGuard`, a stage that is native, or one that is unpriceable
  or uncapped (`inference.decide_projection` is None).

What a consumer can shape, through `ToolShape`:
- the tool's `name`;
- whether the model supplies a `context`;
- `max_question_chars` (at most 1000);
- an options range inside `2..MAX_TOOL_OPTIONS` (16);
- `result`, `full` or `selection`;
- `on_cap`, `error` or `result`.

`context` is fixed or built from the arguments, and is held to
`MAX_DECIDE_CONTEXT` with the model's part truncated first. `select` maps
the `ItemResult` to what the model is sent.

### The handler, in order

1. `ctx.run.take_decision()`, the run's `max_decisions`. Past the cap the
   call gets an error result, or `{"selected": null, "reason": "cap"}`.
2. The one `Choice` item is built and passed through `decisions.validate`.
   A `DecideRequestError` becomes an error result, or
   `reason: invalid_request`.
3. Under a ceiling, the projection is checked against `ctx.run.room_usd()`.
4. `operations.decide(resolved.task, [item], ...)` is called:
   - with the run's `run_id` and turn, and the caller's `round_id` and
     `response_id`;
   - with the caller's `capture` passed straight through (a `# capture-ok:`
     marker on that line);
   - capped with `max_tokens=`;
   - with the run's remaining wall clock as `around`.
5. `Decision.usage` is charged to the run's "spent so far"
   (`inference.rows_cost`, falling back to the projection).

The result is only what the backend reported:
- `full` is `{answer, status, probability?, distribution?}`;
- `selection` is `{selected}` or `{selected: null, reason}`;
- a failed call gets "the decision could not be made: <kind>" or
  `reason: unanswered`.

### `inference`

- `decide(max_tokens=)` caps each structured stage's targets
  (`_capped_stage`).
- New helpers: `decide_projection` (the decide prompt counted with the
  margin plus the cap, summed over every stage attempt and its prompt-only
  re-send; None for a native or unpriceable stage) and `rows_cost`.
- The loop hands every tool a `tool_calls.RunView` (`ToolContext.run`,
  together with `ToolContext.turn`): a decision cap, the room left, and a
  charge. A run whose decisions reach `max_decisions` stops on `decisions`,
  with the finalize turn when one fits.

### Guards

- **The operation guard:**
  - a `decision_tool(...)` call is a decide call site, and needs a literal
    task on a decide route and a resolution;
  - "every decide task is decided" counts it;
  - the shim's one inner `decide(<resolution>.task, ...)` is accepted, in
    that module only.
- **The capture guard:** the inner call carries a `# capture-ok:` marker
  (the cap is 2; it is the first).

## Decisions and drift

- **No route.** No `tool_decision` route and no `routing.NO_LEGACY` are
  added: the route lands with its first consumer (spec, "Routes").
- **Trace kind.** A decide call is traced as a `tool` entry named after the
  tool, not as `kind="decide"`: the loop does not know which tool is the
  decide tool.
- **Spend under a selection shape.** A decide call refused for spend under
  `result="selection"` reads `reason: "cap"`. The spec's list of reasons
  names no spend reason.

## Tests

`test_tool_decision.py` covers:
- the offer refusals;
- the parameters following the shape;
- a structured answer with no probability, and a native answer with its
  probability;
- the selection shape;
- refused requests;
- `on_cap`;
- a failed call;
- capture pass-through;
- the output cap;
- a decision refused before crossing the ceiling, and its rows charged;
- in a loop: each decision counted, the cap stopping the run on
  `decisions`, and the rows carrying the run id.

The operation guard's planted cases cover `decision_tool` and the shim's
inner `decide`.
