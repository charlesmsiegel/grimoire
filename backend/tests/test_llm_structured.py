"""`schema=` on the facade: structured mode, decided per attempt (slice F, spec 7.2).

The resolver flags an attempt of a decide resolution whose `structured_output`
is `yes` (`wire.Target.structured` on its target); the facade reads the flag
from the target each attempt carries, so a fallback without the mode is sent
the same prompt and no structured envelope. Each adapter owns its wire
spelling: `response_format` on OpenRouter and OpenAI-compatible endpoints,
`output_config.format` on the Anthropic API, merged beside an effort control.
A 400 naming the structured field is not a sampler-preset refusal (I3), so the
fallback is still tried.

Raw `httpx` against `httpx.MockTransport` for the adapters, as
`test_anthropic.py` does -- no network, no SDK.
"""

from __future__ import annotations

import dataclasses
import inspect
import json

import httpx
import pytest

import grimoire.store as store
from grimoire import decisions, llm, llm_sampling, wire
from grimoire.anthropic import AnthropicClient
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.openrouter import OpenRouterClient
from grimoire.store.inference import resolve as inf
from tests.llm_fakes import FakeLLM, HeldCassette, ScriptedProvider

from . import inference_baseline as baseline

SCHEMA = decisions.schema(
    [decisions.Item("Mara closes the door behind her.",
                    (decisions.Predicate("over", "Is the scene over?"),))],
    explain=True)

OPENAI_SSE = ('data: {"choices":[{"delta":{"content":"{}"}}]}\n\n'
              "data: [DONE]\n\n")

ANTHROPIC_SSE = "".join(
    f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in (
        {"type": "message_start",
         "message": {"model": "claude-opus-4-7", "usage": {"input_tokens": 3}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "{}"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 1}},
        {"type": "message_stop"}))

#: A current Claude's catalog row: adaptive thinking, so an effort preset is
#: sent as `thinking` AND `output_config.effort`.
ADAPTIVE = {"adaptive_thinking": True, "enabled_thinking": False,
            "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 64000}

MESSAGES = [{"role": "system", "content": "Answer as JSON."},
            {"role": "user", "content": "Mara closes the door behind her."}]


def _recording(seen: list, respond):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return respond(request)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(body: str):
    return lambda request: httpx.Response(200, text=body)


async def _drain(agen):
    return [c async for c in agen]


# ---- the adapters: each owns its wire spelling ----
async def test_openrouter_sends_response_format_only_with_a_schema():
    seen: list = []
    client = OpenRouterClient(http=_recording(seen, _ok(OPENAI_SSE)))
    await _drain(client.stream(MESSAGES, "vendor/a", "k", schema=SCHEMA))
    await _drain(client.stream(MESSAGES, "vendor/a", "k"))
    assert seen[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "reply", "strict": True, "schema": SCHEMA}}
    assert "response_format" not in seen[1]
    # Nothing else about the body moved.
    assert {k: v for k, v in seen[0].items() if k != "response_format"} == seen[1]


async def test_openai_compatible_sends_response_format_only_with_a_schema():
    seen: list = []
    client = OpenAICompatibleClient(http=_recording(seen, _ok(OPENAI_SSE)))
    await _drain(client.stream(MESSAGES, "local-model", "k", "http://localhost:1234/v1",
                               schema=SCHEMA))
    await _drain(client.stream(MESSAGES, "local-model", "k", "http://localhost:1234/v1"))
    assert seen[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "reply", "strict": True, "schema": SCHEMA}}
    assert "response_format" not in seen[1]
    assert {k: v for k, v in seen[0].items() if k != "response_format"} == seen[1]


