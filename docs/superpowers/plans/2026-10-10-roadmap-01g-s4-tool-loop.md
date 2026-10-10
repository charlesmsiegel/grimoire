# 01g-S4: The loop primitive (joined) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: not run (speed mode); the Codex gates are owed.**

**Goal:** `inference.run_tools(task, messages, *, toolset, execute, client,
resolved, budget, run_id, ...)` runs a bounded loop of model turns and
caller-executed tool calls and returns a `tool_calls.LoopResult`. Each turn
is metered under the caller's run id, and every limit is checked before the
send or execution it bounds. Delivers 01g-C2a (full), 01g-C3 (full) and
01g-C4 (part: everything except spend, which is 01g-S5). No route calls the
loop. The guards that police it are proven on planted sources, because spec
"Routes" says a tools route lands with its first consumer.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.1, §3.6-3.11, §4 (C2a, C3, C4), §5, §6 ("The loop" and the gate
additions for wall, limits, routes, fallback and execution), Slices
(01g-S4).

**Builds on:**
- 01g-S1 (capability, `tool_calls` leaf).
- 01g-S2 (shapes, `Collector.calls()` / `opaque()`, lowering,
  `ToolsRefusalError`).
- 01g-S3, from its plan:
  - `usage.meter(run_id=, loop_turn=, tool_calls=)`, with `m.tool_calls`
    settable inside the `with`;
  - `RUN_KEY`, kept by `_stamp`;
  - `usage.sent()` / `PRE_SEND_KEYS`;
  - `inference._meter`;
  - `decide(run_id=, loop_turn=, response_id=)`.
- 01f-S2/S3: `call_chain(schema=, max_tokens=)`, `_without_refused_mode`,
  `cap_sent`.
- 01d-S1..S3, as integrated. The loop honours `chain.fallback is None` and
  adds no fallback policy.

## Design

### Data (`tool_calls.py`, stdlib + `schemas`)

New shapes:
- `RunBudget`, as spec 3.9 lists it but without `spend_ceiling_usd`, which
  01g-S5 adds.
- `LoopResult` and `TraceEntry`.
- `RunRefused(Exception)` with a `kind`. S4 never raises it, but S5 does,
  and the type is shared.
- `MIN_TURN_SECONDS = 5`, `TOOL_WORKERS = 4`.

New helpers:
- `truncate(text, limit)`, which appends `"\n[truncated: N more characters]"`.
- `loop_id(run_id, n)`, giving `gc_<run_id[:8]>_<n>`.
- `rewrite_ids(messages, mapping)`, which rewrites call and result ids
  consistently.
- `registered(toolset) -> Execute`, the default executor:
  - a sync tool runs on the toolset's own bounded `ThreadPoolExecutor`
    (`TOOL_WORKERS`), and an async tool is awaited;
  - a wait is abandoned after `min(spec.timeout, deadline - now)` (the error
    result is "timed out"; the thread finishes on its own);
  - once every worker is held by an abandoned thread, a call gets
    "tool capacity exhausted" and is not started.

**Already integrated, not S4's:** `tool_calls.KEY` is in
`store/usage.PRE_SEND_KEYS` as `TOOL_CALLS_KEY` (01g-S1, pinned by
`test_usage_estimate.py` and `test_incoming_capture.py`), and `Meter.row`
exists (01g-S3). The loop reads each turn's row from `m.row`.

### `grimoire/deadline.py` (new gateway leaf)

`bounded(awaitable, seconds, *, on_timeout=None, error=...)` holds
`_bounded_call`'s abandon semantics: `asyncio.wait`, no `wait_for`, and a
detached task that unwinds on its own. `routes.common._bounded_call`
becomes a thin wrapper over it, with no behaviour change. The loop uses it
for each turn's facade call.

### `inference.run_tools` (the loop; spec 3.7)

**Entry refusals (`ValueError`; no meter opens):**
- `_generating(task, resolved)`;
- `run_id` empty;
- an empty toolset;
- `final_schema` that fails `schemas.check`;
- a prompt that ends in an assistant turn, or a tailed `PreparedMessages`;
- a `PreparedMessages` with no frozen profiles (asked through
  `snapshot()`);
- a route that neither requires `tools` nor is in
  `routing.TOOLS_OPTIONAL` (new, empty);
- a primary that `resolve.known_lacks(resolved, "tools")` says cannot call
  tools (a new public wrapper over `_needs` / `_missing`);
- the current task already running a loop (a module `WeakSet` of
  `asyncio.Task`s).

**Once, off the loop:**
- pin the store root (`store.home()`);
- render nothing yet.

**Each turn k:**
1. The budget check. Its order is: cancelled, then turns (with
   `reserve_final`), then wall (`elapsed + MIN_TURN_SECONDS`;
   `wall_seconds <= 0` means no wall; `None` means
   `config.llm_call_budget()`). If it refuses, finalize when one is reserved
   and fits; otherwise stop with `budget_exhausted` and `limit`.
