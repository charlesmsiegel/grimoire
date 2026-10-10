# 01g-S1: The `tools` capability and its seam refusal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tools` becomes the eighth capability. It resolves `yes|no|unknown`
with a source through `capabilities.resolve_caps` — the adapter (`claude`
lists it in `never` until S8), a passed or failed test, the user's override,
the OpenRouter catalog (a stated parameter list with or without `tools`), and
the `anthropic` preset's `always` — and a route that declares
`requires=("tools",)` is refused at the seam with 409 `incapable` on a primary
known `no`, with a known-`no` fallback in `fallback_missing` and never sent.
A confirmed model test can probe it: one user message, one `ping` tool,
`tool_choice="required"`, through `client.single`, passing when the reply
carried a call and failing to `unknown` when it did not. The frontend's
capability vocabulary, its test list, its override form and its phrase table
gain the entry. Delivers 01g-C1 (full).

**Architecture:** the vocabulary is data (`providers.CAPABILITIES`,
`capabilities.NAMES`, `NEEDS`, `CANNOT`, `_GERUND`, `_ADAPTER_SAYS`), so the
resolver, the picker groups (`grouped`), the facts write path
(`facts._check_overrides`, `record_verified`) and the seam (`resolve._needs`,
`_missing`, `incapable`) take `tools` without new logic. Two rules are new
code: the catalog rule in `capabilities._listed` (OpenRouter rows only), and
the adapter flag `calls_tools`, held equal to the presets' `never`. The probe
is the one part that needs the wire, and S1 takes from S2 exactly what a
probe needs and nothing a loop needs: a new gateway leaf
`grimoire/tool_calls.py` (the neutral tool-definition check, the per-kind
definition and `tool_choice` lowering, and a call-detecting `Collector` in
the usage holder under `tool_calls.KEY`, preserved and reset by `llm._stamp`
beside the reasoning buffer); `tools=` / `tool_choice=` keyword-only on
`LLMClient.single`, `_dispatch`, `_generate`, `_parts_lowered`, each
adapter's `generate` and each HTTP client's `stream`, sent **only when
given**; and stream reading that notes each call begun (OpenAI-style
`delta.tool_calls` fragments, Anthropic `tool_use` blocks) and the end reason.
`stream`/`complete` gain nothing (S2), and no message shape for a tool turn
exists yet (S2).

**Tech Stack:** Python 3.11, pytest, httpx `MockTransport`, the shared fakes
(`FakeLLM`, `FakeProvider` in `test_llm.py`), React + vitest, TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01g-tool-calling-design.md`
§3.14 (the capability), §3.5 (the adapter flag), §3.3/§3.4 (the lowering and
`Collector` shapes S1 starts), §4 (C1), §5 (`test_routing_guard`,
`test_usage_guard`, "a settings surface never spends unasked"), §6
("Capability and seam"), Slices (01g-S1), §9 question 5.

## Open question 5, decided: adopt the OpenRouter catalog rule

The spec adopts "a stated `supported_parameters` list without `tools` is
`no`" only if OpenRouter's list is per-model and complete; otherwise absence
says nothing. Adopted, on this evidence:

- **OpenRouter's own description.** Its models guide describes
  `supported_parameters` per model, as the parameters that work with each
  model, and its tool-calling guide points readers at the models filtered by
  `supported_parameters=tools` to find the ones that call tools.
- **The catalog stores it per model row.** `catalog.entry` keeps
  `supported_parameters` as `params` on each row (`catalog.py:43-49`), and only
  when it is a list: an absent list is "unknown", an empty one "takes none
  of them" (its own comment). Only OpenRouter's rows carry it.
- **The repo evidence is softer than "complete".** The sampler split reads
  the same list for `openrouter` connections only (`resolve.model_params`),
  and `llm_sampling._sampler` answers a parameter absent from it
  `UNSUPPORTED` (source `catalog`) and does not send it. But that is an
  *omission*, and `model_params`' docstring says why it is safe: OpenRouter
  forwards a parameter a model does not take for the model to ignore. Here
  absence becomes a *refusal* at the seam, which is a stronger use of the
  same field, and the catalog rule stands on OpenRouter's description above
  more than on the sampler's precedent.
- `structured_outputs` stays the exception and for its stated reason: its
  absence says nothing because `response_format` alone can be JSON mode
  (`capabilities._STRUCTURED_PARAMS`). `tools` has no second spelling to
  hide behind.

Scope: the `no` half applies to a row resolved under an **`openrouter`-kind**
preset only; a `yes` is read from any row whose stated list names `tools`, so
a custom OpenAI-compatible server echoing an OpenRouter-shaped row can say
`yes` but never refuse. A catalog `no` is lifted by a passed test or the
user's `yes` (both rank above the catalog) and never by a failed test
(`_unknown_over_no`). **A stale cached catalog keeps its `no` until the
catalog is refreshed** (the sidecar is only rewritten by a refresh), and the
remedies stated in the refusal (another model, a pin) and the facts panel
(a `yes` override, or a test) all work without one. Revisit if real use shows
OpenRouter routing a tool request to a model whose list omits `tools`.

No other open question bears on S1 (Q3 `strict` is S2's wire; Q6 is not a
01g contract). A new one is raised for the spec under "Open for the spec"
below.

## Drift from the spec's slice graph

The spec lists S1, S2 and S3 as independent. S1's own scope says
`client.single` gains `tools=`/`tool_choice=` and the probe "passes on a
call", which cannot happen without the wire, so S1 lands the part of S2 the
probe needs. **S2 now builds on S1** (the coordinator sequences S2 after
S1); the spec is not edited. S1 delivers, and S2's scope therefore drops:

- the new leaf `grimoire/tool_calls.py`: `KEY`, `CHOICES`, `check` (the
  neutral definition shape, parameters through `schemas.check`), the
  per-kind definition and `tool_choice` lowering, and a `Collector` that
  detects calls (id, name, end reason) with its feeders
  (`from_openai_chunk`, `feed_anthropic` taking the whole SSE **frame** so
  S2 has the block `index` to route `input_json_delta` by, `anthropic_stop`);
- `llm._stamp` keeping and resetting a `Collector`, beside the reasoning
  buffer (a small, commented block: 01g-S3, in flight, also edits `_stamp`
  for `RUN_KEY`, and the coordinator merges the two);
- `tools=`/`tool_choice=` keyword-only on `LLMClient.single`, `_dispatch`,
  `_generate`, `_parts_lowered`, each adapter's `generate` (with
  `calls_tools`) and each HTTP client's `stream`, sent only when given.

S2 keeps: `ToolSpec`/`Toolset`/`conforms`, argument accumulation and
`Collector.calls()`, opaque state, message lowering for tool turns,
`stream`/`complete` keywords, `tools_refused`, `note_prompt` counting
definitions, and the tool SSE bodies in `llm_fakes`.

## Open for the spec

- **The 64-token cap makes the `tools` probe inconclusive on a reasoning
  model.** The spec fixes the probe at `MAX_TOKENS` (§3.14). A model that
  reasons before it calls can reach that cap first; the probe then reports
  `capped` and files nothing, so the capability stays where the catalog or
  preset put it. S1 states this in the preview's description and adds no
  per-probe cap; whether the `tools` probe deserves a larger cap is for the
  spec to decide.
- **Spec §3.11's `required` downgrade misses implicit thinking.** It tells
  S4's loop to send `auto` only where `llm_sampling.sent_names` includes
  `reasoning`, but an adaptive Anthropic model thinks when `thinking` is
  unset (this slice's B1), so nothing is "sent" and `required` would go out
  beside thinking and be refused. S4 should reuse `probes.tool_choice`'s
  rule, moved out of `store/` into a gateway leaf (`tool_calls` is the
  natural home) so the loop can read it.

## Handoffs to 01g-S2

- **Counting a call-only reply.** On a provider that reports no usage, a
  reply that only calls a tool is estimated at 0 completion tokens (the
  facade notes prose only), and `llm_usage.note_prompt` leaves the tool
  definitions out of the prompt count. S2's `note_prompt` and argument
  accumulation own both.
- **The Claude adapter's refusal is a plain `bad_response`**, which
  `_resilient` would observe as a health failure. Unreachable in S1 (the
  probe's `_test_plan` refuses `tools` on a Claude subscription before any
  call); S2's `_tools_refusal`, which is not observed, should cover it.
- **The `Collector` can split one call in two** when an id-less fragment at
  index i is followed by an id-carrying one at the same index (the first is
  keyed `#<i>`, the second by its id). Harmless for S1's "did it call", wrong
  for S2's argument accumulation, which should adopt the id onto the open
  `#<i>` call instead.