async def test_anthropic_merges_format_into_output_config():
    seen: list = []
    client = AnthropicClient(http=_recording(seen, _ok(ANTHROPIC_SSE)))
    effective = {"max_tokens": 900, "thinking": {"type": "adaptive"},
                 "output_config": {"effort": "high"}}
    await _drain(client.stream(MESSAGES, "claude-opus-4-7", "k", effective=effective,
                               schema=SCHEMA))
    await _drain(client.stream(MESSAGES, "claude-opus-4-7", "k", schema=SCHEMA))
    await _drain(client.stream(MESSAGES, "claude-opus-4-7", "k", effective=effective))
    fmt = {"type": "json_schema", "schema": SCHEMA}
    assert seen[0]["output_config"] == {"effort": "high", "format": fmt}
    assert seen[0]["thinking"] == {"type": "adaptive"}
    assert seen[1]["output_config"] == {"format": fmt}
    # Without a schema the body is today's, and the caller's dict was not touched.
    assert seen[2]["output_config"] == {"effort": "high"}
    assert effective["output_config"] == {"effort": "high"}


# ---- the facade: per attempt, from the flag on the attempt's own target ----
def _or_conn(conn_id: str, model: str, **fields) -> wire.Target:
    return wire.Target(**{"provider_id": conn_id, "provider_name": conn_id,
                          "kind": "openrouter", "model": model, "requested_model": model,
                          "api_key": "k", **fields})


async def test_the_facade_sends_structured_mode_only_to_a_flagged_attempt():
    seen: list = []
    client = LLMClient(openrouter=OpenRouterClient(http=_recording(seen, _ok(OPENAI_SSE))),
                       timeout=0, retries=0)
    await client.complete(MESSAGES, _or_conn("a", "vendor/a"), schema=SCHEMA)
    await client.complete(MESSAGES, _or_conn("a", "vendor/a", structured=True),
                          schema=SCHEMA)
    await client.complete(MESSAGES, _or_conn("a", "vendor/a", structured=True))
    assert "response_format" not in seen[0]
    assert seen[1]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "response_format" not in seen[2]   # flagged, but nothing asked for
    # And an unflagged attempt's adapter is called exactly as before: no keyword.
    provider = ScriptedProvider(chunks=("{}",))
    stubbed = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                        anthropic=provider, timeout=0, retries=0)
    for kind in ("openrouter", "openai_compatible", "anthropic", "claude"):
        await stubbed.complete(MESSAGES, _or_conn("a", "m", kind=kind), schema=SCHEMA)
    assert all("schema" not in r["kwargs"] for r in provider.requests)
    # The claude kind never takes it, flagged or not.
    await stubbed.complete(MESSAGES, _or_conn("a", "m", kind="claude", structured=True),
                           schema=SCHEMA)
    assert "schema" not in provider.requests[-1]["kwargs"]


