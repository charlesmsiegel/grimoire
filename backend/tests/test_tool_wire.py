"""01g-S2: tools on the facade, per HTTP adapter, under the REAL facade.

Each test runs `LLMClient` over a real provider client (`llm_fakes.SSEProvider`
serves recorded-shape SSE bodies through `httpx.MockTransport`), so the
facade's `_stamp`, the adapter, the client's lowering and its stream parser
all run as they do in the app: calls are read from the holder's `Collector`,
reset per attempt, and an offer of tools a provider refuses is
`tools_refused`, never observed as the connection failing.
"""

from __future__ import annotations

import json

import pytest

from grimoire import llm, tool_calls, wire
from grimoire.llm import LLMClient
from grimoire.tool_calls import Collector
from tests.llm_fakes import Cut, SSEProvider, anthropic_tool_sse, openai_tool_sse

SEARCH = {"name": "search", "description": "Searches the library.",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                         "required": ["query"], "additionalProperties": False}}
READ = {"name": "read", "description": "Reads one record.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}}
TOOLS = (SEARCH, READ)
MESSAGES = [{"role": "system", "content": "You are the archivist."},
            {"role": "user", "content": "Who keeps the Saltmarch ledger?"}]

CLIENT_ARG = {"openrouter": "openrouter", "openai_compatible": "openai_compatible",
              "anthropic": "anthropic"}


def _target(kind: str, *, provider_id: str = "", model: str = "") -> wire.Target:
    return wire.Target(provider_id=provider_id or f"{kind}-main", kind=kind,
                       model=model or {"anthropic": "claude-test-1"}.get(kind, "vendor/m"),
                       api_key="k", post_process="none",
                       base_url="https://custom.example.com/v1"
                       if kind == "openai_compatible" else "")


def _client(*providers: SSEProvider, retries: int = 0, observed: list | None = None,
            kinds: tuple[str, ...] = ()) -> LLMClient:
    clients = {CLIENT_ARG[kind]: p.client for kind, p in zip(kinds, providers, strict=True)}
    return LLMClient(timeout=0, retries=retries,
                     observer=(lambda conn, error: observed.append((conn.kind, error)))
                     if observed is not None else None, **clients)


def _holder() -> dict:
    return {tool_calls.KEY: Collector()}


# ---- reading calls, per kind ----
@pytest.mark.parametrize("kind", ["openrouter", "openai_compatible"])
async def test_openai_style_calls_after_text_are_read_and_the_text_streams(kind):
    details = [{"type": "reasoning.encrypted", "index": 0, "data": "sealed"}]
    body = openai_tool_sse([("call_1", "search", '{"query": "ledger"}'),
                            ("call_2", "read", "{}")],
                           text="Let me check.", reasoning_details=details)
    provider = SSEProvider(kind, [body])
    client = _client(provider, kinds=(kind,))
    usage = _holder()
    target = _target(kind)
    text = await client.complete(MESSAGES, target, usage, tools=TOOLS, tool_choice="auto")
    assert text == "Let me check."
    found = usage[tool_calls.KEY]
    assert [(c.id, c.name, c.arguments) for c in found.calls()] == [
        ("call_1", "search", {"query": "ledger"}), ("call_2", "read", {})]
    assert found.finish_reason == "tool_calls"
    assert found.opaque() == {"kind": kind, "provider_id": target.provider_id,
                              "model": target.model, "items": details}
    (sent,) = provider.sent
    assert sent["tools"] == tool_calls.openai_tools(TOOLS) and sent["tool_choice"] == "auto"


async def test_anthropic_calls_and_signed_thinking_are_read():
    body = anthropic_tool_sse([("toolu_1", "search", '{"query": "ledger"}')],
                              thinking=("Mara keeps ledgers.", "sig-1"))
    provider = SSEProvider("anthropic", [body])
    client = _client(provider, kinds=("anthropic",))
    usage = _holder()
    text = await client.complete(MESSAGES, _target("anthropic"), usage, tools=TOOLS,
                                 tool_choice="required")
    assert text == ""
    found = usage[tool_calls.KEY]
    (call,) = found.calls()
    assert (call.id, call.name, call.arguments) == ("toolu_1", "search", {"query": "ledger"})
    assert found.finish_reason == "tool_calls"
    assert found.opaque()["items"] == [
        {"type": "thinking", "thinking": "Mara keeps ledgers.", "signature": "sig-1"}]
    (sent,) = provider.sent
    assert sent["tools"] == tool_calls.anthropic_tools(TOOLS)
    assert sent["tool_choice"] == {"type": "any"}


async def test_malformed_arguments_read_as_none():
    body = openai_tool_sse([("call_1", "search", '{"query": "led')])
    client = _client(SSEProvider("openrouter", [body]), kinds=("openrouter",))
    usage = _holder()
    await client.complete(MESSAGES, _target("openrouter"), usage, tools=TOOLS)
    (call,) = usage[tool_calls.KEY].calls()
    assert call.arguments is None and call.raw_arguments == '{"query": "led'


# ---- a retried attempt starts with nothing ----
async def test_a_retried_anthropic_attempts_fragments_are_discarded():
    cut = anthropic_tool_sse([("toolu_1", "search", '{"query": "first"}')], ended=False)
    good = anthropic_tool_sse([("toolu_2", "read", "{}")])
    provider = SSEProvider("anthropic", [cut, good])
    client = _client(provider, retries=1, kinds=("anthropic",))
    usage = _holder()
    await client.complete(MESSAGES, _target("anthropic"), usage, tools=TOOLS)
    assert len(provider.sent) == 2
    assert [(c.id, c.name) for c in usage[tool_calls.KEY].calls()] == [("toolu_2", "read")]
    assert usage["attempts"] == 2


async def test_a_retried_openrouter_attempts_fragments_are_discarded():
    partial = openai_tool_sse([("call_1", "search", '{"query": "first"}')])
    partial = partial[:partial.index('data: {"choices": [{"delta": {}, "finish_reason"')]
    good = openai_tool_sse([("call_2", "read", "{}")])
    provider = SSEProvider("openrouter", [Cut(partial), good])
    client = _client(provider, retries=1, kinds=("openrouter",))
    usage = _holder()
    await client.complete(MESSAGES, _target("openrouter"), usage, tools=TOOLS)
    assert [(c.id, c.name) for c in usage[tool_calls.KEY].calls()] == [("call_2", "read")]


# ---- sending a tool history ----
HISTORY = [*MESSAGES,
           {"role": "assistant", "content": "",
            "tool_calls": [{"id": "call_1", "name": "search", "arguments": {"query": "ledger"}}],
            "_opaque": {"kind": "openrouter", "provider_id": "openrouter-main",
                        "model": "vendor/m",
                        "items": [{"type": "reasoning.encrypted", "data": "sealed"}]}},
           {"role": "tool", "tool_call_id": "call_1", "name": "search", "content": "Mara."}]


async def test_a_tool_history_is_lowered_and_opaque_state_echoed_to_its_own_model():
    provider = SSEProvider("openrouter", [openai_tool_sse(text="Mara keeps it.",
                                                          finish="stop")])
    client = _client(provider, kinds=("openrouter",))
    assert await client.complete(HISTORY, _target("openrouter"), _holder(),
                                 tools=TOOLS) == "Mara keeps it."
    other = SSEProvider("openrouter", [openai_tool_sse(text="ok", finish="stop")])
    await _client(other, kinds=("openrouter",)).complete(
        HISTORY, _target("openrouter", model="vendor/other"), _holder(), tools=TOOLS)
    (own,), (foreign,) = provider.sent, other.sent
    assert own["messages"][2]["reasoning_details"] == [
        {"type": "reasoning.encrypted", "data": "sealed"}]
    assert "reasoning_details" not in foreign["messages"][2]
    for sent in (own, foreign):
        assert sent["messages"][2]["tool_calls"] == [{
            "id": "call_1", "type": "function",
            "function": {"name": "search", "arguments": '{"query": "ledger"}'}}]
        assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_1",
                                       "content": "Mara."}
        assert all("_opaque" not in m for m in sent["messages"])