2. Before the send, re-check the root pin in a worker thread
   (`failed / store_moved`).
3. Open the meter: `store.usage.meter(task, campaign=, scene=, post=,
   round_id=, response_id=, run_id=, loop_turn=k)`, through a `_turn_meter`
   beside S3's `_meter`. Install a fresh `Collector` in it, and the caller's
   `reasoning` buffer when given.
4. Send `client.complete(messages, chain, m.usage, tools=defs,
   tool_choice=choice)` under `deadline.bounded`, with
   `min(llm_call_budget, wall - elapsed)` and either term absent when it is
   `<= 0`.
   - When the run's wall is what expires, raise with `NOT_A_FAILURE`, so the
     row is `aborted`, and stop with `budget_exhausted / wall`.
   - An `llm_call_budget` overrun is `LLMError("timeout")`, noted against
     `llm.ATTEMPTED` as `_noting` does.
   - `chain` is `call_chain(resolved, max_tokens=budget.max_output_tokens)`,
     or the sticky fallback chain.
   - Set `m.tool_calls = len(calls)` before the meter exits.
5. Settle.
   - The loop appends the meter's row (`m.row`) to `rows`.
   - `capture(messages, outcome, ATTEMPTED)` is called guarded and outside
     the meter.
   - The trace gets a `model` entry.
6. Read the calls:
   - **No calls:** this is the answer. With `final_schema`, the text is
     read with `schemas.find_value` and checked with `conforms`; a reply
     that does not conform gets one finalize turn.
   - **A terminal tool:** its arguments are validated. If valid, return
     `final_call`, unexecuted, and mark later calls in the same turn
     `not_run`. If not valid, send an error result.
   - **`finish_reason == "length"` with calls:** every call gets the
     "arguments were cut off" error result.