async def test_structured_mode_is_decided_per_attempt():
    """Review Focus 2: a structured-capable primary with a fallback that is not."""
    seen: list = []

    def respond(request):
        if len(seen) == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, text=OPENAI_SSE)

    client = LLMClient(openrouter=OpenRouterClient(http=_recording(seen, respond)),
                       timeout=0, retries=0)
    conn = wire.Chain(_or_conn("a", "vendor/a", structured=True), _or_conn("b", "vendor/b"))
    assert await client.complete(MESSAGES, conn, schema=SCHEMA) == "{}"
    assert [body["model"] for body in seen] == ["vendor/a", "vendor/b"]
    assert seen[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "response_format" not in seen[1]
    # One prompt for both: the schema rides the prompt, the mode rides the attempt.
    assert seen[0]["messages"] == seen[1]["messages"]


# ---- I3: a refused schema is not a refused preset ----
def _adaptive() -> wire.Target:
    """An Anthropic attempt flagged for the mode, with an adaptive effort preset."""
    return _or_conn("a", "claude-opus-4-7", kind="anthropic", model_features=ADAPTIVE,
                    sampling=wire.Sampling(preset_id="p", preset_name="Deep",
                                           scope="connection",
                                           params={"reasoning_effort": "high"}),
                    structured=True)


def _adaptive_primary() -> wire.Chain:
    """`_adaptive`, falling back to an unflagged OpenRouter attempt."""
    return wire.Chain(_adaptive(), _or_conn("b", "vendor/b"))


def _refusing_anthropic(seen: list, message: str) -> AnthropicClient:
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return AnthropicClient(http=_recording(seen, lambda r: httpx.Response(400, json=body)))


async def test_a_refused_schema_is_not_a_preset_refusal():
    sent: list = []
    fallback = ScriptedProvider(chunks=("{}",))
    client = LLMClient(
        anthropic=_refusing_anthropic(
            sent, "output_config.format.schema: unsupported keyword 'anyOf'"),
        openrouter=fallback, timeout=0, retries=0)
    conn = _adaptive_primary()
    assert llm_sampling.effective(conn.primary)["effective"]["output_config"] == {"effort": "high"}
    assert await client.complete(MESSAGES, conn, schema=SCHEMA) == "{}"
    # The primary carried both halves of `output_config`, and was refused for one.
    assert sent[0]["output_config"] == {"effort": "high",
                                        "format": {"type": "json_schema", "schema": SCHEMA}}
    # The fallback was tried, and it is not flagged: no schema keyword reached it.
    assert len(fallback.requests) == 1 and "schema" not in fallback.requests[0]["kwargs"]


async def test_a_refused_schema_does_not_mark_the_connection_failing():
    """CODE-M5: the connection answered, and what it refused was the mode --
    a catalog that over-advertised `structured_outputs`. Its health dot must
    not go red over that while it serves every generate call."""
    seen: list = []
    fallback = ScriptedProvider(chunks=("{}",))
    client = LLMClient(
        anthropic=_refusing_anthropic(
            [], "output_config.format.schema: unsupported keyword 'anyOf'"),
        openrouter=fallback, timeout=0, retries=0,
        observer=lambda conn, error: seen.append((conn.provider_id, error)))
    assert await client.complete(MESSAGES, _adaptive_primary(), schema=SCHEMA) == "{}"
    assert seen == [("b", None)]


async def test_another_400_on_a_flagged_attempt_is_still_observed():
    seen: list = []
    fallback = ScriptedProvider(chunks=("{}",))
    client = LLMClient(
        anthropic=_refusing_anthropic([], "model: claude-nope not found"),
        openrouter=fallback, timeout=0, retries=0,
        observer=lambda conn, error: seen.append((conn.provider_id, error)))
    await client.complete(MESSAGES, _adaptive_primary(), schema=SCHEMA)
    assert [(cid, error is None) for cid, error in seen] == [("a", False), ("b", True)]


@pytest.mark.parametrize(("kind", "detail", "refused"), [
    ("anthropic", "output_config.format.schema: unsupported keyword 'anyOf'", True),
    ("anthropic", "output_config.format: json_schema is not supported", True),
    ("anthropic", "output_config.effort 'high' is not supported by this model", False),
    ("anthropic", "invalid request format", False),
    ("anthropic", "model: claude-nope not found", False),
    ("openrouter", "response_format json_schema is not supported by this endpoint", True),
    ("openrouter", "Invalid schema for response_format 'reply'", True),
    ("openrouter", "temperature is not supported", False)])
def test_a_schema_refusal_names_the_envelope_and_nothing_sent_beside_it(kind, detail, refused):
    conn = dataclasses.replace(_adaptive(), kind=kind)
    assert llm._schema_refusal(LLMError("bad_response", detail, status=400), conn) is refused
    # Never on an attempt that was not sent the mode, nor on a non-refusal status.
    unflagged = dataclasses.replace(conn, structured=False)
    assert llm._schema_refusal(LLMError("bad_response", detail, status=400), unflagged) is False
    assert llm._schema_refusal(LLMError("bad_response", detail, status=500), conn) is False


def _relaying_openrouter(raw: str) -> OpenRouterClient:
    """OpenRouter relaying an upstream 400 in its documented provider-error
    form: its own wrapper message, the upstream body under `metadata.raw`."""
    body = {"error": {"message": "Provider returned error", "code": 400,
                      "metadata": {"provider_name": "Saltmarch",
                                   "raw": json.dumps({"error": {"message": raw}})}}}
    return OpenRouterClient(http=_recording([], lambda r: httpx.Response(400, json=body)))


async def test_a_schema_refusal_relayed_by_openrouter_is_a_schema_refusal():
    """Brutal-1 #2: the wrapper's message names nothing; the upstream's names
    the field. Read through the adapter's real error, it is a schema refusal --
    not a health failure -- and `decide` gets the type it re-sends on."""
    seen: list = []
    client = LLMClient(
        openrouter=_relaying_openrouter(
            "'response_format' of type 'json_schema' is not supported"),
        timeout=0, retries=0, observer=lambda conn, error: seen.append((conn.provider_id, error)))
    with pytest.raises(llm.SchemaRefusalError) as exc:
        await client.complete(MESSAGES, _or_conn("a", "vendor/a", structured=True),
                              schema=SCHEMA)
    assert "json_schema' is not supported" in exc.value.detail
    assert seen == []


async def test_another_upstream_400_relayed_by_openrouter_is_unchanged():
    """A relayed 400 that is not the field refused is the attempt's failure, as
    it always was -- observed, and not a preset refusal either, even naming a
    sampler the preset sent (`detail`, which that match reads, is unchanged)."""
    seen: list = []
    fallback = ScriptedProvider(chunks=("{}",))
    primary = _or_conn("a", "vendor/a", structured=True)
    for raw in ("context length exceeded", "temperature is not supported"):
        seen.clear()
        client = LLMClient(
            openrouter=_relaying_openrouter(raw), openai_compatible=fallback,
            timeout=0, retries=0,
            observer=lambda conn, error: seen.append((conn.provider_id, error)))
        conn = wire.Chain(
            dataclasses.replace(primary, sampling=wire.Sampling(
                preset_id="p", preset_name="Warm", scope="connection",
                params={"temperature": 0.9})),
            _or_conn("b", "local", kind="openai_compatible",
                     base_url="http://localhost:1234/v1"))
        assert await client.complete(MESSAGES, conn, schema=SCHEMA) == "{}"
        assert [(cid, error is None) for cid, error in seen] == [("a", False), ("b", True)]
        assert not isinstance(seen[0][1], llm.SchemaRefusalError)


async def test_a_refused_effort_beside_a_schema_is_still_a_preset_refusal():
    sent: list = []
    fallback = ScriptedProvider(chunks=("{}",))
    client = LLMClient(
        anthropic=_refusing_anthropic(
            sent, "output_config.effort 'high' is not supported by this model"),
        openrouter=fallback, timeout=0, retries=0)
    with pytest.raises(llm.PresetRefusalError) as exc:
        await client.complete(MESSAGES, _adaptive_primary(), schema=SCHEMA)
    assert "fallback connection was not tried" in exc.value.detail
    assert fallback.requests == []


@pytest.mark.parametrize(("detail", "refused"), [
    ("output_config.format.schema: unsupported keyword", False),
    ("output_config.format: json_schema is not supported", False),
    ("output_config.effort 'high' is not supported by this model", True),
    ("effort is not supported here", True),
    ("thinking.type: adaptive is not supported", True)])
def test_the_structured_envelope_is_subtracted_from_the_presets_spellings(detail, refused):
    conn = _adaptive()
    exc = LLMError("bad_response", detail, status=400)
    assert (llm._preset_refusal(exc, conn) is not None) is refused


def test_an_unflagged_attempt_keeps_every_preset_spelling():
    """Without the flag nothing is subtracted: the bare parent still matches,
    exactly as before slice F."""
    conn = dataclasses.replace(_adaptive(), structured=False)
    exc = LLMError("bad_response", "Unsupported field: output_config", status=400)
    assert llm._preset_refusal(exc, conn) is not None


def test_the_structured_share_names_the_envelope_and_never_a_question():
    def flagged(kind: str, structured: bool = True) -> wire.Target:
        return _or_conn("a", "m", kind=kind, structured=structured)

    assert llm._structured_share(flagged("anthropic")) == {
        "output_config": {"format": {"type": None, "schema": None}}}
    for kind in ("openrouter", "openai_compatible"):
        assert llm._structured_share(flagged(kind)) == {
            "response_format": {"type": None, "json_schema": None}}
    assert llm._structured_share(flagged("")) == {
        "response_format": {"type": None, "json_schema": None}}   # an unknown kind's
    assert llm._structured_share(flagged("claude")) == {}
    assert llm._structured_share(flagged("anthropic", structured=False)) == {}


# ---- the resolver: only a decide resolution's capable attempts ----
@pytest.fixture
def at_state(tmp_path):
    opened = []

    def build(name: str) -> dict:
        cm = baseline.client_at(tmp_path / name)
        client = cm.__enter__()
        opened.append(cm)
        return baseline.STATES[name](client)

    yield build
    for cm in reversed(opened):
        cm.__exit__(None, None, None)


def _catalog(conn_id: str, rows: list[dict]) -> None:
    rev = store.llm_connections.read_connection_raw(conn_id)["rev"]
    store.llm_connections.set_cached_models(conn_id, rows, rev)


def test_the_resolver_flags_only_a_decide_resolutions_capable_attempts(at_state):
    at_state("routed")       # global: OpenRouter `vendor/active`, fallback `spare`
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "structured_outputs"]}])
    decide = inf.resolve("chat", operation="decide")
    assert decide.chain.primary.provider_id == "openrouter"
    assert decide.chain.primary.structured is True
    # The fallback is flagged on its own capability, which nobody stated.
    assert decide.chain.fallback is not None and decide.chain.fallback.provider_id == "spare"
    assert decide.chain.fallback.structured is False
    # A generate resolution of the same task is flagged nowhere.
    generate = inf.resolve("chat")
    assert generate.chain.primary.structured is False
    # Otherwise the same target, but for the operation its account names.
    assert (dataclasses.replace(decide.chain.primary, structured=False, account=wire.Account())
            == dataclasses.replace(generate.chain.primary, account=wire.Account()))
    # Per field, so the account's other fields (slice E's billing and role)
    # ride beside the operation.
    assert (decide.chain.primary.account.operation,
            generate.chain.primary.account.operation) == ("decide", "generate")
    assert (dataclasses.replace(decide.chain.primary.account, operation="")
            == dataclasses.replace(generate.chain.primary.account, operation=""))
    # A capable fallback is flagged on its own target, the one the facade sends.
    _catalog("spare", [{"id": "vendor/spare", "params": ["structured_outputs"]}])
    both = inf.resolve("chat", operation="decide")
    assert both.chain.fallback.structured is True


