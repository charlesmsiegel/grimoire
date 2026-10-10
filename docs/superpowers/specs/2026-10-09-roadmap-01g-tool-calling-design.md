# 01g. Tool calling: a bounded loop, Decision-as-tool, and run budgets

**Status:** Draft — cross-linked; spec gate pending.
**Date:** 2026-10-09
**Roadmap:** 01g in `ROADMAP-CHECKLIST.md`. Lane: retrieval (it waits on 01f;
12 waits on it). Size L.
**Baseline:** `main` at `35c1fb7`.
**Reconciles:**
- The 2026-10-06 bundle draft `12-bounded-agentic-investigation.md`, sections
  4-8 ("tool design", "write posture", "budgets", "trace", "runtime
  ownership"). This spec takes the runtime half of that draft: the tool
  registry, schema validation, budgets, cancellation, usage attribution and the
  trace. 12 keeps the toolset and the eval gate.
- The bundle draft `02-decision-integration.md` section 9 ("Decision as a tool
  for generative models"), whose call shape and "budgets must prevent a model
  from recursively outsourcing every sentence" rule become 01g-C5.
- `2026-10-07-inference-backend-refactor-design.md` (01): section 6.2 left
  `tools` out of the capability vocabulary ("no call site uses it"), and its
  non-goals listed "agent orchestration, tool use". This spec is where both
  are taken up.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| `generate` / `decide` operations, `wire.Target` / `wire.Chain`, the adapter registry, capabilities with provenance, the seam refusal (`requires`, `incapable`), `usage.Meter` | 01 (landed) | The substrate every section here extends. | Hard |
| 01f-C1 (`generate(schema=)` per attempt) | 01f | The loop's finalize turn asks for the caller's final record with a schema (section 3.7). | Hard |
| 01f-C2 (schema refusal re-sent without the mode) | 01f | The finalize turn inherits it. | Hard |
| 01f-C3 (`schemas.check`, `schemas.find_object`) | 01f | Every tool's parameter schema passes `schemas.check`. Arguments and the final record are read with `find_object`. | Hard |
| 01d-C1 (task-level fallback policy) | 01d | Whether a loop's task may fall back at all. 01g honours `chain.fallback is None` and adds no policy of its own. | Soft |
| 01f-C1 (`generate(max_tokens=)`, `wire.Target.with_output_cap`) | 01f | The per-turn output cap of section 3.9 is 01f's per-call cap, applied to every turn. | Hard |
| 01i-C1 (`wire.Limits`: window and max output) | 01i | A tighter per-turn output cap and prompt-growth check where known (section 3.9). Without it the loop uses its own declared cap. | Soft |
| 01c-C2/C3 (distribution and seeded sampling) | 01c | The decide tool passes a distribution through when one exists. It never samples, and never fabricates one. | Soft |
| 01b-C1 (capture at every decide site) | 01b | The decide tool's inner `decide` call captures through 01b's mechanism once it exists, and through `decide(capture=)` before then. | Soft |

## Required by

Rebuilt from the edges in `ROADMAP-CHECKLIST.md`: 02 ← 01g-C1..C6 (H, for
02-C4 only); 12 ← 01g-C1..C5 (H); 01h ← 01g-C3 run id (S).

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 01g-C1 | 12 (hard), 02-C4 (hard) | 12's `investigation` route declares `requires=("tools",)`, and the seam refuses a primary known unable to call tools. 02-C4 offers its play tool only where the scene route's primary is not a known `no`. |
| 01g-C2a/C2b | 12 (hard), 02-C4 (hard) | The loop primitive: the caller executes its tools, supplies the run id, and gets the terminal call (or the final turn) back. |
| 01g-C3 | 12 (hard), 02-C4 (hard) | Every model turn is a ledger row under the caller's `run_id`, with capture and a trace. |
| 01g-C3 | 01h (soft; 01h-C5) | 01h-C5's optional `run_id` on `embed_sync` / `embed_groups_sync` is filed in 01g-C3's `run_id` ledger field, so embeds made inside a tool belong to their run. |
| 01g-C4 | 12 (hard), 02-C4 (hard) | Turns, tool calls, decisions, wall clock, spend and per-turn output cap, enforced before each send; the result names the limit that stopped the run. An unpriced model under a ceiling is refused before sending (a recorded cross-spec decision 12 follows). |
| 01g-C5 | 12 (hard, for its `decide` tool), 02-C4 (hard) | A generating model calls `decide()` as a tool, capped per run, metered under a task the caller names (12's `investigation-decide`, 02's `turn-tool-decision`). |
| 01g-C6 | 02-C4 (hard) | The final loop turn streams through the generate streaming path, and a tool call after visible text is declined. |

## 1. Current state (reconciled against main)

### 1.1 Nothing in the app calls a tool

- The adapters stream **text only**:
  - OpenRouter reads `choices[0].delta.content` and nothing else
    (`openrouter.py:326-330`). A `delta.tool_calls` fragment falls through as
    a frame with no text.
  - The Anthropic reader answers "nothing" for "a tool's partial JSON"
    (`anthropic.py:482-492`) and keeps `stop_reason` only to detect a refusal
    (`anthropic.py:464-472`, `494-499`).
  - The Claude Agent SDK path is sent `allowed_tools=[], max_turns=1` and
    yields only `TextBlock`s (`claude_agent.py:162-180`).
- **No message shape carries a tool turn.** Both providers that fold
  messages refuse any role other than system, user and assistant:
  `openai_compatible._strict_messages` (`openai_compatible.py:110-114`) and
  `anthropic._messages` (`anthropic.py:222-225`). Each says folding an
  unknown role "would misattribute its content".
- **`tools` is not a capability.** `capabilities.NAMES`
  (`store/inference/capabilities.py:54-55`) and `providers.CAPABILITIES`
  (`store/inference/providers.py:25-28`) have seven names. Neither includes
  `tools`.

### 1.2 The facade is one call returning text

- `LLMClient.stream` / `complete` take `(messages, chain, usage, *, schema)`
  and produce text (`llm.py:1351-1408`).
- `_resilient` (`llm.py:878-1114`) owns retries, the fallback, the idle bound
  and the per-attempt usage stamp. Its central rule is "nothing is ever
  retried once prose has reached the caller". An empty chunk is a heartbeat,
  not text.
- **A side channel already rides the usage holder.** Reasoning reaches its
  display consumer through `usage[llm_reasoning.KEY]`, a `Buffer`. `_stamp`
  preserves the buffer across its per-attempt `usage.clear()` and calls
  `begin()` on it, so a retried attempt's reasoning is discarded
  (`llm.py:578-581`; `llm_reasoning.py:1-55`). Tool-call deltas need exactly
  that shape: not prose, collected beside the text, reset per attempt.
- Each call files its own outcome to the health observer (`_observe`). A
  refused envelope (schema, preset) is deliberately *not* observed
  (`llm.py:1043-1061`).

### 1.3 The seam and the routing registry

- A route declares `operation` (`generate` or `decide`, `routing.OPERATIONS`,
  `store/routing.py:61`), a `default_role` and `requires`
  (`store/routing.py:32-58`).
- The resolver turns `requires` into groups of needs (`resolve._needs`,
  `resolve.py:560-565`), reports what an attempt is *known* unable to do
  (`_missing`, `resolve.py:581-590`), and the seam refuses a primary on a
  known `no` with 409 `incapable` (`resolve.incapable`, `resolve.py:1240-1269`).
- A fallback known unable is reported (`fallback_missing`) and never rides.
- The image route is the one route that uses `requires` today
  (`requires=("vision",)`, `store/routing.py:132`).

### 1.4 Metering

- One `store.usage.Meter` files one row per call (`store/usage.py:458-643`).
- Rows carry attribution fields:
  - `campaign`, `scene`, `post`;
  - `round_id` and `response_id`, which group a group-play round and a
    reply's rerolls (`usage.py:629-643`);
  - what served the call, from the target's account (`Meter.SERVED`).
- **There is no `run_id`.** A failure is recorded at `Meter.done` and nowhere
  else (CLAUDE.md, "Instrument LLM failures at `usage.Meter.done`").

### 1.5 Budgets

- **`llm_call_budget`** (`store/config.py:564`, default `"300"` seconds,
  `config.py:159`) is a per-call *duration* ceiling.
  `routes.common._bounded_call` (`routes/common.py:544-600`) enforces it on
  one-shot generations by abandoning the task rather than waiting on its
  unwinding.
- Absorb's `_Budget` bounds a whole sequence. Decide callers hand a budget in
  as `around` (`inference.py:99-103`).
- **Campaign budgets only warn.**
  - `usage.budget` measures `cost_usd` alone, under `Rates.off()`, and reports
    `off|ok|warn|over` (`store/usage.py:1697-1740`).
  - Nothing refuses a call on `over`.
- **Pricing arithmetic exists.**
  - `pricing.estimate(entry, prompt_tokens=, completion_tokens=)` answers None
    unless both counts and both rates are known (`store/pricing.py:336-401`).
  - `pricing.rate_for_call` is the one precedence function
    (`pricing.py:487-519`).
  - `usage.Rates.estimate` refuses a native-decision row whatever the rates
    (`usage.py:838-863`).

### 1.6 Detached runs

- Five run classes (`routes/runs.py:104`): `turn`, `review`, `background`,
  `draft` and `maintenance`. Only `turn` and `review` hold an exclusion key
  (`runs.py:108`).
- A detached run's id is `uuid4().hex` (`runs.py:252`).
- A reserving route may not be `async def` (CLAUDE.md, "Detached runs").

### 1.7 `decide`

- `inference.decide(task, items, *, client, resolved, explain, campaign,
  scene, post, round_id, capture, around)` (`inference.py:684-716`) opens its
  own meter per structured chunk and per native item.
- It refuses a resolution for another task or operation before any meter
  opens.
- `test_operation_guard.py:965-992` holds every call site to a **literal**
  task on a decide route with `resolved=`, and requires every decide task to
  be decided by some call site (`test_operation_guard.py:994-1000`).

### 1.8 The Claude Agent SDK's own tool model

The project pins `claude-agent-sdk>=0.1` in the `claude` extra
(`backend/pyproject.toml:24-25`). The installed SDK in this container
(0.2.159, `claude_agent_sdk/types.py`) documents a model that differs from
every other provider:

- **The SDK owns the loop.** A `query()` runs model turns and tool executions
  itself, up to `max_turns`. Tools are either built-in (`Read`, `Bash`, ...)
  or MCP tools. In-process tools are `@tool` handlers served by
  `create_sdk_mcp_server` (`claude_agent_sdk/__init__.py:251-330, 491-560`).
- **`tools` and `allowed_tools` differ.** `tools=[]` "disable[s] all built-in
  tools", and `allowed_tools` only lists tools "auto-allowed without prompting
  for permission" (`types.py:1965-1985`). `claude_agent.py:163` passes only
  `allowed_tools=[]`, so as the SDK documents it the built-in tools are still
  in the model's context. `max_turns=1` stops the run before any tool result
  reaches the model, so no tool output can enter a reply today. But this is
  worth fixing on its own (section 9, question 6).
- **Interception and limits.** A `PreToolUse` hook can answer `"defer"`,
  which stops the run and reports the call in `ResultMessage.deferred_tool_use`
  (`types.py:1301-1311, 1356`). `max_budget_usd` stops a query at a
  subscription-equivalent cost.
- **Usage arrives per query.** It comes in the `ResultMessage` (`usage`,
  `total_cost_usd`, `num_turns`). `AssistantMessage.usage` exists in this
  version (`types.py:1140-1151`), but not across the declared version floor.

### 1.9 What the drafts asked for, against this

12's draft asks for "a Grimoire-owned agent orchestrator rather than giving
one provider SDK unrestricted filesystem tools". It wants purpose-built read
tools, proposal tools instead of mutators, budgets with explicit terminal
states, and a trace of what was inspected. 02's draft asks for a `decision`
tool "the Primary model" can call, with budgets that "prevent a model from
recursively outsourcing every sentence". Nothing in the current code
contradicts either.

The one correction: the Claude Agent SDK cannot be driven like the other
three adapters (section 3.13). So the checklist's "across the OpenRouter,
OpenAI-compatible and Claude Agent adapters" is split here into C2a (the
three HTTP adapters, Anthropic included) and C2b (the SDK).

## 2. Goal (and what is explicitly not the goal)

One provider-neutral tool-calling substrate that later specs build on:

1. **Tools are the caller's.** The caller declares them and executes them
   through the executor it hands the loop. An adapter or a provider SDK never
   executes one. They are read-only unless they *propose*, and a terminal
   call comes back to the caller unexecuted.
2. **A bounded loop** of model turns and tool executions, with:
   - every turn metered under one run id;
   - a budget checked **before** every send;
   - an explicit terminal status;
   - a trace of what was called.
3. **A `tools` capability** with provenance, and a seam refusal for a route
   that requires it.
4. **Decision-as-tool:** a generating model may ask a bounded closed question
   through `decide()`, capped per run, with no way to recurse.

Not the goal:

- **Any consumer.** 01g adds no route that calls tools, except the decide
  tool's own route (section 3.12), and no toolset. 12 owns investigation, and
  02-C4 owns play.
- **Write tools.** A tool never writes the store (section 3.6).
- **Provider-hosted tools** (web search, code execution, a provider's own
  file tools), MCP servers outside the process, and parallel tool execution.
- **Making a scene turn agentic.** Play keeps its single streamed call unless
  02-C4 opts a turn in behind its eval gate.

## 3. Design

### 3.1 Shape of the whole

```
caller (routes/, inside a detached run)
  resolved = require_inference("<task>", cid)        # route requires ("tools",)
  result = await operations.run_tools("<task>", messages,
               toolset=..., execute=..., client=client, resolved=resolved,
               budget=RunBudget(...), run_id=run.id, campaign=cid, scene=sid,
               final_schema=None | {...}, capture=...)
                 |
                 v
inference.run_tools  (the loop; the only door, in inference.py)
  for each turn:
    budget.check_turn(projection)  --refused-->  finalize or stop
    with store.usage.meter(task, ..., run_id, loop_turn) as m:
        install tool_calls.Collector in m.usage
        text = await client.complete(messages, chain, m.usage,
                                     tools=wire_tools, tool_choice=...)
    calls = collector.calls()
    no calls -> final (parse final_schema if any) -> done
    for each call (in order):
        budget.check_tool()       --refused-->  error result "budget"
        terminal call -> return it unexecuted (final_call)
        result = await execute(call, ctx)   # the caller's executor
        append assistant tool turn + tool results
  -> LoopResult(status, text, final, messages, trace, rows, error)
```

- **New gateway leaf `grimoire/tool_calls.py`.** Standard library plus
  `schemas` (01f-C3); never the store. It holds the data shapes (section
  3.2), the per-kind wire lowering helpers the adapters call (section 3.3),
  the stream `Collector` (section 3.4) and the pure budget arithmetic
  (section 3.9).
- **`inference.py` gains the loop** (`run_tools`, `stream_tools`), because
  `client.complete` may be spelled only there (`test_routing_guard.py:236-244`)
  and its meters are scanned there (`test_usage_guard.py`).
- **`routes/tool_decision.py` (new) builds the decide tool** for a task the
  caller names and resolves at its own call site, where the literal is
  visible to both guards (section 3.12).

### 3.2 Provider-neutral shapes (`tool_calls.py`)

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                 # ^[A-Za-z][A-Za-z0-9_]{0,63}$, unique in a toolset
    description: str          # what the model reads; <= MAX_DESCRIPTION chars
    parameters: dict          # JSON Schema object; passes schemas.check (01f-C3)
    fn: Callable[[dict, ToolContext], ToolOutput | Awaitable[ToolOutput]]
    effect: Literal["read", "propose"] = "read"
    terminal: bool = False    # ends the loop; returned to the caller, never executed
    timeout: float = TOOL_TIMEOUT_S   # per execution, seconds
    max_result_chars: int = MAX_RESULT_CHARS

@dataclass(frozen=True)
class ToolOutput:
    text: str                 # what the model is sent (truncated to max_result_chars)
    refs: tuple[str, ...] = ()        # stable store refs inspected, for the trace
    proposal: dict | None = None      # effect == "propose" only; collected, never applied

@dataclass(frozen=True)
class ToolContext:            # what a tool may know; nothing it can write with
    campaign: str
    scene_identity: str
    run_id: str
    deadline: float           # monotonic; the tool's own reads should respect it
    root: str                 # the store root pinned at loop start (see 3.6)

class ToolError(Exception):
    """Raised by a tool to send the model `str(exc)` as an error result.
    Any other exception is sent as a generic error (3.10)."""

@dataclass(frozen=True)
class ToolCall:
    id: str                   # the loop's own id (3.11), never the provider's
    name: str
    arguments: dict | None    # None when the provider's JSON did not parse
    raw_arguments: str        # as received, for the trace and the error result
    provider_id: str = ""     # the provider's id for it, kept for the capture

@dataclass(frozen=True)
class Toolset:
    tools: tuple[ToolSpec, ...]
    # validated at construction: names unique and well formed, at most
    # MAX_TOOLS, each schema passing schemas.check with a top-level object
```

**Messages.** The loop keeps Grimoire's existing OpenAI-style message list
and adds two neutral shapes. Adapters lower these; nothing above the
adapters spells a provider's format.

```python
{"role": "assistant", "content": "<text, may be ''>",
 "tool_calls": [{"id": "gc_<run8>_<n>", "name": "...", "arguments": {...}}],
 "_opaque": {...}}           # optional; see 3.4 -- provider state, adapter-owned
{"role": "tool", "tool_call_id": "gc_<run8>_<n>", "name": "...",
 "content": "<text>", "is_error": False}
```

**Constants**, each argued structurally and to be tuned against real
prompts later:

- `MAX_TOOLS = 32`. Providers document limits far above this. Past a few
  dozen tools, a model's choice among them degrades and the definitions
  alone fill the prompt. 12's draft lists fourteen read tools.
- `MAX_DESCRIPTION = 1024` characters.
- `MAX_RESULT_CHARS = 8000` per result. A result is sent back on every later
  turn, so one oversized read taxes the rest of the run.
- `TOOL_TIMEOUT_S = 10.0`. A tool is a store read. A read that has not
  answered in ten seconds has hit a cold library or a sync stall, and the run
  is better told so than held.

### 3.3 Wire lowering per adapter (01g-C2a)

Each adapter's `generate(messages, target, usage, *, schema=None,
tools=None, tool_choice=None)` sends tools **only when given**. Any call
without tools is byte-for-byte the call it was before, which is the slice-F
discipline (`adapters.py:137-141`).

| | OpenRouter / OpenAI-compatible | Anthropic Messages |
|---|---|---|
| tool definitions | `tools: [{type: "function", function: {name, description, parameters}}]` | `tools: [{name, description, input_schema}]` |
| `tool_choice` | `"auto"`, `"none"`, `"required"` | `{type: "auto"}`, `{type: "none"}`, `{type: "any"}` |
| assistant tool turn | `{role: "assistant", content, tool_calls: [{id, type: "function", function: {name, arguments: json.dumps(args)}}]}` | assistant `content`: optional `text` block, then `tool_use {id, name, input}` blocks; `_opaque` thinking blocks first (3.4) |
| tool result | `{role: "tool", tool_call_id, content}` (`is_error` is prefixed into `content` as `"ERROR: "`) | a **user** turn of `tool_result {tool_use_id, content, is_error}` blocks |
| stream: call fragments | `choices[0].delta.tool_calls[i]` `{index, id?, function: {name?, arguments?}}` | `content_block_start` `tool_use {id, name}`, then `content_block_delta` `input_json_delta {partial_json}` |
| stream: end of turn | `finish_reason: "tool_calls"` (or `"stop"` with calls on some upstreams) | `message_delta.delta.stop_reason: "tool_use"` |

Folding rules the lowering must keep:

- **`openai_compatible._strict_messages`** keeps its system folding and the
  user-first rule. It passes an assistant message carrying `tool_calls`
  through unmerged, and passes each `tool` message through in place.
  Alternation is checked over user and assistant turns with the tool messages
  between them. A strict endpoint that refuses a `tool` role is refusing
  tools, which section 3.5 covers. It is never a reason to fold a result into
  a user turn, which would misattribute it exactly as the current refusal
  comment warns.
- **`anthropic._messages`** lowers a `tool` message to a `tool_result` block
  in a user turn. Consecutive results merge into one user turn, as
  consecutive user turns already do. A `tool_result` turn may not be preceded
  by folded system text in the same turn, because the API wants results
  first, so pending system text is flushed into a user turn *after* the
  results.
- Tool ids are the loop's own (section 3.11). They match
  `^[A-Za-z0-9_-]{1,64}$`, which both providers accept.
- **`strict` function calling is not requested.** OpenAI's
  `function.strict: true` would hold arguments to the schema. But
  OpenRouter's upstreams vary in honouring it, and the loop validates
  arguments itself (section 3.6), so it adds a refusal surface for little
  gain. Section 9, question 3 revisits this.

### 3.4 Reading calls out of the stream: the `Collector` side channel

`tool_calls.Collector` lives in the usage holder under `tool_calls.KEY`. It
works exactly like `llm_reasoning.Buffer`:

- **Installed by the loop** in the meter's holder before the facade call.
- **Preserved by `llm._stamp`** across its per-attempt `usage.clear()`, with
  `begin()` called on it. A retry or a fallback attempt therefore starts with
  no fragments from the attempt that failed. This is a three-line change
  beside the reasoning one at `llm.py:578-581`.
- **Fed by the adapters.** `tool_calls.from_openai_chunk(obj, usage)` is
  called beside `llm_reasoning.from_chunk`. `_Reader._block` hands
  `tool_use` and `input_json_delta` to `tool_calls.feed_anthropic(...)`.
  Fragments accumulate by index or block id. Each fragment is also noted for
  the local token estimate (`llm_usage.note_reply`), because arguments are
  billed completion tokens.
- **Opaque state.** Two kinds of provider state must be echoed back on the
  next turn of the same provider:
  - signed Anthropic `thinking` and `redacted_thinking` blocks;
  - OpenRouter `reasoning_details`, when a reasoning model returns them.

  Both providers document refusing, or degrading, a tool-use continuation
  that drops them. The collector keeps that state with its provenance
  (`kind`, `provider_id`, `model`), and the loop stores it on the assistant
  message as `_opaque`. An adapter re-sends `_opaque` **only** when its
  provenance matches the target it is sending; anything else is dropped
  (section 3.11).
- **Read after the call.** `Collector.calls()` returns the completed calls
  in order. Arguments that are not a JSON object parse to `arguments=None`,
  with `raw_arguments` kept. `Collector.finish_reason` is the provider's end
  reason, normalised to `stop`, `tool_calls`, `length` or `other`.

The facade's text rule is untouched. A turn's text still streams. A retry is
still possible only while no text has reached the caller. A tool-call
fragment is not text, and every SSE line already yields `""` as proof of
life (`openrouter.py:300-307`, `anthropic.py:324-328`).

If a provider streams text *and* calls and then dies mid-arguments, the
facade raises as for any mid-prose failure (text was sent). If it dies
before any text, it retries as today, and the collector has been reset.

### 3.5 Facade and adapter registry changes

- `LLMClient.stream` and `complete` gain `tools: tuple[dict, ...] | None =
  None` and `tool_choice: str | None = None`. Both are keyword-only and
  passed through `_streamed` and `_dispatch` to the adapter. Absent, every
  call is unchanged.
- **The adapter registry gains `calls_tools`**, beside `carries_images` and
  `embeds`. It is True for `openrouter`, `openai_compatible` and
  `anthropic`, and False for `claude` until C2b lands. A test holds it equal
  to "the preset's `never` lists `tools`" for every preset of the kind, the
  same discipline as `embeds` against `resolve.embed_endpoint`.
- **A refused `tools` field.** This is a 400 or 422 whose message names
  `tools`, `tool_choice` or `functions`, recognised by
  `llm._tools_refusal(exc, target)` and modelled on `_schema_refusal`
  (`llm.py:851-875`). It is **not observed** as a health failure, because
  the connection answered and refused a feature. The next route is tried as
  for any failure. If every route fails, the error is
  `LLMError("bad_response", ..., code="tools_refused")`. Unlike a schema,
  tools cannot be dropped and the call re-sent: a loop without tools is not
  the call that was asked for.
- **`llm_usage.note_prompt`** counts tool definitions and assistant
  `tool_calls` arguments into the estimate's prompt side, so a provider that
  reports no counts is not under-counted by the size of its toolset.

### 3.6 Who executes tools, and what they may do

**The caller executes its tools.** An adapter never does, and neither does a
provider SDK. The loop hands each validated call to the caller's executor and
sends back what the executor returns:

```python
Execute = Callable[[ToolCall, ToolContext], Awaitable[ToolOutput]]
```

- `run_tools(..., execute=...)` takes the caller's executor. The caller runs
  its tools however it must: 12 runs its reads through
  `anyio.to_thread.run_sync`, and 02-C4 answers its one decide tool in place.
- `tool_calls.registered(toolset)` is the default executor for a caller that
  has nothing special to do. It dispatches by name to each `ToolSpec.fn`
  under the rules below. The bullets below describe that default, and bind
  any executor a caller writes on the read-only rule.
- **A terminal tool is never executed.** A `ToolSpec` with `terminal=True`
  (12's "finish" and "propose" endings) ends the loop when it is called:
  its arguments are validated and the call is handed back to the caller as
  `LoopResult.final_call` for the caller to act on. The loop never acts on
  it.

- **Sync tools** (`def fn(args, ctx)`) run in a worker thread
  (`asyncio.to_thread`). Store reads touch the filesystem, and the loop runs
  on the lifespan loop inside a detached run.
- **Async tools** (`async def`) are awaited on the loop and must not block.
  The decide tool (section 3.12) is the one shipped here.
- **A tool's timeout is a wait, not a kill.** The loop stops waiting after
  `min(spec.timeout, deadline - now)`, sends the model an error result
  (`"timed out"`), and leaves the thread to finish. This is
  `routes.common._bounded_call`'s abandon semantics, for the same reason:
  a cancelled thread cannot be stopped, and waiting on it would make the
  deadline soft. A read-only tool that finishes late harms nothing, which is
  why "read-only" is the rule and not a preference.
- **Arguments are validated before execution.** They must be a JSON object
  that conforms to the tool's `parameters` (`tool_calls.conforms`, the
  01f-C3 subset, checked structurally: types, required keys, no extra keys,
  enum membership). A failure is an error result naming the first problem,
  and it counts against `max_tool_calls`.
- **Read-only.** The contract is:
  - A tool is handed a `ToolContext` that carries ids and the pinned root.
    It carries no lock, writer or client.
  - `effect="read"` must not write the store, take a campaign lock, or
    start a run.
  - `effect="propose"` may not either. It returns `ToolOutput.proposal`,
    which the loop collects in `LoopResult.proposals`, in call order.
  - The **caller** applies proposals after the loop, under its own lock and
    through the store's existing validation and review. This is the 12
    draft's "proposal tools", made structural.
- **The guard** is `test_tool_guard.py` (new). It parses the package's ASTs,
  finds every `ToolSpec(...)` construction and resolves its `fn` to a
  function in the package. It fails if that function's module imports
  `store.atomic`, or if the function calls `campaign_lock`, `hold_all` or any
  `runs.run_*` / `start_detached`. It is a guard of the same honesty as
  `test_atomic_guard.py`: it sees direct calls, not a write three helpers
  down. A clearance is `# tool-ok: <reason>`, capped like the other markers.
- **The store root is pinned at loop start** (`ToolContext.root`), and the
  loop stops with `failed` (error `store_moved`) if `store.home()` no longer
  answers it between turns. That is the `maintenance` class's rule for a
  root that changes mid-run (CLAUDE.md, "It pins the store root"). It applies
  here because a tool reading the new root would hand the model another
  library's records under this run's ids. `PUT /config/data-dir` already
  refuses while any run is live, so this is a second line of defence.
- **Transcript text goes through the regex view.** A tool that returns
  transcript text to the model is an LLM reader, and must take it through
  `store.regex.view.view(..., phase="prompt")`. `test_regex_prompt_guard.py`
  already fails a reader that never asks. Tools are inside its scan as
  package code, so nothing is added there; this paragraph is the reminder
  for 12's toolset.

### 3.7 The loop primitive (01g-C2a)

```python
# inference.py
async def run_tools(task: str, messages: list[dict] | PreparedMessages, *,
                    toolset: tool_calls.Toolset, execute: tool_calls.Execute,
                    client: LLMClient,
                    resolved: ResolvedInference, budget: tool_calls.RunBudget,
                    run_id: str,
                    campaign: str = "", scene: str = "", scene_identity: str = "",
                    post: int | None = None, final_schema: dict | None = None,
                    tool_choice: Literal["auto", "required"] = "auto",
                    capture: TurnCapture | None = None,
                    decline_after_text: bool = False,
                    reasoning: llm_reasoning.Buffer | None = None
                    ) -> tool_calls.LoopResult

def stream_tools(...same...) -> AsyncIterator[tool_calls.LoopEvent]
    # run_tools is stream_tools, drained; one implementation.
```

**Refused before anything is sent** (`ValueError`, so no meter opens):

- `resolved` is for another task or operation, or resolved nothing (as
  `generate`);
- `task`'s route does not list `"tools"` in `requires`;
- the toolset is empty;
- `final_schema` fails `schemas.check`;
- the prompt ends in an assistant turn (a prefill), or a `PreparedMessages`
  carries per-attempt tails;
- `run_tools` is already running in this task (section 3.12, no recursion);
- `run_id` is empty. **The caller supplies the run id.** A loop inside a
  detached run passes that run's `Run.id` (`routes/runs.py:252`), so the run
  registry, the ledger and the trace name it the same way. The loop never
  mints one.

Also before anything is sent, `tool_calls.RunRefused(kind="unpriceable")`
is raised when the budget sets a spend ceiling and any attempt on the chain
has no rate (section 3.9). That is a refusal the caller turns into a 409
before reserving, like the seam's.

**Each turn**, numbered `k = 1..`:

1. **Check the budget for a turn** (section 3.9). If it refuses and a
   finalize turn is reserved and still affordable, go to step 7. Otherwise
   stop with `budget_exhausted`.
2. **Open a meter**:
   `store.usage.meter(task, campaign=, scene=, post=, run_id=, loop_turn=k)`.
   Install a fresh `Collector` in `m.usage`.
3. **Call the facade.** Send `client.complete(messages, chain, m.usage,
   tools=wire_tools, tool_choice=...)` under the turn's wall-clock bound
   (section 3.9). `stream_tools` uses `client.stream` instead and forwards
   each text delta as an event. `chain` is the resolution's, or the sticky
   chain after a fallback served (section 3.11).
4. **Settle.** Hand `capture` the turn (section 3.10) and read the calls.
   - **No calls** means the model has answered:
     - `final_schema is None`: the result is this turn's text, status
       `completed`.
     - Otherwise the text is read with `schemas.find_object` and checked with
       `tool_calls.conforms`. A conforming object is `final`, status
       `completed`. Anything else goes to step 7, once.
   - **A call to a terminal tool** (section 3.6) ends the loop: its
     arguments are validated (a non-conforming one is an error result and
     the loop continues), and a valid one is returned as `final_call`,
     status `completed`, unexecuted. Calls after it in the same turn are
     answered with nothing and recorded in the trace as `not_run`.
   - **Calls after visible text, with `decline_after_text`** (01g-C6,
     section 3.8): none is executed. The turn's text is the answer, status
     `completed`, and each call is recorded as `declined: "after_text"` in
     the trace and in `LoopResult.declined`.
   - **`finish_reason == "length"` with calls** means the arguments were cut
     off. Each such call is answered with an error result ("your arguments
     were cut off; call again with a shorter request"), and the turn counts.
5. **Execute the calls in order**, one at a time, through the caller's
   `execute` (section 3.6). Before each, check the budget for a tool call. A call past `max_tool_calls`, or past the wall
   clock, is *not* executed. It gets an error result (`"not run: the run's
   tool budget is spent"`), because every provider requires a result for
   every call id. An unknown name, unparseable arguments or non-conforming
   arguments each get an error result and count. Results are truncated to
   `max_result_chars` with a marker (`"\n[truncated: N more characters]"`)
   and counted against `max_result_chars_total`.
6. **Append** the assistant message (text, the loop's call ids, `_opaque`)
   and the tool messages. A `PreparedMessages` is extended through
   `with_appended` (`model_guidance.py:211-223`), one message at a time, so
   every variant gets the same tail. The method drops the breakdown, which is
   correct because the old breakdown no longer measures the prompt. Go to
   step 1.
7. **Finalize** (only when `final_schema` is set, or when a budget stop
   reserved one). One turn with `tool_choice="none"` and the tool
   definitions still sent, because both providers require the definitions
   while the history holds tool calls. A rendered instruction is appended:
   `templates/tools/finalize.j2`, carrying the schema per 01f-C1's
   in-prompt rule.
   - It is sent through the 01f path (`schema=final_schema`, per-attempt
     structured mode and refusal re-send), metered as turn `k`.
   - A conforming object is `final`. The status stays `budget_exhausted` if
     a budget stop led here, and is `completed` otherwise.
   - A non-conforming reply is status `failed` with error
     `final_unreadable`, and the text is kept.

**`LoopResult`**:

```python
@dataclass(frozen=True)
class LoopResult:
    status: Literal["completed", "budget_exhausted", "failed"]
    limit: str = ""            # the limit that stopped the run (01g-C4), with
                               #   budget_exhausted: turns|tool_calls|decisions|
                               #   wall|spend|result_chars; "" otherwise
    text: str = ""             # the last model turn's text (the visible text, with C6)
    final: dict | None = None  # the conforming final record, with final_schema
    final_call: ToolCall | None = None  # the terminal call, validated, unexecuted
    declined: tuple[ToolCall, ...] = ()  # calls declined after visible text (C6)
    proposals: tuple[dict, ...] = ()
    messages: tuple[dict, ...] = ()   # the loop's appended turns, as sent
    trace: tuple[TraceEntry, ...] = ()
    rows: tuple[dict, ...] = ()       # every ledger row this run filed, in order
    error: LLMError | None = None     # with failed
    run_id: str = ""           # the caller's, echoed
```

**`LoopEvent`** (from `stream_tools`) is one of:

- `text(turn, delta)`;
- `turn_end(turn, finish, interstitial: bool)`, where `interstitial` is True
  for a turn that ended in tool calls;
- `tool_start(turn, call_id, name)`;
- `tool_end(turn, call_id, name, ok, chars, refs)`;
- `done(LoopResult)`.

`run_tools` drains the iterator and returns the `done` payload.

### 3.8 Streaming with tool calls

A turn's text streams as it arrives. Whether that text is the answer or
interstitial ("let me check the ledger") is known only when the turn ends,
so `turn_end.interstitial` says which. The loop never retracts text. That is
the facade's own rule, and the loop adds nothing that could break it,
because each turn is its own facade call with its own pre-text retry window.

A caller that must show only the final answer (an investigation behind a
spinner) uses `run_tools` and ignores text. A caller that streams to a
reader (02-C4's play turn) decides from these events how interstitial text
is drawn. That UX is 02-C4's. 01g guarantees only the ordering:

```
text* turn_end (tool_start tool_end)* ... done
```

`done` comes last, after every meter has filed.

**01g-C6: the final turn streams, and a call after text is declined.** A
scene contribution (02-C4) cannot be carried by a loop that only joins
replies, so:

- **Every turn is sent through the generate streaming path.** In
  `stream_tools` each turn is the same `client.stream(...)` call inside
  `inference.py` that `generate(stream=True)` makes: the same `_resilient`
  retries, idle bound and `""` heartbeats. `tool_calls.text_deltas(events)`
  turns the event stream back into the plain `AsyncIterator[str]` that
  `generate(stream=True)` yields, heartbeats included, so a caller such as
  `_stream_contribution` keeps its display and its watcher as they are.
- **A display reasoning buffer rides each turn.** The caller passes its
  `llm_reasoning.Buffer` as `reasoning=`, and the loop installs it in each
  turn's holder, so thinking still reaches the display across turns.
- **`decline_after_text=True`** makes visible text final. Once any turn has
  yielded a non-empty delta, a tool call that turn ends with is **not
  executed**. The loop ends with that text as the answer, status
  `completed`, and records each such call as declined after text. No turn
  follows it, so the visible stream is one continuous generation. A tool
  call made before any visible text runs normally, and the visible stream
  starts on the next turn. That is 02's rule (02 section 8.2), made a loop
  option rather than a caller's workaround.

### 3.9 The run budget (01g-C4)

```python
@dataclass(frozen=True)
class RunBudget:
    max_turns: int = 6               # model turns, the finalize turn included
    max_tool_calls: int = 12         # executions attempted, refused ones included
    max_decisions: int = 2           # decide-tool calls (3.12); also count as tool calls
    wall_seconds: float | None = None    # None: config.llm_call_budget(); <= 0: no wall
    spend_ceiling_usd: float | None = None   # None: no spend axis
    max_output_tokens: int = 2048    # per turn; sent as the turn's output cap
    max_result_chars_total: int = 48_000
    reserve_final: bool = True       # keep one turn's worth for finalize
```

The defaults are structural and will be tuned against real runs (12-C2's
eval gate):

- Six turns gives a model room to search, read, refine once and answer.
- Twelve tool calls allows two per turn.
- Two decisions keeps the decide tool a rare escape rather than the way the
  model writes.
- The wall clock defaults to `llm_call_budget` because a whole run should
  not be allowed what one call is not, unless its caller says so.
- 48,000 result characters is six full results. The figure is bounded by
  what a Fast model's window holds beside the prompt (01i-C1 tightens it
  where known).

**Enforced before each send**, in this order. The first refusal wins:

1. cancelled: the caller's `cancelled: Callable[[], bool] | None` (the
   detached run's `cancel_requested`, or 12's callback), asked before every
   send and every tool execution. `CancelledError` propagates anyway;
2. turns: `turns_used + 1 + (1 if reserve_final and final pending else 0) >
   max_turns`;
3. wall (only when `wall_seconds > 0`; `<= 0` is no wall, matching
   `_bounded_call`'s reading of `llm_call_budget <= 0`,
   `routes/common.py:584-586`): `elapsed + MIN_TURN_SECONDS > wall_seconds`,
   where `MIN_TURN_SECONDS = 5`, the shortest window in which a turn could
   plausibly answer;
4. spend: `projected_spent + projection(next_turn) > spend_ceiling_usd`.

Before each tool execution the checks are `tool_calls_used + 1 >
max_tool_calls`, then (decide tool only) `decisions_used + 1 >
max_decisions` and the decide spend check (below), then `elapsed >=
wall_seconds`. Result characters are checked when appending. A result past
`max_result_chars_total` is truncated to what remains and the run stops
after the turn, with `limit = "result_chars"`.

**A tool-call or decision limit stops the run, as the turn limit does.**
Once `max_tool_calls` or `max_decisions` is reached, every call beyond it in
that turn gets the "not run" error result, as every call id needs one. The
next turn is then the finalize turn (`tool_choice="none"`) when one is
reserved. Otherwise the run stops. Either way `limit` names the limit that
was reached, so a run that hit `max_tool_calls` never reports `turns`.

**The per-turn wall bound**, with its three cases kept apart:

- **`run_tools` (joined).** A turn's facade call runs under
  `min(llm_call_budget(), wall_seconds - elapsed)` (either term absent when
  `<= 0`), with `_bounded_call`'s abandon semantics. The plan moves those
  semantics into a gateway-neutral helper (`grimoire/deadline.py`) so that
  `routes.common._bounded_call` and the loop share one implementation.
- **The run's wall expiring** is a budget stop, not a failure: status
  `budget_exhausted`, `limit = "wall"`. The turn's meter files `aborted`
  (the overrun is raised with `store.usage.NOT_A_FAILURE` set False, as
  absorb's `BudgetRefused` is). Only an overrun of `llm_call_budget` itself
  is `LLMError("timeout")`, an error row, noted against the attempt that was
  running (`llm.ATTEMPTED`), as `routes.common._noting` does.
- **`stream_tools`.** Streamed prose is idle-bounded only, deliberately
  (`config.py:155-158`, `llm._guard`): a long reply must not be cut
  mid-sentence. So the wall bound **never interrupts a turn that has yielded
  visible text**. It only refuses the next send. Before any visible text,
  the turn is bounded as in the joined case.

**The per-turn output cap is 01f-C1's `max_tokens`.** Each turn's chain is
`inference.call_chain(resolved, max_tokens=budget.max_output_tokens)` (and
`schema=final_schema` on the finalize turn). That is the one function
`generate(max_tokens=)` sends through (01f section 3.9), so the loop
reimplements nothing:

- each target is `with_output_cap(cap)`;
- 01i-C1's maximum output tightens the cap where known;
- a refused cap is 01f's `CapRefusalError`.

Where the cap is not sent (`cap_sent` is False), it is not a bound, and
under a spend ceiling that attempt is unpriceable (above).

**How spend is estimated before a send.** This is the part that must not
break the three-columns rule (CLAUDE.md, "Costs"). It prices a call the way
the app's other pre-send estimate already does, the model-test preview
(`routes/config.py:1355-1390`, `probes.estimate_usd` and
`estimate_from_rates`).

- **Per-attempt price, in this order** (`tool_calls.price_for(attempt)`,
  built once per run off the event loop):
  1. **The attempt's cached catalog row** (`llm_connections.cached_row`), its
     per-token `prompt` and `completion` prices. A stated `0` is a free
     model: a reported price, projecting to `0.0`.
  2. **Otherwise the user's rates** (`pricing.rate_for_call`: the model's own,
     then `pricing.json`), **only when the attempt's preset does not
     `reports_price`**. For a provider that reports its own price, the ledger
     never prices its calls from the user's rates, so a `"": 0` default meant
     for local models would otherwise project a billed OpenRouter turn at
     `$0.00`, the exact failure the preview's docstring names.
  3. **Otherwise unpriceable.**
- **A projection, never accounting.** The guard figure is that arithmetic
  applied uniformly (price times counts). It never reads `cost_usd`,
  `estimated_usd` or `modelled_usd` from a row and adds them together. A turn
  a provider priced is re-priced here by the same rule, because the guard
  needs one unit. So it is a projection, like the Costs trend's "Estimated
  total": never spend, never written to the ledger as money, never shown as
  what the run cost. What the run cost is read from its rows by `run_id`, in
  three columns.
- **Projection for the next turn:** the price applied to `P` prompt tokens
  and `C` completion tokens.
  - **Turn 1:** `P` is `tokens.count_if_loaded` over the serialised messages
    plus the tool definitions, counted **in a worker thread**, never on the
    event loop, times `PROJECTION_MARGIN = 1.5`.
  - **From turn 2:** `P` is the previous turn's reported `prompt_tokens`
    (else its counted `P`) plus a count of the appended turns, with the
    margin on the appended part only.
  - The margin is structural, because `count_if_loaded` is not an upper
    bound: chars/4 under-counts CJK text, and a cl100k-style encoder
    under-counts Claude's tokenizer. To be tuned against real runs.
  - `C` is the turn's output cap. It is a bound **only where it is sent**:
    `inference.cap_sent(target)`, 01f-C1. An attempt whose cap is not sent
    (the Claude SDK; an OpenRouter model whose catalog omits `max_tokens`)
    is **unpriceable under a ceiling**.
  - Across the chain, the projection is the **maximum** over the attempts
    that could serve this turn.
- **Spent so far:** for each settled call of the run (every turn row, and
  every row in each decide-tool call's `Decision.usage`), the same rule
  applied to its row's counts: the reported counts, else the local estimate,
  else that call's own projection. A turn that failed and filed no row adds
  its projection, because a failed attempt can still bill.
- **What the ceiling cannot promise, stated:** the facade's retries, a
  connection dropped after generating, and 01f's prompt-only re-send in a
  finalize turn are each billed and not separately projected. A ceiling can
  therefore be overrun by **at most the billed failed attempts of one turn**
  (the primary's retries plus the fallback's one attempt). Every later send
  sees them in "spent so far".
- **Unpriceable means refused, not zero** (the recorded cross-spec decision
  in `ROADMAP-CHECKLIST.md`, which 12 follows). With a ceiling set, a chain
  on which any attempt is unpriceable raises `RunRefused("unpriceable")`
  before anything is sent. The message names the model and says to set rates
  for it, or run without a spend ceiling. A ceiling that counted an unpriced
  turn as free would not be a ceiling.
- **A subscription attempt** (`billing: subscription`) is projected by the
  same rule, so under a ceiling the guard bounds *usage at list price*, not
  money owed. `usage.budget` keeps such calls out of a campaign's spend
  (`usage.py:1711-1715`) for the opposite question. A `claude` attempt has
  no catalog and `reports_price`, so it is unpriceable, and a ceiling over a
  Claude subscription chain is refused, as section 3.13 already says.
- **Decide-tool calls go through the same pre-send check** (section 3.12):
  - before each decide call, `spent + projection(decide) <= ceiling`;
  - the projection is the rendered decide prompt's tokens and the decide
    call's output cap (`decide(max_tokens=)`, applied to its structured stage
    targets through 01f's `with_output_cap`), summed over every call the
    decision could make: each stage, plus each stage's prompt-only re-send;
  - a native decide stage is never modelled (`Rates.estimate` refuses it),
    so under a ceiling the decide tool is not offered when any stage is
    native, unpriceable or uncapped.
- **Campaign budgets are not read.** A campaign budget measures `cost_usd`
  only and only warns (`usage.budget`). Combining it with this projection
  would add columns. Whether an investigation should be refused while its
  campaign is `over` is the consumer's policy (section 9, question 4).
- **`tool_calls.tool_run_refusal(task, resolved, budget, prices) ->
  RunRefused | None`** is the same check as a pure preflight. A `def` route
  calls it **before reserving**, so 12's E2 and E3 answer 409 `unpriceable`
  before a 202, and `run_tools` repeats it at entry.

### 3.10 Metering, capture and the trace (01g-C3)

- **One meter per turn**, opened in `run_tools`. A finalize turn is a turn.
  A 01f re-send inside a turn files on that turn's holder (01f-C2), so it is
  that turn's row. Each row carries:
  - **`run_id`**: the caller's (section 3.7), normally the detached run's
    `Run.id`, so the ledger and the run registry share an id. The loop never
    mints one.
  - Embeds made inside a tool are filed under the same `run_id` through
    01h-C5's optional `run_id` on `embed_sync` / `embed_groups_sync`. The
    tool reads it from `ToolContext.run_id`.
  - **`loop_turn`**: `k`.
  - `usage.record`, `Meter` and `meter` gain both as optional keywords,
    written only when set, like `round_id`. An older build reads such a row
    exactly as it did.
  - `usage_rollup.VERSION` does not change, because no rollup aggregates
    either field.
  - Rows also carry `tool_calls` (the count of calls the turn asked for),
    written only when non-zero, so 12's eval gate can read tool use per turn
    from the ledger alone.
- **The decide tool's calls** file their own rows (section 3.12) with the
  same `run_id` and the `loop_turn` of the turn that asked. `decide` gains
  `run_id=` and `loop_turn=` keywords, passed to each meter it opens.
- **Failures** are filed at each turn's `Meter.done`, as for every call, so
  a failed turn is one `error` row with its kind. A budget stop is not a
  failure and files nothing. A tool exception is not an LLM failure: it is
  logged at error with the tool name and the exception's **type only** (an
  exception message from store code can quote record text), and the model is
  sent `"the tool failed"`. A `ToolError`'s message is the exception: it is
  meant for the model, and it reaches the model.
- **Capture** has three forms, each guarded so that a broken one costs only
  itself:
  - **Incoming responses** (`llm_capture`) are recorded per attempt, as
    today. `Capture.meta` gains `run_id` when the holder carries one.
  - **The prompt log.** `TurnCapture = Callable[[list[dict], dict,
    wire.Target], Awaitable[None]]` is handed each turn once it settles,
    outside its meter, exactly as `decide`'s `Capture` is
    (`inference.py:333-346`). Its arguments are the messages as sent (tool
    definitions included, as one system-side section), an outcome
    `{finish, calls: [{name, arguments}], text_chars}`, and the target that
    answered. The prompt viewer draws the outcome the way it draws the
    decision outcome (`OUTCOME_SECTION_ID`), as "not sent".
  - **The trace.** `TraceEntry(turn, kind: "model"|"tool"|"decide"|"stop",
    name, call_id, ok, refs, chars, elapsed_ms, note)` is accumulated in
    memory and returned in `LoopResult.trace`. It holds what 12's draft asks
    for (which tools were called, which refs they inspected, how large the
    results were, and why the run stopped) and **not** argument or result
    text. Persisting it is the consumer's (12-C2), inside its own store and
    privacy rules.
- **Logs.** Each turn's `Meter.done` and errors file as usual. The loop
  itself logs at info one line per run end: `run_id`, task, status, limit,
  turns, tool calls and elapsed. It logs no tool names with arguments, and no
  text. Logs may carry ids (CLAUDE.md, "Observability"), and tool arguments
  are prose.

### 3.11 Fallback across a loop

Each turn is one facade call, so each turn gets the facade's retries and
fallback (`_resilient`). Four rules make a mid-loop provider switch safe:

1. **Sticky after a fallback serves.** After each turn the loop reads
   `m.usage[llm.ATTEMPTED]`. **"The fallback served"** means
   `ATTEMPTED.provider_id != primary.provider_id or ATTEMPTED.model !=
   primary.model`. It is never an identity comparison: every turn sends new
   targets (`call_chain`, `_streamed`'s unflagging), so identity is never
   equal. It ignores `degrade`, since a degrade sibling is the same
   connection sending images as text. Once the fallback has served, every
   later turn sends `wire.Chain(that_target)` alone, with the facade's
   normal retry budget and no fallback behind it.
   - Flapping back to a primary that just failed would pay its retries
     again on every turn, and would let two models alternate within one
     chain of reasoning.
   - The trace records the switch (`kind="model"`, `note="fell back"`).
   - Whether a task may fall back at all is 01d-C1's policy. The loop only
     honours a chain that carries no fallback.
2. **Ids are the provider's until a switch.** The loop keeps the provider's
   own call ids while the same provider serves, because an OpenRouter
   `reasoning_details` entry (a Gemini thought signature, for one) can
   reference its call id, and renaming would orphan it. At a switch to
   another `(provider_id, model)`, every id in the history the new target is
   sent is rewritten consistently (call and result alike) to
   `gc_<run_id[:8]>_<n>`, which matches `^[A-Za-z0-9_-]{1,64}$`. The
   original stays in `ToolCall.provider_id` for the capture. A provider's id
   format therefore never reaches another provider's validator.
3. **Opaque state never crosses providers.** `_opaque` is re-sent only to a
   target whose `(kind, provider_id, model)` matches its provenance (section
   3.4). After a switch the fallback sees a plain tool history. A switch
   *back* cannot happen (rule 1).
4. **Thinking is off on a target that inherits tool turns it did not
   write.** An Anthropic fallback with extended thinking would be sent a
   history whose last assistant `tool_use` turn has no thinking block,
   which the Messages API refuses while thinking is enabled. So a target
   whose history holds tool turns written by another `(provider_id, model)`
   is sent with its reasoning control `off` (a new target, built the way
   `with_output_cap` is). The trace notes it.

**`tool_choice="required"` with thinking on.** The Anthropic API refuses
`tool_choice: any` beside extended thinking with a 400 naming `thinking`,
which `_preset_refusal` would read as the user's preset refused, skipping
the fallback. So the loop sends `"auto"` in place of `"required"` to any
target whose reasoning control sends thinking (`llm_sampling.sent_names`
includes `reasoning`), and the trace notes the downgrade.

The preset rule is unchanged: a route-scoped preset follows the route onto
the fallback (`llm.fallback_sampling`). The output cap of section 3.9 is
applied to whichever target is sent.

### 3.12 Decision-as-tool (01g-C5)

A generating model meets a bounded choice that Grimoire did not know to ask
in advance ("does Mara tell the truth, lie, or evade?"). It asks the
Decision role rather than settling it in prose. The Primary keeps the prose.

**The route.** `Route("tool_decision", "Decisions asked by a model", ...,
tasks, True, operation="decide", default_role="decision",
legacy=routing.NO_LEGACY)`. `routing.NO_LEGACY` is the shared sentinel for a
route born at format 2 (`ROADMAP-CHECKLIST.md`, "Shared structures"): 01g,
09, 10 and 02 may each be the first to add it. It tells the planner and the
legacy route list that no format-1 key exists to read. **The task names come
from callers**, such as 02's `turn-tool-decision`. A consumer may instead put
its decide tool on a decide route of its own (12 proposes
`investigation_decide`), and the shim accepts either. The route lands in the
slice of its first consumer, because `test_routing_guard.py` fails a route
whose tasks nothing uses.

**The builder** (`routes/tool_decision.py`). The caller names the task and
resolves it at its own call site, so the literal sits where the guards read
it:

```python
resolved, why, kind = _soft_resolved(
    lambda: require_inference("turn-tool-decision", cid, operation="decide"))

def decision_tool(task: str, resolved: UsableInference | None, client: LLMClient, *,
                  cid: str, scene: str = "", context: str = "",
                  spend_ceiling: bool = False, why: str = ""
                  ) -> tuple[tool_calls.ToolSpec | None, str]:
    """The `decide` tool for one loop, metered under `task`, or (None, why)
    when it cannot be offered: no resolution (the caller's soft refusal,
    never a 409 for the whole run), a resolution of another task, or with
    `spend_ceiling`, one that would answer on a native stage (3.9)."""
```

The handler is async and calls `operations.decide(resolved.task, [item],
client=client, resolved=resolved, campaign=cid, scene=scene,
run_id=ctx.run_id, around=<the run's remaining wall clock>)`. `decide`
itself refuses a resolution of any other task.

**Guard change.** `test_operation_guard.py` needs two changes:

- It recognises a `decision_tool(...)` call as a decide call site. Its first
  argument must be a literal task on a decide route, and it must pass a
  resolution. So "every decide task is decided by a call site" counts the
  caller's literal.
- It accepts the shim's one inner `decide(resolved.task, ...)` call, in
  `routes/tool_decision.py`, where the task is read off the resolution rather
  than spelled.

**The tool's parameters** (inside the 01f-C3 subset):

```json
{"type": "object", "additionalProperties": false,
 "required": ["question", "options", "allow_none", "context"],
 "properties": {
   "question":   {"type": "string"},
   "context":    {"type": "string"},
   "allow_none": {"type": "boolean"},
   "options": {"type": "array", "items": {
       "type": "object", "additionalProperties": false,
       "required": ["id", "description"],
       "properties": {"id": {"type": "string"}, "description": {"type": "string"}}}}}}
```

The handler builds one `decisions.Item`:

- **context**: the builder's bound `context` (what the caller wants every
  decision to see, such as the scene state), then the model's `context`,
  capped at `MAX_DECIDE_CONTEXT = 4000` characters with the model's part
  truncated first;
- **one `Choice`**: `id="choice"`, `instructions=question`, the options, and
  `allow_none`.

`decisions.validate` runs first. A `DecideRequestError` (two options that
normalise alike, an empty question, more than `MAX_TOOL_OPTIONS = 16`
options) is an error result to the model, naming the problem, and counts.
Sixteen is structural: past a dozen options a model is enumerating rather
than choosing, and an unbounded list would let one call carry a chunk's
worth of enum values.

**What the model gets back:**

```json
{"answer": "<option id>" | null,
 "status": "answered" | "abstained" | "refused" | "unanswered",
 "probability": 0.73,                 // only when the backend reported one
 "distribution": {"<id>": 0.73, ...}} // only when the backend reported one (01c)
```

Nothing is fabricated: a structured answer carries no probability, because
none was reported. A failed call (`LLMError`, no item answered) is an error
result, `"the decision could not be made: <kind>"`, and the loop continues;
the turn that asked decides what to do. The answer's rationale is not
returned. The tool exists so the model does not reason the choice itself in
prose.

**Bounds, and no recursion:**

- `max_decisions` caps decide calls per run, and each also counts as a tool
  call.
- Each decide call is a single item, so one chunk. Its meters (one per
  stage reached) file under the run's `run_id`.
- **No recursion by construction.** `decide` takes no tools and cannot reach
  `run_tools`. `run_tools` refuses to start inside a running loop: a
  `contextvars.ContextVar` set for the loop's duration and checked at entry
  raises `ValueError("a tool loop cannot start inside another")`. A tool, the
  decide tool included, therefore cannot start an agent, and a Decision call
  cannot create one, which is the 12 draft's rule.
- **Capture.** The handler passes `decide(capture=)` the caller's decide
  capture if one was given. When 01b-C1 lands, the tool's site uses 01b's
  capture like every other decide site. The trace records
  `kind="decide"` with the answer's `status` and option id. An option id is
  a label the model wrote, not store content, but it is still kept out of
  the logs.

### 3.13 The Claude Agent SDK (01g-C2b)

The SDK cannot be driven the way the HTTP adapters are. It runs its own
loop, executes tools itself, and reports usage per query. C2b fits it to
section 3's contract without letting it own the loop:

- **One `query()` per loop turn**, with:
  - `tools=[]` (no built-ins);
  - `mcp_servers={"grimoire": create_sdk_mcp_server(tools=[...])}`, one
    `@tool` per `ToolSpec` with the same schema;
  - `allowed_tools` naming exactly those MCP tools;
  - a `PreToolUse` hook that answers `"defer"` for each of them.

  The run stops at the model's first tool call and reports it in
  `ResultMessage.deferred_tool_use`. **The SDK never executes a Grimoire
  tool.** The `@tool` handlers exist only to declare schemas, and each
  raises if it is ever reached.
- **The history is re-sent flattened**, as `claude_agent._flatten` already
  does for every call. Assistant tool turns and tool results render into the
  transcript as labelled blocks (`[assistant -> tool <name> <id>]`,
  `[tool <id>]`). There is no session resume: resuming would make the SDK's
  session store the source of the conversation rather than the loop.
- **One call per turn.** `deferred_tool_use` carries one call, so a turn on
  this kind yields at most one call. The loop is unchanged; the model simply
  calls tools one turn at a time.
- **Metering** comes from the turn's `ResultMessage`, as today. It is
  subscription-billed (`cost_basis: equivalent`), so a turn's figure lands
  in `estimated_usd`.
- **Spend ceiling.** The guard's arithmetic needs the user's rates for the
  model, which a `claude` provider's model facts may or may not hold.
  Unpriced means `RunRefused`, as for any kind. The SDK's own
  `max_budget_usd` is not used, because it measures a different column.
- **The output cap** cannot be sent (the SDK takes no sampling). So on this
  kind the projection's `C` is 01i-C1's maximum output where known. Without
  it, a spend ceiling on a `claude` chain is refused as unpriceable: the
  ceiling could not be honest.
- **Version floor.** This needs `tools=`, in-process MCP servers and the
  `"defer"` decision. The plan reads the SDK changelog, sets the `claude`
  extra's floor to the first version with all three, and keeps the import
  survivable as `claude_agent.py:15-39` does (the extra is absent on
  Android).

Until C2b lands, the `claude` preset lists `tools` in `never`, so a
tools-requiring route refuses a Claude subscription primary at the seam with
an honest `incapable` sentence, and such a fallback never rides.

### 3.14 The `tools` capability (01g-C1)

- **Vocabulary.** `tools` joins `capabilities.NAMES`,
  `providers.CAPABILITIES`, `capabilities.CANNOT` (`"call tools"`) and
  `_GERUND` (`"calling tools"`). The frontend reads capability names from the
  API types (`api/types.ts:168-170`), the provider page lists capabilities and the testable ones (`routes/ProvidersView.tsx:31-40`; `tools` joins `TESTABLE` with its probe), and the
  `selection.ts` phrase table), and each gains the entry.
- **Sources** (the resolver's order, unchanged):
  - **adapter**: the `claude` preset's `never` lists `tools` until C2b; no
    other preset does. A test holds the presets' `never` equal to the
    adapter's `calls_tools` (section 3.5).
  - **test**: a `tools` probe (below).
  - **user**: `overrides.tools: yes|no`.
  - **catalog**: an OpenRouter row whose `supported_parameters` names
    `tools` is `yes`. A stated list without it is **`no`**. OpenRouter
    states the parameters each model's endpoints accept, and a model whose
    list omits `tools` is one OpenRouter will not route a tool request to.
    This differs from `structured_outputs`, whose absence says nothing,
    because `response_format` alone can be a JSON mode. Here there is no
    second spelling to hide behind. The plan verifies this against the
    catalog's current documentation and falls back to "absence says
    nothing" if it does not hold. Anthropic's catalog states no tools
    feature, so it contributes nothing.
  - **preset**: `anthropic` lists `tools` in `always`, because every model
    on the Messages API accepts tool definitions. `openrouter`, `openai`,
    `zai`, `zai_coding`, `ollama`, `lmstudio` and `custom` list it in
    `possible`.
  - **name**: none.
- **The probe** (`store/inference/probes.py`). One user message, "Call the
  `ping` tool.", one tool `ping` with an empty-object schema,
  `tool_choice="required"`, and the reply capped at `MAX_TOKENS`.
  - It passes when the provider accepted the request and the reply carried
    a call. A reply with no call is a failed test (`unknown`, with "the
    model answered without calling the tool").
  - It goes through `client.single`, so one attempt, under the model test's
    existing `confirm: true` rule (CLAUDE.md, "A settings surface never
    spends unasked"), and it is priced like the generate probe.
  - `client.single` gains `tools=` and `tool_choice=`.
    `test_routing_guard.py`'s `GENERATION_DOORS` keeps `single` at
    `routes/config.py`.
- **Seam refusal.** A route with `requires=("tools",)` adds `{"tools"}` as
  a need group (`resolve._needs`). A primary known `no` is refused with
  409 `incapable`: "The <route> route runs on <model on provider>, which
  cannot call tools — choose another model or pin this route." A fallback
  known `no` is in `fallback_missing` and does not ride. `unknown` is let
  through, as for every capability. The refusal at the provider (section
  3.5) is what an unknown that cannot actually call tools meets.
- **01s fit.** The Models page already lists `requires` per route and its
  capability warnings (01s, "`requires` and the vision capability
  warning"). A tools-requiring route shows `tools` there with no new
  component. 01g adds no settings.

## 4. Contract

**01g-C1 — a `tools` capability with provenance, and a seam refusal.**
`tools` resolves `yes|no|unknown` with a source through
`capabilities.resolve_caps`, from the adapter (`claude` `never` until C2b),
a passed probe, the user's override, the OpenRouter catalog, and the
`anthropic` preset's `always`. A route declaring `requires=("tools",)`
refuses a primary known `no` with 409 `incapable` before anything is sent,
and never rides a fallback known `no`. `unknown` is never refused.

**01g-C2a — provider-neutral tools on the facade, tool-call parsing, and the
bounded loop, on OpenRouter, OpenAI-compatible and Anthropic.**

- `ToolSpec` / `Toolset` / `ToolCall` / `ToolOutput` / `ToolContext` and
  the two neutral message shapes are as in section 3.2.
- Each HTTP adapter lowers them (section 3.3) and sends nothing new on a
  call without tools.
- Calls are read from the stream through the holder's `Collector`, reset
  per attempt.
- `operations.run_tools` / `stream_tools` run the loop of section 3.7.
- **The caller executes the tools.** The loop hands each call, validated
  against its schema, to the caller's `execute(call, ctx)` (section 3.6),
  never to an adapter or an SDK. Tools are read-only or proposing, never
  writing.
- **The caller supplies the run id** (`run_id`, required and non-empty;
  normally the detached run's `Run.id`). The loop never mints one.
- **The caller gets the final call back.** A call to a `terminal` tool ends
  the loop unexecuted, returned as `LoopResult.final_call` with its
  arguments validated. Without one, the final model turn's text (and, with
  `final_schema`, its conforming record) is returned.
- Terminal status is `completed`, `budget_exhausted` (with `limit`) or
  `failed` (with `error`). The loop raises only for invalid input
  (`ValueError`), `RunRefused`, an `LLMError` on turn 1 (nothing was done,
  so the caller maps it as any failed call), and cancellation.
- Fallback within a loop is sticky after a fallback serves. Call ids are the
  loop's, and opaque provider state never crosses providers.

**01g-C2b — the same contract on the Claude Agent SDK** (section 3.13): one
query per turn, built-ins off, tools declared through an in-process MCP
server, every call deferred to the loop. At most one call per turn. Lands
after C2a. Until then `claude` is `tools: no` (adapter).

**01g-C3 — metering and capture per turn, under one run id.**

- One ledger row per model turn (and per decide-tool call, per stage),
  each with `run_id` and `loop_turn`. Turn rows also carry `tool_calls`.
- Failures are filed at `Meter.done` only.
- Each turn is handed to the caller's `TurnCapture` after it settles,
  guarded.
- `LoopResult.trace` lists every model turn, tool call, decision and stop,
  with refs and sizes and no argument or result text.

**01g-C4 — a run budget, enforced before every send.**

- `RunBudget` sets max turns, tool calls and decisions, a wall clock, a
  spend ceiling, a per-turn output cap (sent on the wire) and a
  total-result-size cap.
- Each is checked before the send or execution it would bound.
- A refused model turn becomes the reserved finalize turn when one fits,
  and otherwise stops the run with `budget_exhausted`.
- Spend is a uniform pre-send projection, `pricing.estimate` at the user's
  rates over counted prompt tokens and the output cap, maximised across the
  chain.
- It is never accounting, never added to a reported figure, and never zero
  for an unpriced model. An unpriced chain under a ceiling is
  `RunRefused("unpriceable")` before any send. This is the recorded
  cross-spec decision in `ROADMAP-CHECKLIST.md`, which 12 follows.
- **It reports which limit stopped the run.** `LoopResult.limit` names it
  (`turns`, `tool_calls`, `decisions`, `wall`, `spend` or `result_chars`),
  and the trace's `stop` entry repeats it.

**01g-C5 — Decision-as-tool.**

- `routes/tool_decision.decision_tool(task, resolved, ...)` returns a
  `decide` tool, or None and a reason.
- **The task name is supplied by the caller**, as a literal resolved at the
  caller's own call site. It is on the `tool-decision` route (operation
  `decide`, Decision role, `routing.NO_LEGACY`) or on a decide route of the
  caller's own.
- Each call asks one `Choice` through `operations.decide`, metered under that
  task with the run's `run_id`, and capped per run by `max_decisions`.
- It returns only what the backend reported (an answer, a status, and a
  probability or distribution where the backend gave one).
- It cannot recurse: `decide` takes no tools, and `run_tools` refuses to
  start inside a running loop.

**01g-C6 — the final loop turn streams, and the loop can decline a tool call
that comes after visible text** (section 3.8).

- Every turn of `stream_tools` is sent through the same streaming facade
  call `generate(stream=True)` makes, heartbeats included.
- `tool_calls.text_deltas` yields the plain text iterator a streaming caller
  already consumes.
- The caller's reasoning buffer rides each turn.
- With `decline_after_text=True`, a tool call ending a turn that already
  yielded visible text is never executed. The text is final, and the call
  is in `LoopResult.declined` and the trace as declined `"after_text"`.

## 5. Interaction with repo rules

- **`test_routing_guard.py`.** `client.stream` and `complete` stay spelled
  only in `inference.py`, where the loop lives. `single` stays at
  `routes/config.py`, for the probe. Every loop names a literal task and a
  routed one, because `run_tools` is recognised like `generate`: the guard's
  recogniser gains `run_tools` and `stream_tools`.
- **`test_operation_guard.py`** gains two rules:
  - a `run_tools` / `stream_tools` call names a literal task on a route whose
    `operation` is `generate` and whose `requires` includes `tools`, and
    passes `resolved=`;
  - every route requiring `tools` is used by such a call, so the safety rule
    holds both ways, as it does for decide.

  The decide tool follows section 3.12's guard change. `decision_tool(...)`
  is a decide call site with a literal task on a decide route and a
  resolution, and its one inner `decide(resolved.task, ...)` is accepted.
- **`test_usage_guard.py`.** The loop's facade calls pass a meter's holder
  opened in `inference.py` (`m.usage`), which the guard already scans there.
  `single(...)` for the probe passes a meter's holder, as the other probes do.
- **New `test_tool_guard.py`** (section 3.6), with the `# tool-ok:` marker,
  capped.
- **Lock domain.** Tools take no lock, so they need no classification. A
  consumer that applies proposals does so in its own route under its lock,
  and is classified there as today.
- **`test_atomic_guard.py`.** Unchanged. Tools do not write.
- **`test_regex_prompt_guard.py`.** It covers tools as package code, and 12's
  transcript-reading tools must use the view (section 3.6).
- **`test_import_guard.py`, `test_wire.py`.**
  - `tool_calls.py` imports only the standard library and `schemas`.
  - `wire.py` gains `with_output_cap` and stays standard-library-only.
  - `adapters`, `openrouter`, `openai_compatible` and `anthropic` import
    `tool_calls` (a leaf). `inference` imports it.
  - The store never imports it.
- **Android / pydantic v1.** No pydantic is involved, and the shapes are
  dataclasses and dicts. C2b's SDK use stays inside the `claude` extra's
  guarded import.
- **Detached runs.**
  - `run_tools` is `async` and is run *inside* a detached run's producer.
    The reserving route stays `def` and reserves through the portal, as
    today.
  - Cancellation is the run's: `CancelledError` unwinds the turn's meter
    (`aborted`), an executing sync tool is abandoned (its thread finishes),
    and `done` is never emitted.
  - 01g adds no run class. 12 chooses `draft` or `background` for its
    investigation, and the exclusion rules follow that class.
- **Privacy.**
  - Tool arguments and results are store content and prose. They reach the
    provider (that is the point), the prompt log through `TurnCapture` under
    the existing capture rules, and the incoming-response capture at Debug,
    as any reply does.
  - They never reach `logs.record`, the trace, or a ledger row.
  - The run-end log line carries ids and counts only.
- **The cost rule.** Rows are filed as any call's, so each turn's price lands
  in the column its `cost_basis` decides. The budget's projection is a
  guard, never accounting (section 3.9).
- **A settings surface never spends unasked.** The `tools` probe is part of
  the confirmed model test, and adds no unconfirmed send.
- **Templates.**
  - `templates/tools/finalize.j2` is new, and `verify_templates.py` covers
    it.
  - Tool descriptions are code (consumers'), not templates.
- **`llm_fakes.py`.** `FakeLLM` gains tool turns, so tests never write an
  inline fake:
  - `FakeToolTurns(*turns)`, where each turn is a text and a list of
    `(name, arguments)`;
  - a cassette entry may answer with calls.

  Provider-level fakes (`ScriptedProvider`, `CassetteProvider`) gain
  tool-call SSE bodies per kind, so the adapters' parsers are exercised
  under the real facade. `test_llm_fakes.py` renders `finalize.j2`.
- **The equivalence baseline and `NO_LEGACY`.** Adding the `tool-decision`
  route changes what `tests/inference_baseline.py` enumerates through
  `routing.TASK_ROUTE`. The plan handles it as the split routes were
  handled. The route is born at format 2 (`legacy=routing.NO_LEGACY`, the
  shared sentinel 09, 10 and 02 also use). Whichever spec lands first adds
  the sentinel and teaches `LEGACY_ROUTES` and the legacy config keys to
  skip such a route.

## 6. Tests and acceptance

**Shapes and lowering (`test_tool_calls.py`):**

- `Toolset` refuses a bad name, a duplicate, more than `MAX_TOOLS`, and a
  parameters schema failing `schemas.check`.
- `conforms` accepts and refuses per the subset.
- Lowering round-trips per kind (section 3.3's table).
- `_strict_messages` keeps tool messages in place.
- `anthropic._messages` builds a `tool_result` user turn, with system text
  after it.

**Stream parsing, per adapter:**

- Recorded-shape SSE bodies in `llm_fakes`:
  - OpenAI-style fragments across chunks, two parallel calls, and a call
    after text;
  - Anthropic `tool_use` with `input_json_delta` fragments and a signed
    thinking block.
- Malformed arguments become `arguments=None`.
- A retried attempt's fragments are discarded (`Collector.begin`).
- A call without tools is byte-identical to the baseline request (the
  capturing provider compares bodies).

**The loop (`test_tool_loop.py`):**

- **The caller's side (01g-C2a):** every tool runs through the caller's
  `execute`, never the adapter's or the loop's own; an empty `run_id` is a
  `ValueError` and every row carries the caller's; a terminal call ends the
  loop unexecuted as `final_call`, and a non-conforming terminal call is an
  error result.
- **01g-C6:** with `decline_after_text`, a call after a visible delta is not
  executed, no further turn is sent, and the call is in `declined`; a call
  before any text runs and the next turn streams; `text_deltas` yields the
  same deltas and heartbeats `generate(stream=True)` would; a caller's
  reasoning buffer receives every turn's thinking.

- **Happy path:** search, then read, then answer. One row per turn, all
  with one `run_id` and increasing `loop_turn`.
- **The finalize turn:** `final_schema` gets a conforming object; a
  non-conforming answer gets one finalize turn; a still-non-conforming reply
  is `failed` with `final_unreadable`.
- **Each budget limit** stops with its `limit`:
  - turns, with the reserved finalize turn run;
  - tool calls, where an unexecuted call still gets an error result;
  - decisions;
  - wall, using a stalling fake and the abandon semantics;
  - result characters.
- **Spend:**
  - the projection uses counted prompt plus the cap, and the maximum over
    the chain;
  - a priced chain stops before the turn that would cross the ceiling;
  - an unpriced chain raises `RunRefused` before any send (a
    `RecordingProvider` saw nothing);
  - a reported `cost_usd` is never read into the guard.
- **Fallback:** a primary fails on turn 2 and the fallback serves; turns
  3+ go to the fallback alone; ids stay the loop's; `_opaque` is not sent to
  the fallback.
- **Failure:**
  - a turn-1 provider failure raises `LLMError`;
  - a turn-3 failure returns `failed` with the earlier rows intact;
  - a tool exception sends a generic error result and logs the type only;
  - a `ToolError` message reaches the model;
  - an unknown tool name and non-conforming arguments are error results;
  - cancellation files `aborted` and abandons a running sync tool.
- **Structure:**
  - a nested `run_tools` raises;
  - `test_tool_guard.py` flags a planted writer and passes a reader;
  - `stream_tools` event order is as in section 3.8, with `interstitial`
    set on tool turns.

**Capability and seam:**

- `resolve_caps` places `tools` from each source.
- The catalog rule: a stated list without `tools` is `no` (subject to the
  plan's verification).
- `claude` is `no` (adapter).
- A route requiring tools refuses a known `no` with the `incapable`
  sentence, and drops a `no` fallback into `fallback_missing`.
- The probe passes on a call, and fails to `unknown` on a reply with no
  call.

**Decision-as-tool:**

- The builder offers nothing on a refused resolution, and nothing on a
  native resolution under a ceiling.
- A call answers through structured `decide` and returns no probability.
- A native answer returns its probability.
- `DecideRequestError` becomes an error result.
- The cap is enforced, and each decision counts as a tool call.
- Rows carry the run's `run_id`.
- `test_operation_guard.py` counts the caller's literal task at
  `decision_tool(...)` and accepts the shim's inner
  `decide(resolved.task, ...)`.
- The task is the caller's: a decision is metered under the task the caller
  named, and a resolution of another task is refused.

**C2b (when it lands):**

- A deferred call becomes a loop call.
- The `@tool` handler is never invoked.
- Built-in tools are off (`tools=[]` in the options).
- A missing SDK still raises `missing_dependency` at the call.

**Acceptance:**

- `make check` is green.
- Every existing generate and decide suite passes unchanged.
- No request without tools differs from the baseline.
- A scripted three-turn investigation over the frozen campaign's store
  (read-only tools written in the test) runs to `completed` under the fake
  with the expected trace. It is never pointed at a real library.

## 7. Non-goals

- Any consumer route other than `tool-decision`, and any toolset (12-C1).
- Write tools, or a tool that holds a lock or starts a run.
- Parallel tool execution. Calls run in order, one at a time. A model's
  parallel calls are honoured as a batch, sequentially.
- Provider-hosted tools, out-of-process MCP servers, and session resume on
  the SDK.
- `strict` function calling on the wire (section 9, question 3).
- Images in tool results. Results are text.
- Retrying a turn after its text reached the caller (the facade's rule).
- Making campaign budgets enforce. They stay warnings.
- Agentic play by default. That is 02-C4, behind its gate.

## 8. Contract changes from the checklist

- **01g-C2 is split.** C2a covers the HTTP adapters, Anthropic included,
  which the checklist did not name. C2b covers the Claude Agent SDK, which
  the checklist named but which cannot be driven the same way (section
  3.13). C2b lands after C2a. Until then a Claude subscription primary is
  refused at the seam for a tools route.
- C1, C3, C4 and C5 match the checklist's headlines. C2a states that the
  caller executes the tools, supplies the run id and gets the final call
  back. C3 adds `loop_turn` and `tool_calls` beside `run_id`. C4 reports the
  limit that stopped the run. C5 takes its task from the caller, and its
  `tool-decision` route uses the shared `routing.NO_LEGACY`.
- **01g-C6 is new** (02-C4 needs it). The final turn streams through the
  generate streaming path, and a call after visible text can be declined.

## 9. Open questions

1. **Settled: `run_id` is the caller's.** It is required, and a loop in a
   detached run passes `Run.id` (01g-C2a). Kept here only so the numbering of
   the other questions does not move.
2. **Sticky fallback, or full chain every turn?** *Recommendation:* sticky,
   as written. It is cheaper on a provider that is down, and one model
   reasons through the whole run. Revisit if 12's evals show a primary
   recovering mid-run matters.
3. **`strict` function calling.** *Recommendation:* off in C2a. Arguments
   are validated locally either way. Turn it on per attempt where the
   catalog says `structured_outputs` (the same flag rule as 01f) only if
   12's evals show malformed arguments costing turns.
4. **Should a loop be refused while its campaign is `over` budget?**
   Campaign budgets only warn today (#153), and the run ceiling is a
   different column. *Recommendation:* not in 01g. 12 decides for
   investigation, and should surface the campaign's level in its start
   dialog rather than refuse silently.
5. **The OpenRouter catalog rule for `tools`** (a stated list without it is
   `no`). *Recommendation:* adopt it if the plan confirms OpenRouter's
   parameter list is per-model and complete. Otherwise treat absence as
   saying nothing, as `structured_outputs` does, and rely on the probe and
   the provider's refusal.
6. **`claude_agent.py` passes `allowed_tools=[]`, not `tools=[]`.** As the
   SDK documents it, built-in tools therefore stay in the model's context on
   every Claude subscription call today, though `max_turns=1` keeps any
   result out of a reply. *Recommendation:* fix it independently of 01g. Set
   `tools=[]` where the installed SDK supports it, guarded for older
   versions. It shortens every prompt on that path and removes a model
   behaviour nobody wants. It is listed here because 01g's reading found it.
   It is not a 01g contract.
7. **Settled: embed calls inside tools.** 01h-C5 now carries an optional
   `run_id` on `embed_sync` / `embed_groups_sync`, filed in 01g-C3's
   `run_id` ledger field (section 3.10). A tool passes `ToolContext.run_id`.
   Until 01h-C5 lands, such rows are attributed to the campaign and scene
   but not the run.
8. **Interstitial text in play (02-C4).** *Settled by 02:* play passes
   `decline_after_text=True` (01g-C6), so a contribution never has
   interstitial text. Other callers still see `turn_end.interstitial`.