async def test_a_tool_history_reaches_anthropic_as_tool_use_and_a_result_turn():
    """OpenRouter's opaque state rides the history and is dropped here: it is
    another provider's."""
    provider = SSEProvider("anthropic", [anthropic_tool_sse(text="Mara.", stop="end_turn")])
    await _client(provider, kinds=("anthropic",)).complete(
        HISTORY, _target("anthropic"), _holder(), tools=TOOLS)
    (sent,) = provider.sent
    assert sent["system"] == "You are the archivist."
    assert sent["messages"][1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "call_1", "name": "search", "input": {"query": "ledger"}}]}
    assert sent["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "Mara."}]}


# ---- a call without tools is the call it always was ----
@pytest.mark.parametrize("kind", ["openrouter", "openai_compatible", "anthropic"])
async def test_a_call_without_tools_is_byte_identical(kind):
    body = (anthropic_tool_sse(text="hi", stop="end_turn") if kind == "anthropic"
            else openai_tool_sse(text="hi", finish="stop"))
    bare, collecting = SSEProvider(kind, [body]), SSEProvider(kind, [body])
    await _client(bare, kinds=(kind,)).complete(MESSAGES, _target(kind), {})
    await _client(collecting, kinds=(kind,)).complete(MESSAGES, _target(kind), _holder())
    assert json.dumps(bare.sent) == json.dumps(collecting.sent)
    (sent,) = bare.sent
    assert "tools" not in sent and "tool_choice" not in sent


async def test_the_facade_checks_an_offer_before_anything_is_sent():
    provider = SSEProvider("openrouter", [openai_tool_sse(text="hi", finish="stop")])
    client = _client(provider, kinds=("openrouter",))
    with pytest.raises(ValueError):
        await client.complete(MESSAGES, _target("openrouter"), {}, tools=(), tool_choice="auto")
    with pytest.raises(ValueError):
        client.stream(MESSAGES, _target("openrouter"), {}, tool_choice="auto")
    assert provider.sent == []


# ---- the local estimate counts the offer and the call ----
async def test_the_estimate_counts_the_definitions_and_the_arguments():
    body = openai_tool_sse([("call_1", "search", '{"query": "ledger"}')])
    client = LLMClient(openrouter=SSEProvider("openrouter", [body]).client, timeout=0,
                       retries=0, count_tokens=len)
    usage = _holder()
    await client.complete(MESSAGES, _target("openrouter"), usage, tools=TOOLS)
    assert usage["tokens_estimated"] is True
    assert usage["completion_tokens"] == len('search{"query": "ledger"}')
    plain = len("\n".join(m["content"] for m in MESSAGES))
    assert usage["prompt_tokens"] > plain + len("Searches the library.")


# ---- tools refused (spec 3.5) ----
REFUSED_400 = (400, json.dumps({"error": {"message": "This model does not support tools"}}))
LIMITED = (429, json.dumps({"error": {"message": "slow down"}}))


async def test_a_refusal_of_tools_is_not_observed_or_retried_and_the_fallback_serves():
    primary = SSEProvider("openrouter", [REFUSED_400])
    fallback = SSEProvider("anthropic", [anthropic_tool_sse(text="Mara.", stop="end_turn")])
    observed: list = []
    client = _client(primary, fallback, retries=2, observed=observed,
                     kinds=("openrouter", "anthropic"))
    chain = wire.Chain(_target("openrouter"), _target("anthropic"))
    assert await client.complete(MESSAGES, chain, _holder(), tools=TOOLS) == "Mara."
    assert len(primary.sent) == 1                       # never retried
    assert observed == [("anthropic", None)]            # the refusal was not observed


async def test_every_route_refusing_tools_is_tools_refused():
    primary, fallback = SSEProvider("openrouter", [REFUSED_400]), \
        SSEProvider("openai_compatible", [REFUSED_400])
    observed: list = []
    client = _client(primary, fallback, observed=observed,
                     kinds=("openrouter", "openai_compatible"))
    chain = wire.Chain(_target("openrouter"), _target("openai_compatible"))
    with pytest.raises(llm.ToolsRefusalError) as exc:
        await client.complete(MESSAGES, chain, _holder(), tools=TOOLS)
    assert (exc.value.kind, exc.value.code) == ("bad_response", "tools_refused")
    assert observed == []
    single = _client(SSEProvider("openrouter", [REFUSED_400]), kinds=("openrouter",))
    with pytest.raises(llm.ToolsRefusalError) as alone:
        await single.complete(MESSAGES, _target("openrouter"), {}, tools=TOOLS)
    assert alone.value.code == "tools_refused" and alone.value.status == 400


async def test_one_route_refusing_tools_beside_another_failure_is_composed():
    primary = SSEProvider("openrouter", [REFUSED_400])
    fallback = SSEProvider("openai_compatible", [LIMITED])
    client = _client(primary, fallback, kinds=("openrouter", "openai_compatible"))
    chain = wire.Chain(_target("openrouter"), _target("openai_compatible"))
    with pytest.raises(llm.LLMError) as exc:
        await client.complete(MESSAGES, chain, _holder(), tools=TOOLS)
    assert not isinstance(exc.value, llm.ToolsRefusalError)
    first, second = exc.value.words
    assert first.code == "tools_refused" and second.status == 429
    # The other route's word gives the kind: the feature was refused, the
    # connection answered.
    assert exc.value.kind == second.kind == "rate_limit"


async def test_a_400_naming_tools_without_an_offer_is_an_ordinary_failure():
    observed: list = []
    client = _client(SSEProvider("openrouter", [REFUSED_400]), observed=observed,
                     kinds=("openrouter",))
    with pytest.raises(llm.LLMError) as exc:
        await client.complete(MESSAGES, _target("openrouter"), {})
    assert not isinstance(exc.value, llm.ToolsRefusalError)
    assert [kind for kind, error in observed if error is not None] == ["openrouter"]


@pytest.mark.parametrize("said, refused", [
    ("This model does not support tools", True),
    ("tool_choice 'required' is not supported", True),
    ('"auto" tool choice requires --enable-auto-tool-choice', True),
    ("functions are deprecated here", True),
    ("No endpoints found that support tool use.", True),
    ("messages.2: `tool_use` ids were found without `tool_result` blocks", False),
    ("Invalid 'messages[3].tool_call_id'", False),
    ("context length exceeded", False),
])
def test_what_reads_as_tools_refused(said, refused):
    error = llm.LLMError("bad_response", said, status=400)
    assert llm._tools_refusal(error, True) is refused
    assert llm._tools_refusal(error, False) is False
    assert llm._tools_refusal(llm.LLMError("bad_response", said, status=500), True) is False


async def test_the_claude_adapters_refusal_is_coded_and_not_observed():
    observed: list = []
    client = LLMClient(timeout=0, retries=2,
                       observer=lambda conn, error: observed.append(error))
    target = wire.Target(provider_id="claude-sub", kind="claude", model="sonnet")
    with pytest.raises(llm.ToolsRefusalError) as exc:
        await client.complete(MESSAGES, target, {}, tools=TOOLS)
    assert exc.value.code == "tools_refused"
    assert observed == []