def test_an_unknown_or_response_format_only_capability_leaves_the_flag_off(at_state):
    at_state("fresh")
    assert inf.resolve("scene-break", operation="decide").chain.primary.structured is False
    # Minor 5: `response_format` alone also covers JSON mode, which is not a schema.
    _catalog("openrouter", [{"id": "vendor/active",
                             "params": ["temperature", "response_format"]}])
    resolved = inf.resolve("scene-break", operation="decide")
    assert resolved.attempts[0].capabilities["structured_output"].value == "unknown"
    assert resolved.chain.primary.structured is False


@pytest.mark.parametrize("row", [
    {"id": "vendor/active", "features": {"structured_output": False}},
    # The features' explicit answer outranks the params' within the catalog step.
    {"id": "vendor/active", "params": ["structured_outputs"],
     "features": {"structured_output": False}}])
def test_an_explicit_no_leaves_the_flag_off(at_state, row):
    at_state("fresh")
    _catalog("openrouter", [row])
    resolved = inf.resolve("scene-break", operation="decide")
    assert resolved.attempts[0].capabilities["structured_output"].value == "no"
    assert resolved.chain.primary.structured is False


# ---- the fakes ----
async def test_fakes_record_the_schema():
    fake = FakeLLM([["{}"]])
    conn = _or_conn("a", "vendor/a")
    assert await fake.complete(MESSAGES, conn, schema=SCHEMA) == "{}"
    assert await fake.complete(MESSAGES, conn) == "{}"
    assert fake.schemas == [SCHEMA, None]
    assert fake.calls == 2
    # `single` matches the facade's exactly: no `schema=`, nothing recorded.
    with pytest.raises(TypeError):
        await fake.single(MESSAGES, conn, schema=SCHEMA)
    assert await fake.single(MESSAGES, conn) == "{}"
    assert fake.schemas == [SCHEMA, None]
    assert inspect.signature(FakeLLM.single).parameters.keys() == \
        inspect.signature(LLMClient.single).parameters.keys()
    # A subclass overriding only `stream(messages, conn, usage=None)` still
    # takes the keyword through `complete`.
    held = HeldCassette([{"when": {"contains": "door"}, "reply": "{}"}],
                        hold={"contains": "never in this prompt"})
    assert await held.complete(MESSAGES, conn, schema=SCHEMA) == "{}"
    assert held.schemas == [SCHEMA]
    # `stream` takes the keyword for parity and records nothing.
    assert await _drain(fake.stream(MESSAGES, conn, schema=SCHEMA)) == ["{}"]
    assert fake.schemas == [SCHEMA, None]


