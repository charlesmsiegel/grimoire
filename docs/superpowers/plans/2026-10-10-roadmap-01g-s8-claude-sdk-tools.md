# 01g-S8: The Claude Agent SDK adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: not run (speed mode); the Codex gates are owed.**

**Goal:** a Claude subscription can serve a tool loop under the same
contract as the HTTP adapters. The SDK never runs the loop and never
executes a tool. Delivers 01g-C2b (full).

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.13, §4 (C2b), §6 (the "C2b" tests and the "Guard and SDK" gate
additions), Slices (01g-S8).

## Design

### One query per loop turn, isolated from the host

`claude_agent._tool_options` builds the turn's options:
- `setting_sources=[]`, `strict_mcp_config=True` and `tools=[]`;
- one in-process MCP server, `grimoire`, declaring each tool through
  `@tool`, with handlers that raise if they are ever reached;
- `allowed_tools` set to exactly those `mcp__grimoire__<name>` tools;
- a `PreToolUse` hook on `"*"`, which answers `defer` for a Grimoire tool
  and `deny` for anything else;
- `max_turns=2`, so that what stops the run is the deferral, not the turn
  limit.

`tool_choice` is handled as follows:
- `"none"`: the hook denies every call, and `allowed_tools` is empty;
- `"required"`: the SDK has no way to ask for it, so it is offered as
  `auto`.

### Reading the turn

- The `ResultMessage`'s `deferred_tool_use` is noted on the holder's
  `Collector`, with the `mcp__grimoire__` prefix stripped. An unknown
  remainder is left for the loop to answer as an unknown tool.
- Its text is noted for the local estimate.
- The turn ends `tool_calls` when a call was deferred, and `stop`
  otherwise.
- A turn on this kind therefore makes at most one call. Any other
  `tool_use` blocks are not answered, because they are not in the re-sent
  history.

### History

- The history is re-sent flattened, as `_flatten` already does.
- An assistant turn's calls become `[assistant -> tool <name> <id>]`
  blocks, and each result becomes a `[tool <id>]` block.
- There is no session resume.

### Names

- `MAX_TOOL_NAME = 64 - len("mcp__grimoire__")` = 49.
- The client refuses a longer name, coded `tools_refused`.
- `run_tools` refuses one at entry when any attempt on the chain is a
  Claude subscription.

### Capability

- The `claude` preset moves `tools` from `never` to `always`, and the
  adapter's `calls_tools` becomes True, still held equal to the preset.
- The adapter hands an offer of tools to the client. S1's coded refusal in
  the adapter is deleted.
- An SDK too old to declare tools is refused with the code
  `tools_refused`. That is not observed as a health failure. The new SDK
  names are imported in a guarded block of their own, so an older SDK
  still serves every call that offers no tools.

### Version floor

The SDK changelog (CHANGELOG.md in `anthropics/claude-agent-sdk-python`)
dates each feature to a release:
- the `tools` option: before 0.1.60;
- `setting_sources=[]` passed as empty: fixed in 0.1.60;
- the `"defer"` decision, `DeferredToolUse` and `strict_mcp_config`:
  0.1.76 (corrected by the brutal-review round: 0.1.74 and 0.1.75 were
  never published, and the 0.1.73 wheel has none of the three).

The `claude` extra's floor is therefore `claude-agent-sdk>=0.1.76`, and the
adapter gates on the features themselves (`claude_agent._declares_tools`),
not the version.

### Metering and spend

- Metering is unchanged: the turn's `ResultMessage`, billed as
  `cost_basis: equivalent`.
- Under a spend ceiling a `claude` attempt has no catalog and its preset
  `reports_price`, so it is unpriceable, and a ceiling over a Claude chain
  is refused (01g-S5's rule, unchanged).

## Not done here

Spec question 6 is not addressed: a call that offers no tools still sends
only `allowed_tools=[]`, without `tools=[]` or `setting_sources=[]`. The
spec leaves that fix to the coordinator, independently of 01g, and calls
without tools are unchanged here.

## Tests

**In `test_claude_agent.py`:**
- the isolation options;
- a deferred call becomes a loop call;
- the hook defers or denies;
- a handler is never run;
- `tool_choice` "none";
- a 50-character name is refused;
- a missing SDK still raises `missing_dependency`;
- the flattened history;
- the options without tools are unchanged.

**Updated** (Claude now calls tools): the S1 capability, seam, registry,
read-API and model-test cases. `test_tool_wire.py` now covers the
too-old SDK. `test_tool_loop.py` covers the 49-character name check.