7. Execute each call in order. Before each one, check cancelled, then
   `max_tool_calls`, then the wall. A call past a limit gets
   `"not run: the run's tool budget is spent"`, and the run then finalizes
   or stops with that `limit`.
   - These get error results and count against the budget: an unknown
     name, unparseable arguments, arguments that do not conform, a tool
     exception (logged with the type only; the model sees "the tool
     failed"), and a `ToolError` (the model sees its message).
   - Results are truncated per tool, then held to `max_result_chars_total`.
     Crossing that total stops the run after the turn with `result_chars`.
   - Proposals are collected.
8. Append the assistant turn (text, `tool_calls` with the loop's ids,
   `_opaque` from `Collector.opaque()`) and one `tool` message per call.
   - A `PreparedMessages` is extended through
     `with_appended_keeping(...)`, a new method that keeps `on_variant`
     and `settings`.
   - A plain list is extended as a list.
9. **Finalize** (spec 3.7 step 7):
   - render `templates/tools/finalize.j2` with the schema, in a worker
     thread;
   - send with `tool_choice="none"` and the definitions;
   - with `final_schema`, send through the 01f path (`schema=` on
     `call_chain`, then a `_without_refused_mode` re-send on the same
     holder);
   - an unreadable reply is `failed / final_unreadable`.

**Fallback (spec 3.11):**
- **Sticky switch.** After each turn, compare `ATTEMPTED`'s
  `(provider_id, model)` with the primary's, ignoring a degrade sibling.
  Once the fallback has served, each later turn sends
  `wire.Chain(that_target capped)`, and the trace notes "fell back".
- **Id rewrite at the switch.** History ids are rewritten to
  `loop_id(run_id, n)`. The originals stay in `ToolCall.provider_id`.
- **Calls with no id.** A call that arrives without one gets a loop id at
  once.
- **Opaque state.** `_opaque` is filtered by the S2 adapters' provenance
  rule, so it never crosses.
- **Thinking off.** A target that inherits tool turns written by another
  `(provider_id, model)` is sent with its reasoning control `off`. This is
  a new `wire.Target.with_thinking_off()`, built the way `with_output_cap`
  is.
- **The `required` downgrade.** `required` is downgraded to `auto` wherever
  `probes.tool_choice` would ask for `auto`. That covers implicit adaptive
  thinking (S1's "Open for the spec"). The catalog features are read once
  per run, off the loop.

**Errors:**
- An `LLMError` on turn 1 is raised; nothing was done.
- A later one gives `failed`, with the earlier rows intact.
- `CancelledError` propagates: the meter files `aborted`, and no `done`.
- The run-end log line carries ids and counts only.

**Shape for S6.** The body is a private async generator `_loop_events(...)`
yielding the spec's `LoopEvent`s; `run_tools` drains it and returns
`done`'s payload. S6 makes it public as `stream_tools` and switches the
per-turn call to `client.stream`.

### Guards

- **`test_routing_guard.py`.** Its recogniser gains `run_tools` and
  `stream_tools`: a literal task on a routed generate task.
- **`test_operation_guard.py`.** Two new rules:
  - a loop call names a literal task on a generate route that requires
    `tools` or is in `TOOLS_OPTIONAL`, and passes `resolved=`;
  - every route that requires `tools` is used by such a call. This is
    vacuous today and proven on a planted source.
- **`test_usage_guard.py`.** The loop's `client.complete(..., m.usage, ...)`
  is inside a meter in `inference.py`, which it already scans.
- **`test_tool_guard.py`** (new):
  - it resolves every `ToolSpec(fn=...)` and every `execute=` function;
  - it fails such a function when its module imports `store.atomic`, or
    when it calls `campaign_lock`, `hold_all`, `runs.run_*` or
    `start_detached`;
  - the marker is `# tool-ok: <reason>`, capped;
  - it is proven on a planted writer and a planted reader;
  - its row goes in `CONTRIBUTING.md`'s guard table (`test_docs_guard.py`).

### Fakes and templates

- `llm_fakes.FakeToolTurns(*turns)`: each turn is `(text, [(name, args),
  ...])`, noted on the holder's `Collector` the way an adapter would note
  it.
- `FakeLLM.stream` / `complete` gain `tools=` / `tool_choice=`, recorded on
  the request, and the `llm_fakes` header's surface list follows.
- `templates/tools/finalize.j2`. `verify_templates.py` and
  `test_llm_fakes.py` render it; if the harness asks, its builder is
  registered.

## Decisions on open questions

- **Q2: sticky fallback**, as the spec recommends.
- **The `required` downgrade** reuses `probes.tool_choice`. It is a store
  module, which `inference` may import, so it stays where it is rather than
  moving into the leaf.
- **`spend_ceiling_usd`** is absent from `RunBudget` until S5, so S4 cannot
  silently ignore a ceiling.

## Files

**Modify:**
- `tool_calls.py`, `inference.py`, `llm.py` (none expected beyond the
  rebase), `wire.py` (`with_thinking_off`), `model_guidance.py`
  (`with_appended_keeping`).
- `routes/common.py` (`_bounded_call` over `deadline`),
  `store/routing.py` (`TOOLS_OPTIONAL`), `store/inference/resolve.py`
  (`known_lacks`), and nothing in `store/usage.py`.
- `tests/llm_fakes.py`, `CONTRIBUTING.md`.

**Create:** `grimoire/deadline.py`, `templates/tools/finalize.j2`,
`tests/test_tool_loop.py`, `tests/test_tool_guard.py`.

**Tests in `test_tool_loop.py`:**
- the spec §6 loop list:
  - happy path, one row per turn with one `run_id` and increasing
    `loop_turn`;
  - finalize, conforming and then unreadable;
  - each limit with its `limit`: turns, tool calls, wall (stalling fake;
    `<= 0` means no wall), result characters and cancelled;
  - fallback: sticky, a degrade sibling not counted as a switch, ids
    rewritten, `_opaque` not sent, thinking off, the `required` downgrade;
  - failure: an error on turn 1 raises, a turn-3 failure is `failed`, the
    tool exception and `ToolError` results, unknown and non-conforming
    calls;
  - structure: a nested loop raises, an unfrozen `PreparedMessages` raises,
    a `TOOLS_OPTIONAL` route runs on `unknown` and raises on a known `no`;
  - the executor is bounded, and saturated by abandoned threads;
- a scripted three-turn run over `frozen_copy.copy_home(tmp)` with read-only
  tools.

## Verification

- The tests above.
- `test_tool_calls.py`, `test_tool_wire.py`, `test_inference_generate.py`,
  `test_llm*.py`, `test_usage_fields.py`, `test_usage_estimate.py`.
- `test_routes.py -k bounded`, or whichever tests hold `_bounded_call`.
- The speed-mode guard list with `-n 3`, plus `test_tool_guard.py` and
  `test_regex_prompt_guard.py`.
- `make check-lint check-mypy`, and `make check-templates`.

## As built (drift from the design above)

- **Cancelled.** `cancelled()` returning True raises `asyncio.CancelledError`
  before the next send or execution: cancellation is the run's (spec §5), and
  `LoopResult.limit` keeps the spec's vocabulary.
- **Terminal calls.** A valid terminal call anywhere in a turn ends the run;
  every other call of that turn is recorded `not_run` (the spec names only
  the calls after it).
- **Ids.** A provider's call id is kept while it is inside `SAFE_ID`, so one
  history is valid on every provider; an id outside it (or none) is minted
  at once. At a switch every id in the history is rewritten to the loop's.
- **Recursion.** The tasks a tool executes in (`deadline.bounded` runs the
  executor in a task of its own) are marked as the loop's, so a tool cannot
  start a loop inside it.
- **The routing guard is unchanged.** `test_operation_guard.py`'s new loop
  half holds the literal task, the route and `resolved=`; the routing
  guard's recogniser (`require_inference`) already sees the resolution.
- `verify_templates.py` compares `inference.finalize_message` with
  `tools/finalize.j2`, and `templates/README.md` documents it.
