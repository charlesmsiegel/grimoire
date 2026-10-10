# 01g-S2: Neutral tool shapes, wire lowering and stream parsing on the HTTP adapters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Review gates: not run (speed mode); the Codex gates are owed.**

**Goal:** everything a loop needs below `inference.py` to send a tool history
and read a turn's calls back, on OpenRouter, OpenAI-compatible and the
Anthropic Messages API, with nothing a loop itself does. Delivers 01g-C2a
(part): the `tool_calls.py` shapes and `Toolset` validation, `conforms`,
argument accumulation and opaque provider state in the `Collector`, the two
neutral message shapes lowered per kind, `tools=` / `tool_choice=` on the
facade's `stream` / `complete`, `tools_refused`, and `note_prompt` counting
tool definitions. Nothing calls tools yet.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.2-3.5, §4 (C2a), §6 ("Shapes and lowering", "Stream parsing"), Slices
(01g-S2). Builds on 01g-S1 (`deec0d9`), which took the definition check,
definition / `tool_choice` lowering, call detection, `_stamp`'s collector and
the keywords from `single` down (its "Drift from the spec's slice graph").

## Design

- **`tool_calls.py` grows in place** (stdlib + `schemas`):
  - `ToolSpec`, `ToolOutput`, `ToolContext`, `ToolError`, `ToolCall`,
    `Toolset` and the `Execute` alias, as spec 3.2. `Toolset` validates at
    construction (well-formed unique names, at most `MAX_TOOLS`, a string
    description of at most `MAX_DESCRIPTION`, parameters through
    `schemas.check`) and answers `definitions()`, the neutral tuple `check`
    and the adapters take. `MAX_TOOLS`, `MAX_DESCRIPTION`,
    `MAX_RESULT_CHARS`, `TOOL_TIMEOUT_S` as the spec states them.
  - `violation(value, schema) -> str` ("" when it conforms) and
    `conforms(value, schema) -> bool`, structural over the 01f-C3 subset:
    types (`integer` refuses a bool and a float), `anyOf`, `enum`,
    required keys, no extra keys, `items`.
  - **The `Collector` accumulates.** One record per call (provider id,
    name, argument fragments, Anthropic's start `input`); OpenAI fragments
    keyed by `index`, a new id at a used index starts a new call, and — the
    S1 handoff — an id arriving at an index whose open call has none yet is
    adopted onto that call rather than opening a second. Anthropic blocks
    keyed by block `index`: `tool_use` + `input_json_delta`, and `thinking`
    (+ `thinking_delta`, `signature_delta`) / `redacted_thinking` kept as
    opaque state. OpenAI-style `delta.reasoning_details` entries are kept
    as opaque state, merged by their `index` (string fields concatenated).
    `calls()` answers `ToolCall`s in order: `id` and `provider_id` are the
    provider's id ("" when it gave none — the loop names those, S4),
    `arguments` the parsed JSON object or None, `raw_arguments` as
    received; an empty argument string is `{}` (a no-argument call on
    both wires). `opaque()` answers `{"kind", "provider_id", "model",
    "items"}` or None; its provenance comes from `begin(kind=,
    provider_id=, model=)`, which `llm._stamp` now passes from the
    attempt's target.
  - The feeders **return the text they consumed** (name and argument
    fragments), with or without a collector, and the adapters hand it to
    `llm_usage.note_reply`: arguments are billed completion (S1 handoff 1).
  - **Lowering.** `provenanced(messages, kind, provider_id, model)` drops an
    `_opaque` whose provenance is not the target's (the adapters call it
    with the attempt's target); `openai_messages(messages, *, reasoning)`
    lowers the neutral assistant tool turn (`function` calls with
    `json.dumps` arguments; `reasoning_details` from `_opaque` only when
    `reasoning` — OpenRouter) and the `tool` message (`ERROR: ` prefixed
    when `is_error`); `anthropic_*` block helpers for the Anthropic side.
    Every lowering returns the **same list object** when no message carries
    a tool turn or `_opaque`, so a call without tools is byte-identical.
- **Adapters / clients.** OpenRouter `_payload` and OpenAI-compatible
  `stream` lower through `openai_messages`; `_strict_messages` passes an
  assistant `tool_calls` message unmerged (a plain assistant turn just
  before it is carried into it, so alternation holds) and each `tool`
  message in place. `anthropic._messages` lowers an assistant tool turn to
  opaque thinking blocks, then text, then `tool_use` blocks, and a `tool`
  message to a `tool_result` block placed after the results already leading
  the last user turn (pending system text therefore lands after them).
  The Anthropic reader feeds `content_block_start`/`_delta` to the
  collector (S1) and notes argument text.
- **Facade.** `stream` / `complete` gain keyword-only `tools=` /
  `tool_choice=`, checked like `single`'s, carried through `_streamed` →
  `_dispatch`; `note_prompt(usage, messages, tools)` counts the definitions
  and assistant `tool_calls` (name + arguments) on the prompt side.
- **`tools_refused`.** `ToolsRefusalError(LLMError)` (kind `bad_response`,
  code `tools_refused`). `_tools_refusal(exc, offered)` reads a 400/422
  (not an account limit) whose message names `tools`, `tool_choice` / "tool
  choice" or `functions` as whole words, on an attempt that was offered
  tools, or any error an adapter raised with code `tools_refused` (the
  Claude adapter's refusal, S1 handoff 2). In `_resilient` it is read after
  the preset refusal and before the schema one; it is **not observed**, not
  retried, and the next route is tried. `routes_failed` treats it as it
  treats a schema refusal (the other route's word gives the kind), and a
  call whose every route refused tools raises `ToolsRefusalError`.
- **`llm_fakes`.** `openai_tool_sse(...)` / `anthropic_tool_sse(...)` build
  recorded-shape bodies, and `SSEProvider` serves them (or a `(status,
  text)` error, or a body cut mid-stream) to a REAL client over
  `httpx.MockTransport`, recording each request body — so the parsers run
  under the real facade. `FakeToolTurns` stays S4's.

## Decisions on open questions

- Q3 (`strict` function calling): off, as recommended — nothing sends it.
- `_tools_refusal` takes `offered: bool` rather than the target: whether
  tools were sent is not on a `wire.Target`. Recorded as a spelling drift.
- An empty argument string reads as `{}` on both kinds.

## Files

- Modify: `backend/src/grimoire/tool_calls.py`, `llm.py`, `llm_usage.py`,
  `adapters.py`, `openrouter.py`, `openai_compatible.py`, `anthropic.py`,
  `backend/tests/llm_fakes.py`.
- Tests: `test_tool_calls.py` (shapes, `Toolset`, `conforms`, collector,
  lowering round-trips, strict and Anthropic folding), `test_tool_wire.py`
  (new: per-adapter parsing under the real facade through `SSEProvider`, a
  retried attempt's fragments discarded, `tools_refused` composition and
  non-observation, a call without tools byte-identical, the local estimate
  counting definitions and arguments). Two existing tests that used `tool`
  as their example of an unknown role now use `function`.

## Verification

Targeted: the tests above plus the existing adapter, facade and golden
suites (`test_llm*.py`, `test_openrouter.py`, `test_openai_compatible.py`,
`test_anthropic.py`, `test_adapter_*.py`, `test_decide_chain_golden.py`,
`test_model_test_call.py`, `test_tool_calls.py`), then the speed-mode guard
list with `-n 3`, then `make check-lint check-mypy`.