# ---- 01f-S2, spec 3.4: a flagged target was sent a schema ----
async def test_a_flagged_target_sent_no_schema_goes_out_unflagged():
    """With no schema, every target is sent unflagged, so `_structured_share`
    -- and with it `_schema_refusal` and `_preset_refusal`'s subtraction --
    reads only an attempt that was sent the envelope."""
    refused = LLMError("bad_response", "response_format is not supported", status=400)
    provider = ScriptedProvider(chunks=(), error=refused)
    observed: list = []
    client = LLMClient(openrouter=provider, timeout=0, retries=0,
                       observer=lambda conn, error: observed.append(error))
    holder: dict = {}
    flagged = _or_conn("a", "vendor/a", structured=True)
    with pytest.raises(LLMError) as exc:
        await client.complete(MESSAGES, flagged, holder)
    # The attempt's own failure: observed, and no re-send is asked for.
    assert not isinstance(exc.value, llm.SchemaRefusalError)
    assert observed == [exc.value]
    sent = holder[llm.ATTEMPTED]
    assert sent.structured is False and llm._structured_share(sent) == {}
    assert dataclasses.replace(sent, structured=True) == flagged
    # The same through `single`, which never sends a schema.
    holder.clear()
    with pytest.raises(LLMError):
        await client.single(MESSAGES, flagged, holder)
    assert holder[llm.ATTEMPTED].structured is False


async def test_an_unflagged_target_is_sent_as_the_same_object():
    provider = ScriptedProvider(chunks=("{}",))
    client = LLMClient(openrouter=provider, timeout=0, retries=0)
    holder: dict = {}
    target = _or_conn("a", "vendor/a")
    await client.complete(MESSAGES, wire.Chain(target, _or_conn("b", "vendor/b")), holder)
    assert holder[llm.ATTEMPTED] is target
    # And a flagged target WITH a schema keeps its flag: decide is unchanged.
    flagged = _or_conn("a", "vendor/a", structured=True)
    await client.complete(MESSAGES, flagged, holder, schema=SCHEMA)
    assert holder[llm.ATTEMPTED] is flagged