## Global Constraints

- **A request without tools is byte-for-byte what it was.** Every layer
  passes `tools`/`tool_choice` on **only when given** (the slice-F
  discipline, `adapters.py`'s "each keyword only when there is something to
  send"). `test_adapter_wire_golden.py`, `test_adapter_registry.py`'s
  `test_a_chain_sends_what_its_adapter_sends`, `test_decide_chain_golden.py`
  and `test_openai_compatible.py::test_no_tools_field_in_payload` pass
  untouched.
- **No route requires `tools`** and none is added: `test_routing_guard.py`
  fails a route whose tasks nothing uses, and no consumer exists (spec,
  "Routes"). The seam refusal is proven on a route planted in the test.
- **`unknown` is never refused; a failed test is `unknown`, never `no`.**
  The probe that answers without a call files a failed verdict (`unknown`,
  source `test`, with its sentence), and a reply cut by the cap before any
  call files nothing.
- **The probe spends only behind the existing confirm.** It is a fourth
  chat probe under `post_connection_test`'s `confirm: true` and
  `_test_plan`'s refusals; `_test_plan` already refuses a capability the
  preset's `never` lists, so a Claude subscription is refused before
  sending.
- `client.single` stays spelled only at `routes/config.py`
  (`test_routing_guard.GENERATION_DOORS`), with the meter's holder
  (`test_usage_guard._GENERATORS`). `FakeLLM.single` keeps the facade's
  parameter names (`test_llm_structured.py::test_fakes_record_the_schema`
  compares the signatures).
- `tool_calls.py` imports only the standard library. The store does not
  import it in S1 (`probes.py` spells its tool as a plain dict).
- No pydantic, no new store file, no new marker, no template.
- Placeholder names only (Saltmarch, Mara, Winifred, Realm).
- Ratchets: no new finding. `capabilities._listed` would cross ruff's C901
  (max 10) with the catalog rule inline, so the parameter half moves to a
  helper; `routes/config._probe` gets its tools verdict from a helper.

## Review Focus

- **What S1 takes from S2, and that it is the minimum.** The probe cannot
  pass "on a call" without sending a tool and seeing a call, so S1 lands:
  definition and `tool_choice` lowering, call *detection* (id, name, end
  reason — no argument accumulation), `_stamp` preservation, and the keyword
  on `single` and below. S2 keeps: `ToolSpec`/`Toolset`/`conforms`,
  argument accumulation and `Collector.calls()`, opaque state, message
  lowering for tool turns, `stream`/`complete` keywords, `tools_refused`,
  `note_prompt` counting tool definitions, and the tool SSE bodies in
  `llm_fakes`. S1's `Collector` is shaped so S2 grows it in place.
- **The catalog rule's scope** (above): `no` only under an `openrouter`
  preset; nothing else in `_listed` changes.
- **Anthropic `tool_choice: any` beside implicit thinking.** The Messages
  API refuses forced tool use while thinking is on, and an adaptive model
  thinks when `thinking` is unset (`llm_sampling._thinking_off`'s
  docstring); the probe's target sends no reasoning control, so no
  `thinking` field goes out at all. The downgrade is decided **in the
  probe, from the target** (`probes.tool_choice`): `required` only where the
  body explicitly sends `thinking: {type: disabled}`, or where the catalog's
  `model_features` say the model does not think when nothing is sent
  (`adaptive_thinking` False); otherwise `auto`, and the preview's sentence
  says "asking for" rather than "requiring" a call. The adapter lowers what
  it is handed and never rewrites `required` (the loop's downgrade, with its
  trace note, is S4's, spec §3.11). Without this the probe would 400 on the
  one preset that states `tools` `always`, and the filed failure would
  outrank that `yes`.
- **z.ai takes `tool_choice: auto` only** (its own documentation), so the
  probe asks `auto` on the `zai` and `zai_coding` presets; the prompt asks
  for the call either way.
- **A Claude model behind OpenRouter** (an `anthropic/` id) may think as it
  does at Anthropic, and no catalog feature row says otherwise there, so
  the probe asks `auto` for it as well.
- **`anthropic` `always` reaches every anthropic-kind connection**,
  third-party base URLs included: `providers.infer` maps every `anthropic`
  kind to that one preset. A Messages-compatible server that cannot call
  tools is met by its own refusal (S2's `tools_refused`) or a user `no`.
- **A tools verdict an older build drops.** `facts.read`/`set_stated`
  filter verdicts and overrides to the reading build's `CAPABILITIES`, so an
  older build that rewrites a model's facts file drops a `tools` override or
  verdict. The capability then reads from the catalog or as `unknown`. A
  lost user `no` is met by the provider's refusal (S2); a lost user `yes`
  or passed verdict that had lifted an OpenRouter catalog `no` brings that
  refusal back. Accepted, with no format bump: testing or overriding again
  restores it.
- `NEEDS` gains `tools` so a tools-requiring route's pin picker can ask
  `GET .../capabilities?need=tools` (the frontend treats a route's
  `requires` as needs, `selection.routePinNeeds`); `OPERATION_CAPABILITY`
  reads only the three operations and does not move.

---

### Task 1: the vocabulary, the presets and the adapter flag

**Files:**
- Modify: `backend/src/grimoire/store/inference/providers.py`
  (`CAPABILITIES` + `"tools"`; `openrouter` and `openai` `possible` +
  `tools`; `anthropic` `always=_GEN | {"tools"}`; `claude` `never` +
  `tools` with a comment "until 01g-S8"; `_ZAI_POSSIBLE` + `tools`; the
  local presets take it through `_EVERYTHING_BUT_DECISION`)
- Modify: `backend/src/grimoire/store/inference/capabilities.py`
  (`NAMES` gains `"tools"` **last**, so every existing order —
  `resolve._missing`, `probes.ordered` — is unchanged for the seven;
  `NEEDS["tools"] = ("tools",)`; `CANNOT["tools"] = "call tools"`;
  `_GERUND["tools"] = "calling tools"`; `_ADAPTER_SAYS["tools"] =
  "calls no tools"`; module docstring's source list names `tools` under
  `preset` and `adapter`)
- Modify: `backend/src/grimoire/adapters.py` (`calls_tools` on the
  `Adapter` protocol and each class — True for `openrouter`,
  `openai_compatible`, `anthropic`, False for `claude` until S8; module
  docstring's flag list; `calls_tools(kind) -> bool` beside
  `decides_natively(kind)`)
- Modify: `backend/src/grimoire/routes/config.py`
  (`get_connection_capabilities`' `need` Literal gains `"tools"`)
- Test: `backend/tests/test_inference_providers.py`,
  `backend/tests/test_inference_capabilities.py`,
  `backend/tests/test_adapter_registry.py`,
  `backend/tests/test_inference_read_api.py`,
  `backend/tests/test_provider_api.py`

- [ ] **Step 1: Failing tests**
  - `test_inference_providers.py`: `CAPS` gains `"tools"`;
    `test_capability_sets_are_exact` states the new sets (openrouter and
    openai `possible` with `tools`; anthropic `always` `{generate, stream,
    tools}`; claude `never` with `tools`; zai/zai_coding `possible` with
    `tools`; ollama/lmstudio/custom `possible` = `ALL_BUT_DECIDE`, which
    now includes it). `test_capability_sets_partition_the_vocabulary`
    passes as written.
  - `test_inference_capabilities.py`:
    - `test_tools_is_no_from_the_adapter_on_a_claude_subscription`:
      `_resolve("claude")["tools"] == Cap("no", "adapter")`, and a passed
      test or a user `yes` does not move it.
    - `test_tools_is_yes_from_the_anthropic_preset`:
      `_resolve("anthropic")["tools"] == Cap("yes", "preset")`; a user `no`
      beats it (`Cap("no", "user")`).
    - `test_tools_is_unknown_where_the_preset_leaves_it_possible`: each of
      `openrouter`, `openai`, `zai`, `ollama`, `custom` with no row →
      `Cap("unknown", "unknown")`.
    - `test_a_tools_test_and_override_are_read`: `verified={"tools": {"ok":
      True}}` → `Cap("yes", "test")`; `{"ok": False, "error": "x"}` →
      `Cap("unknown", "test", "x")`; `overrides={"tools": "no"}` →
      `Cap("no", "user")`.
    - `test_fits_and_groups_for_tools`: `fits(..., "tools")` per value;
      `group_for(_resolve("claude"), "tools", P["claude"]) == ("hidden",
      "Claude subscription calls no tools")`; a user `no` hides with "you
      marked this model as not calling tools".
    - `test_fits_refuses_an_unknown_need` asks `"telepathy"` (it used
      `"tools"`, which is now a need).
  - `test_adapter_registry.py`:
    - `test_adapter_facts_agree_with_the_registry`: `flags` gains
      `"tools": "calls_tools"`.
    - `test_calls_tools_is_exactly_the_presets_never` (new, spec §3.5): for
      every preset, `("tools" in preset.never) is (not
      registry[preset.kind].calls_tools)`; `adapters.calls_tools(k)` per
      kind and False for `"x"`.
  - `test_inference_read_api.py`:
    `test_a_claude_provider_rules_tools_out_in_one_sentence` (`need=tools`
    on a claude connection: both groups and `hidden` empty, `reason ==
    "Claude subscription calls no tools"`), and `need=tools` is a 200 on
    an OpenRouter connection (it was a 422 Literal refusal).
  - `test_provider_api.py::test_facts_round_trip_and_validate`: a PUT with
    `overrides: {"tools": "no"}` round-trips, and
    `got["capabilities"]["tools"] == {"value": "no", "source": "user"}`.
- [ ] **Step 2: Run** →  fail.
  `cd /home/user/wt/01g-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_inference_providers.py tests/test_inference_capabilities.py tests/test_adapter_registry.py tests/test_inference_read_api.py tests/test_provider_api.py`
- [ ] **Step 3: Implement.** **Step 4: Run** → pass.

### Task 2: the OpenRouter catalog rule

**Files:**
- Modify: `backend/src/grimoire/store/inference/capabilities.py`
  (`_listed(row, kind)`; the parameter half moves to `_params_caps(params,
  kind) -> dict[str, Cap]`: `structured_output` `yes` as today, and
  `tools` `yes` when the list names it, `no` when it does not **and** `kind
  == "openrouter"`; `resolve_caps` passes `preset.kind`; the docstring's
  step 3 states the rule and why the `no` is OpenRouter's alone)
- Test: `backend/tests/test_inference_capabilities.py`,
  `backend/tests/test_inference_read_api.py`

- [ ] **Step 1: Failing tests**
  - `test_an_openrouter_list_says_tools_both_ways`:
    `_resolve("openrouter", row={"id": "m", "params": ["tools",
    "temperature"]})["tools"] == Cap("yes", "catalog")`; `params:
    ["temperature"]` and `params: []` → `Cap("no", "catalog")`; no
    `params` key → `Cap("unknown", "unknown")`.
  - `test_only_openrouter_lists_say_no_to_tools`: the same rows under
    `custom` and `openai` give `yes` for a list naming `tools` and
    `unknown` for one that does not.
  - `test_a_failed_tools_test_never_lifts_the_catalogs_no` and
    `test_a_passed_tools_test_or_the_users_yes_lifts_it` (the existing
    `_unknown_over_no` ladder, for `tools`).
  - `test_the_catalog_says_nothing_about_stream_or_prefill` and
    `test_a_malformed_row_contributes_nothing` pass unchanged
    (`params: "structured_outputs"` is not a list).
  - `test_inference_read_api.py::test_tools_groups_by_what_the_openrouter_catalog_states`:
    two rows, one listing `tools`; `need=tools` puts it in `fits` (reason
    `catalog`) and the other in `hidden` with "the catalog says this model
    does not call tools".
- [ ] **Step 2: Run** → fail. **Step 3: Implement.** **Step 4: Run** the
  Task 1 command plus `tests/test_inference_resolve.py
  tests/test_inference_controls.py tests/test_inference_decide.py` → pass
  (fixtures whose OpenRouter rows list `params` without `tools` now read
  `tools: no`; no route requires it, so no `missing` moves).

### Task 3: the seam refusal, on a planted route

No production code: `resolve._needs` already turns each `requires` entry
into a need group and `incapable` names the first missing capability with
`CANNOT`. These tests prove it for `tools`.

**Files:**
- Test: `backend/tests/test_inference_resolve.py`

- [ ] **Step 1: Tests** (a `planted` fixture: `PLANTED =
  routing.Route("planted_tools", "Planted tool runs", "",
  ("planted-tools",), True, requires=("tools",))`, installed with
  `monkeypatch.setitem` on `routing.TASK_ROUTE` and `routing._BY_KEY`;
  `routing.route` and `routing.label_for` read both at call time):
  - `test_a_tools_route_refuses_a_primary_the_catalog_says_cannot_call_tools`:
    state `fresh`, `_catalog("openrouter", [{"id": "vendor/active",
    "params": ["temperature"]}])` → `resolve("planted-tools").missing ==
    ("tools",)`, and `require_inference("planted-tools")` raises 409
    `{"kind": "incapable", "detail": "The Planted tool runs route runs on
    the Primary role (vendor/active on OpenRouter), which cannot call tools
    — choose another Primary model or pin this route."}` (the exact
    `_primary_phrases` wording, checked against the resolved primary at
    implementation).
  - `test_a_tools_route_runs_on_an_unknown_primary`: no catalog row →
    `missing == ()`; `require_inference` returns the chain.
  - `test_a_tools_route_refuses_a_user_no` (`facts.set_overrides(...,
    {"tools": "no"})`) and `test_a_tools_route_refuses_a_claude_subscription`
    (state `claude_active`: `Cap("no", "adapter")`, `incapable` naming
    "cannot call tools"; `chat` on the same connection still resolves).
  - `test_a_tools_fallback_known_unable_is_dropped`: state `routed`,
    primary row lists `tools`, `spare`'s does not → `missing == ()`,
    `fallback_missing == ("tools",)`, `build_llm()._routes(chain)` sends
    only `openrouter`, and the seam does not refuse.
- [ ] **Step 2: Run** `tests/test_inference_resolve.py -k tools` → pass
  (they pass as soon as Tasks 1-2 land; written after them, they are the
  acceptance, not a red step).

### Task 4: the probe's wire — `tool_calls.py`, the adapters, `single`, `_stamp`

**Files:**
- Create: `backend/src/grimoire/tool_calls.py` (stdlib only):
  - `KEY = "_tool_calls"`; `CHOICES = ("auto", "none", "required")`;
    `NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")`.
  - `check(tools, tool_choice) -> None`: `ValueError` unless `tools` is a
    non-empty tuple of dicts each with a `NAME`-matching `name` (unique), a
    str `description` and `parameters` passing `schemas.check` (spec §3.2's
    rule for a ToolSpec's parameters; `schemas` is a stdlib-only leaf, so
    `tool_calls` stays one); `tool_choice` is None or in `CHOICES`; a choice
    with no tools is refused.
  - `openai_tools(tools) -> list[dict]` (`{"type": "function", "function":
    {name, description, parameters}}`); the OpenAI-style `tool_choice` is
    the neutral string itself.
  - `anthropic_tools(tools) -> list[dict]` (`{name, description,
    input_schema}`); `anthropic_choice(choice) -> dict` (`auto`/`none`/
    `any`), a plain mapping: the adapter never rewrites `required` (the
    probe decides what it asks, Task 5; the loop's downgrade is S4's).
  - `class Collector`: `begin()`; `note(call_id, name)` (a new call per
    distinct non-empty id, a new call for each `""`);
    `openai_fragment(fragment)` (by `index`, default 0: an `id` that
    differs from the call open at that index starts a new call — the
    parallel-calls-at-`index: 0` case; a fragment with no id continues the
    open call, or starts one keyed `#<index>`; a later `function.name`
    fills an empty name); `finish(reason)`; read-only `called: bool`,
    `names() -> tuple[str, ...]`, `finish_reason: str` (normalised to
    `stop`, `tool_calls`, `length`, `other`; "" before any).
  - Feeders that no-op when the holder has no `Collector`:
    `from_openai_chunk(obj, usage)` (every `choices[0].delta.tool_calls`
    fragment, then `choices[0].finish_reason`: `tool_calls` /
    `function_call` → `tool_calls`, `stop`, `length`, else `other`);
    `feed_anthropic(usage, frame)` (the whole `content_block_start` /
    `content_block_delta` frame, so its `index` is in hand for S2's
    `input_json_delta`; S1 reads a start frame's `tool_use` block →
    `note(id, name)` and ignores deltas); `anthropic_stop(usage, stop_reason)` (`tool_use` →
    `tool_calls`, `end_turn`/`stop_sequence` → `stop`, `max_tokens` →
    `length`, else `other`).
- Modify: `backend/src/grimoire/openrouter.py` (`stream(..., tools=None,
  tool_choice=None)` → `_payload(...)` adds `tools`
  (`openai_tools`) and `tool_choice` only when given; the line loop calls
  `tool_calls.from_openai_chunk(obj, usage)` beside
  `llm_reasoning.from_chunk`)
- Modify: `backend/src/grimoire/openai_compatible.py` (the same in
  `stream`'s payload and line loop)
- Modify: `backend/src/grimoire/anthropic.py` (`stream`/`_payload` take
  `tools`/`tool_choice`; `body["tools"]`, `body["tool_choice"] =
  anthropic_choice(choice)` only when given; `_Reader.line` hands each
  `content_block_start`/`content_block_delta` frame to
  `tool_calls.feed_anthropic` before reading its text; `_Reader._delta` hands `stop_reason` to
  `tool_calls.anthropic_stop`; the `_block` docstring's "a tool's partial
  JSON: nothing" stays true — no text is yielded for a call)
- Modify: `backend/src/grimoire/adapters.py` (each `generate` takes
  keyword-only `tools=None, tool_choice=None`; the three HTTP adapters pass
  them to their client only when `tools is not None`;
  `ClaudeAgentAdapter.generate` with tools returns an async generator that
  raises `LLMError("bad_response", "claude connections cannot call
  tools")` on its first step, so the facade sees an attempt failure as for
  any provider error; the protocol's docstring)
- Modify: `backend/src/grimoire/llm.py` (`single(messages, target,
  usage=None, *, tools=None, tool_choice=None)` calls
  `tool_calls.check` when `tools` is given, **before** anything is
  stamped, and passes both through its `_dispatch` lambda; `_dispatch`,
  `_generate` and `_parts_lowered` take them keyword-only and hand them to
  the adapter only when given; `_stamp` keeps a `tool_calls.Collector`
  across its `usage.clear()` and calls `begin()` on it, beside the
  reasoning buffer -- a small block with its own comment, since 01g-S3 adds
  `RUN_KEY` to the same function and the coordinator merges the two; `single`'s docstring says what the keywords are for)
- Modify: `backend/tests/llm_fakes.py` (`FakeLLM.single` and
  `StallingGateway.single` take `tools=None, tool_choice=None`; `FakeLLM`
  records them on the request (`request["tools"]`,
  `request["tool_choice"]`) and takes `tool_calls: Sequence[str] = ()`,
  the names it reports — through the holder's `Collector`, as an adapter
  would — on every `single` that offered tools, with `finish("tool_calls")`
  when it reported any and `finish("stop")` otherwise; the module
  docstring's surface list)
- Create: `backend/tests/test_tool_calls.py`
- Test: `backend/tests/test_openrouter.py`, `test_openai_compatible.py`,
  `test_anthropic.py`, `test_llm.py`, `test_adapter_registry.py`,
  `test_llm_fakes.py`, `test_llm_structured.py`

- [ ] **Step 1: Failing tests**
  - `test_tool_calls.py`: `check` accepts the probe's shape and refuses an
    empty tuple, a list, a bad name, a non-object schema, an unknown
    choice, a choice without tools, a duplicate name and parameters failing
    `schemas.check` (`{"type": "object", "properties": {}}` without
    `additionalProperties: false`); both lowerings; `anthropic_choice` maps
    `required` to `any` whatever else the body holds; the `Collector`: fragments of
    one call across chunks are one call; two ids at `index: 0` are two
    calls; two indices are two calls; a name arriving after the id fills
    it; `begin()` empties it; each finish mapping; both feeders no-op on a
    holder without a `Collector` and on `None`.
  - Adapters (MockTransport, each suite's own client helper): the body
    carries `tools`/`tool_choice` in its kind's spelling only when given
    (OpenRouter and OpenAI-compatible: `[{"type": "function", ...}]` and
    `"required"`; Anthropic: `input_schema` and `{"type": "any"}`, and still
    `{"type": "any"}` beside `effective={"thinking": {"type": "adaptive"}}`
    -- the adapter does not rewrite);
    a stream carrying `delta.tool_calls` and `finish_reason: "tool_calls"`
    yields no text and leaves the holder's `Collector` `called` with
    `names() == ("ping",)`; an Anthropic stream with a `tool_use`
    `content_block_start`, an `input_json_delta` and `stop_reason:
    "tool_use"` the same; a stream without calls leaves it uncalled with
    `finish_reason == "stop"`; the existing no-tools body tests pass.
  - `test_llm.py`: `single(..., tools=..., tool_choice="required")` over
    `FakeProvider` hands the client `tools`/`tool_choice` as keywords, and
    a `single` without them hands neither (the kwargs dict is what it
    was); a malformed `tools` raises `ValueError` with no provider call and
    an untouched holder; `_stamp` keeps the same `Collector` object in the
    holder and empties it, and adds none to a holder that had none.
  - `test_adapter_registry.py`: the Claude adapter offered tools fails the
    attempt with `bad_response` and never calls its client.
  - `test_llm_fakes.py`: `FakeLLM(["ok"], tool_calls=["ping"]).single(...,
    tools=..., tool_choice="required")` with a `Collector` in the holder
    leaves it called with `("ping",)` and records the tools on the
    request; without `tools=` it notes nothing.
  - `test_llm_structured.py::test_fakes_record_the_schema`'s signature
    comparison passes with the new parameters on both sides.
- [ ] **Step 2: Run** → fail.
  `... -m pytest -q -p no:cacheprovider tests/test_tool_calls.py tests/test_openrouter.py tests/test_openai_compatible.py tests/test_anthropic.py tests/test_llm.py tests/test_adapter_registry.py tests/test_llm_fakes.py tests/test_llm_structured.py`
- [ ] **Step 3: Implement.** **Step 4: Run** the same plus
  `tests/test_adapter_wire_golden.py tests/test_decide_chain_golden.py
  tests/test_import_guard.py tests/test_routing_guard.py
  tests/test_usage_guard.py` → pass.

### Task 5: the `tools` probe and the model test route

**Files:**
- Modify: `backend/src/grimoire/store/inference/probes.py`
  (`TOOLS_PROMPT = "Call the ping tool."`; `PING_TOOL = {"name": "ping",
  "description": "Says the caller is here. Takes no arguments.",
  "parameters": {"type": "object", "properties": {}, "required": [],
  "additionalProperties": False}}` (inside `schemas.check`'s subset);
  `AUTO_ONLY_PRESETS = frozenset({"zai", "zai_coding"})` (z.ai documents
  `tool_choice` as `auto` only); `tool_choice(preset_id, kind, features,
  sent_thinking) -> str` -- `auto` on an `AUTO_ONLY_PRESETS` preset; on an
  `anthropic`-kind target `required` only when `sent_thinking ==
  {"type": "disabled"}` or `features` state `adaptive_thinking: False`
  (the model does not think when nothing is sent: `llm_sampling._thinking`'s
  `thinks_not` and budget-only readings), else `auto` (an adaptive model
  thinks when `thinking` is unset, and forced tool use beside thinking is a
  400); `required` everywhere else; `NO_CALL = "the model answered without calling the tool"`;
  `CAPPED = "the reply reached the test's cap before calling the tool"`;
  `PROBES["tools"] = Probe("tools", "generate", TOOLS_PROMPT_TOKENS,
  MAX_TOKENS)` with `TOOLS_PROMPT_TOKENS = 400` argued structurally in the
  `Probe.prompt_tokens` style — the message and the `ping` definition are a
  few dozen tokens, and providers add their own framing to a request that
  carries tools (Anthropic documents a tool-use system prompt of a few
  hundred tokens), so the guess takes the larger side as the vision guess
  takes the largest per-image charge; to be tuned against real ledger
  rows; `messages("tools")`; `offer(cap, choice) -> dict` — `{"tools":
  (PING_TOOL,), "tool_choice": choice}` for `tools`, `{}` for every other
  probe; `describe("tools", capped, choice)` — "One chat message, “Call the
  ping tool.”, offering one tool, `ping`, that takes no arguments, and
  requiring a call" (`required`) or "and asking for a call" (`auto`), then
  the cap clause and, when capped, "; a model that reasons first can reach
  the cap before calling, which leaves this probe inconclusive"; the module
  docstring's probe list)
- Modify: `backend/src/grimoire/routes/config.py` (`_tools_choice(raw,
  target)` asks `probes.tool_choice` with `providers.infer(raw).id`,
  `target.kind`, `target.model_features` and
  `llm_sampling.effective(target)["effective"].get("thinking")`, and the
  preview and the run both use it, so the confirmation describes the choice
  the run sends; `_probe`'s chat branch: `offer = probes.offer(cap,
  choice)`, **validated with `tool_calls.check` before the meter opens** —
  a `Collector` in the holder makes it non-empty, and `Meter.done` files a
  row for any non-empty holder (`store/usage.py:579`) — then a fresh
  `tool_calls.Collector` is put in `m.usage[tool_calls.KEY]` and
  `client.single(..., **offer)` is sent; after it returns,
  `_missed_call(collector)` answers the outcome: a call → `{"ok": True}`;
  no call and `finish_reason == "length"` → `{"ok": False, "kind":
  "capped", "error": probes.CAPPED}`, **not recorded**, not halting (the
  cap cut it, which says nothing about the model, `_records`' rule); no
  call otherwise → `{"ok": False, "kind": "no_tool_call", "error":
  probes.NO_CALL}`, **recorded** (the model answered), not halting. The
  ledger row is the call's own (`ok`). Every other probe's `single` call
  is unchanged — `**{}`)
- Test: `backend/tests/test_model_test_call.py`

- [ ] **Step 1: Failing tests**
  - `test_the_probe_payloads_are_the_specified_ones` covers `tools`:
    `probes.messages("tools") == [{"role": "user", "content": "Call the
    ping tool."}]`, `probes.offer("tools")`, `probes.offer("generate") ==
    {}`, and `tool_calls.check(**probes.offer("tools"))` passes.
  - `test_the_preview_describes_and_prices_the_tools_probe`: `sends` names
    the tool, the required call and the inconclusive-cap sentence; the estimate prices
    `TOOLS_PROMPT_TOKENS` and `MAX_TOKENS` at the catalog row's rates.
  - `test_a_claude_subscription_is_refused_a_tools_test_before_sending`
    (`test` and `test/preview`: 400 naming `tools`, `fake.calls == 0`, no
    row) — the existing `_test_plan` refusal.
  - Wire, OpenAI-compatible (`_wire_client`): an SSE reply with a `ping`
    `tool_calls` delta and `finish_reason: "tool_calls"` → `results["tools"]
    == {"ok": True}`, recorded `verified["tools"]["ok"] is True`; the
    request body carries `tools` and `"tool_choice": "required"`; a
    `generate` probe in the same run sends a body with neither key; one
    `model-test` row per probe, `status` `ok`.
  - Wire, text-only reply (`_answer`) → `{"ok": False, "kind":
    "no_tool_call", "error": probes.NO_CALL}`, recorded, and the model's
    `tools` capability reads `Cap("unknown", "test", probes.NO_CALL)`.
  - Wire, a reply with no call and `finish_reason: "length"` → `kind ==
    "capped"`, `recorded is False`.
  - Wire, Anthropic, an **adaptive** model (catalog row `features:
    {"adaptive_thinking": True}`) with no `thinking` field in the body: the
    body carries `input_schema` and `{"type": "auto"}`, the preview says
    "asking for a call", and a `tool_use` stream passes. A model whose row
    says `adaptive_thinking: False` is sent `{"type": "any"}`.
  - `probes.tool_choice` unit cases: `zai`/`zai_coding` → `auto`; an
    anthropic target with no features → `auto`; `sent_thinking ==
    {"type": "disabled"}` → `required`; openrouter → `required`.
  - Wire, a `zai` connection (OpenAI-compatible at the z.ai URL): the body's
    `tool_choice` is `"auto"`.
  - `test_a_refused_tool_offer_files_no_row`: `probes.offer` patched to an
    offer `tool_calls.check` refuses → no ledger row (`_rows() == []`) and
    no request sent.
  - A provider 400 refusing `tools` is recorded unverified with its
    scrubbed message, as the vision refusal test does.
  - `FakeLLM(["ok"], tool_calls=["ping"])` through the route → pass;
    `FakeLLM(["ok"])` → `no_tool_call`.
- [ ] **Step 2: Run** → fail.
  `... -m pytest -q -p no:cacheprovider tests/test_model_test_call.py`
- [ ] **Step 3: Implement.** **Step 4: Run** → pass.

### Task 6: the frontend

**Files:**
- Modify: `frontend/src/api/types.ts` (`CapabilityName` + `"tools"`,
  last; `CapabilityNeed` + `"tools"`; `TestableCapability` + `"tools"`;
  each doc comment; `ModelTestProbe`'s comment names the `tools` probe's
  two kinds, `no_tool_call` (filed) and `capped` (not filed))
- Modify: `frontend/src/routes/ProvidersView.tsx` (`TESTABLE` =
  `["generate", "vision", "embed", "decide_native", "tools"]`, the
  server's `NAMES` order; `OVERRIDABLE` + `{ name: "tools", label: "Tool
  calling" }`)
- Modify: `frontend/src/components/inference/selection.ts` (`CANNOT.tools
  = "call tools"`)
- Modify: `frontend/src/components/models/notes.tsx` (`warningOf`'s switch
  gains `case "tools": return null;` with the reason: a tools route's
  refusal is the seam's own sentence, already the row's `problem`, and no
  surface asks a tools warning today)
- `useModelList.probesFor` needs no change (a non-`decide` need names its
  own probe).
- Test: `frontend/src/routes/ProvidersView.test.tsx`,
  `frontend/src/components/inference/ProviderModelPicker.test.tsx`,
  `frontend/src/components/inference/selection.test.ts` (new)

- [ ] **Step 1: Failing tests**
  - `ProvidersView.test.tsx`: the two `previewModelTest` expectations at
    the "catalog's no is offered" and "lists decide_native" tests gain
    `"tools"` (their `CAPS` fixture states no `tools`, which reads as not
    ruled out); new: an adapter `no` on `tools` leaves it out of the Test…
    list; the facts form has a "Tool calling: override" combobox, and
    choosing "no" saves `overrides: { tools: "no" }`.
  - `ProviderModelPicker.test.tsx`: a picker with `needs={["generate",
    "tools"]}` over a row whose `generate` is `yes` and `tools` `unknown`
    previews `capabilities: ["tools"]`.
  - `selection.test.ts`: `droppedFallbackWords(["tools"], "Planted tool
    runs")` reads "The fallback is known not to fit Planted tool runs (it
    cannot call tools), so it is never sent."
  - Backend, `test_inference_capabilities.py::test_the_frontend_mirrors_the_capability_vocabulary`
    (new, through `tests.ts_unions.ts_union`, as `test_suggest_controls.py`
    holds its vocabulary): `CapabilityName` == `NAMES`, `CapabilityNeed` ==
    `NEEDS`' keys, `TestableCapability` == `probes.PROBES`' keys (as sets).
    Nothing held these mirrors before; `tools` is the change that would
    have let them drift.
- [ ] **Step 2: Run** → fail.
  `cd /home/user/wt/01g-s1/frontend && npx vitest run src/routes/ProvidersView.test.tsx src/components/inference/ProviderModelPicker.test.tsx src/components/inference/selection.test.ts`
- [ ] **Step 3: Implement.** **Step 4: Run** the same, `npx tsc --noEmit
  -p .`, and the backend mirror test
  (`tests/test_inference_capabilities.py -k frontend`) → pass.

### Task 7: the gate and the commit

- [ ] Guards and suites the slice touches:
  `cd /home/user/wt/01g-s1/backend && PYTHONPATH=src /home/user/grimoire/backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tool_calls.py tests/test_inference_*.py tests/test_adapter_*.py tests/test_model_test_call.py tests/test_llm*.py tests/test_openrouter.py tests/test_openai_compatible.py tests/test_anthropic.py tests/test_provider_api.py tests/test_routing*.py tests/test_usage_guard.py tests/test_operation_guard.py tests/test_import_guard.py tests/test_docs_guard.py tests/test_decide_chain_golden.py tests/test_format2_play.py`
- [ ] Ratchets: `cd /home/user/wt/01g-s1 && make check-lint check-mypy PY=/home/user/grimoire/backend/.venv/bin/python` and `make check-eslint` — at baseline, no
  baseline edit (if a finding resolves, `make baseline PY=...` and commit
  the smaller file).
- [ ] Frontend: `cd frontend && npx tsc --noEmit -p . && npx vitest run`
  (whole suite once).
- [ ] Whole backend suite once: `make check-py PY=/home/user/grimoire/backend/.venv/bin/python`.
- [ ] Commit `01g-S1: the tools capability and its seam refusal`, body
  naming the catalog-rule decision and the wire S1 takes from S2, then the
  two attribution lines.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI in this environment: an independent reviewer agent ran the
adversarial review of this plan against the spec and the code in place of
`/codex:adversarial-review`. Verdict: revise, then implement. Each finding,
and how it was folded:

- **B1 (blocking): Anthropic implicit thinking.** The first draft decided
  the downgrade in the adapter from `body.get("thinking")`, which the probe
  never sends; an adaptive model thinks when it is unset, so the probe would
  have sent `tool_choice: any` beside implicit thinking, been refused, and
  filed a failed test over the preset's `always`. Folded: the choice is the
  probe's (`probes.tool_choice`, from the target: `required` only where
  thinking is explicitly disabled or the catalog says the model does not
  think), the adapter lowers what it is handed and never rewrites
  `required`, `describe` says "asking for" when it asks `auto`, and a wire
  test covers an adaptive model with no `thinking` field (Review Focus,
  Tasks 4-5).
- **S2: z.ai takes `tool_choice: auto` only.** Folded: `AUTO_ONLY_PRESETS`,
  and a wire test on a `zai` connection.
- **S3: `PING_TOOL` must pass `schemas.check`.** Folded: the schema carries
  `required: []` and `additionalProperties: false`, and `tool_calls.check`
  runs every definition's parameters through `schemas.check`.
- **S4: slice-graph drift.** Folded as "Drift from the spec's slice graph"
  (the spec is not edited; S2 builds on S1 and drops what S1 took);
  `_stamp`'s change kept small and commented for the merge with 01g-S3's
  `RUN_KEY`; `feed_anthropic` takes the whole frame so S2 has the block
  `index`.
- **S5: the 64-token cap.** Folded: stated in the preview's sentence and as
  an "Open for the spec" item; no per-probe cap in this slice.
- **S6: OQ5 evidence.** Folded: OpenRouter's own per-model description
  cited, the repo precedent stated as weaker than first claimed (the
  sampler omits; here absence refuses), and the stale-cache case named.
- **N7: a Collector makes the holder non-empty.** Folded: the offer is
  checked before the meter opens, with a test that a refused offer files
  no row.
- **N8:** `ModelTestProbe`'s comment names `no_tool_call` and `capped`.
- **N9:** `anthropic` `always` reaching every anthropic-kind connection is
  stated in Review Focus.

Accepted as planned by the reviewer: `TOOLS_PROMPT_TOKENS = 400` as a
stated upper bound, `NEEDS["tools"]` with the `need=tools` Literal and the
`warningOf` case, and the preset table apart from z.ai's `tool_choice`.

## Review and final gate (substitute review, 2026-10-10)

No Codex CLI: an independent reviewer agent reviewed the slice diff (in
place of `/codex:review`) and checked it against spec §3.14, C1 and the S1
entry (in place of the final spec gate). Verdict: ready to land, after
these were folded (amended into the one commit):

- **S1: §3.11's downgrade misses implicit thinking.** Recorded under "Open
  for the spec": S4 should reuse `probes.tool_choice`'s rule from a gateway
  leaf.
- **S2: Claude behind OpenRouter.** `probes.tool_choice` takes the model id
  and asks `auto` for an `anthropic/` id on an OpenRouter connection;
  tested, and noted in Review Focus.
- **S3 (handoff): a call-only reply counts 0 completion tokens and the
  definitions are outside the prompt estimate.** Recorded under "Handoffs
  to 01g-S2".
- **N4: a dropped `tools` fact can bring a refusal back.** Review Focus
  corrected: an older build dropping a user `yes` or passed verdict over an
  OpenRouter catalog `no` restores the refusal; accepted, fixed by testing
  or overriding again.
- **N5: the Claude adapter's refusal would be observed.** Recorded for S2's
  `_tools_refusal` (unreachable in S1).
- **N6: an id-less then id-carrying fragment at one index opens two calls.**
  Recorded for S2's argument accumulation.
- **N7: the confirm gate for `tools`.** Added
  `test_an_unconfirmed_tools_test_is_refused_and_nothing_is_sent`.

Checked and found right by the reviewer: the confirm gate, byte-identical
bodies without tools, the per-kind lowering, the catalog rule's OpenRouter
scope, and the frontend mirrors.

## Rebase onto 01g-S3 (2026-10-10)

01g-S3 landed first and replaced `Meter.done`'s holder-emptiness test with
`store.usage.sent`, which ignores the keys a caller places before a call
(`PRE_SEND_KEYS`). On the rebase: `tool_calls.KEY` joins `PRE_SEND_KEYS`,
restated in `store/usage.py` as `TOOL_CALLS_KEY` and pinned equal in
`test_usage_estimate.py::test_estimate_keys_are_one_spelling`;
`test_incoming_capture.py::test_the_keys_stamp_keeps_are_the_pre_send_keys`
gives the collector a candidate value; `llm._stamp` keeps both S3's
`RUN_KEY` and this slice's collector. Task 5's "a `Collector` makes the
holder non-empty" is therefore history: a holder carrying only the
collector is not a sent call. The offer is still checked before the meter
opens, and the `_probe` comment now says why in terms of `usage.sent`.
