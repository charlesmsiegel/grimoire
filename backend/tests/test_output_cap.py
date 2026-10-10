"""The per-call output cap (01f-S3, spec 3.7 and 3.9): `generate(max_tokens=)`
caps each attempt at `min(preset's, n)` on new per-call targets
(`wire.Target.with_output_cap`), built with the schema flags by one pure
function a capture can be handed (`inference.call_chain`). The cap goes on
the wire only where the attempt's adapter sends `max_tokens`
(`inference.cap_sent`), and a refusal of a cap only the call carried is the
call's (`llm.CapRefusalError`), never the user's preset.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from grimoire import inference, llm, schemas, wire
from grimoire.anthropic import AnthropicClient
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openrouter import OpenRouterClient
from grimoire.routes import common
from grimoire.store.inference.capabilities import Cap
from tests import wire_kit
from tests.llm_fakes import FakeLLM, ScriptedProvider

CONN = wire_kit.target(provider_id="openrouter", model="vendor/active", api_key="sk-test")
SPARE = wire_kit.target(provider_id="spare", kind="openai_compatible", model="local/spare",
                        api_key="sk-spare", base_url="http://localhost:1234/v1")
WARM = wire.Sampling(preset_id="warm", preset_name="Warm", scope="global",
                     params={"temperature": 0.9, "max_tokens": 4000})

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title"],
          "properties": {"title": {"type": "string"}}}
MESSAGES = [{"role": "system", "content": f"Reply as JSON:\n{schemas.render(SCHEMA)}"},
            {"role": "user", "content": "Mara reaches Saltmarch."}]


def _drain(agen) -> list[str]:
    async def go():
        return [delta async for delta in agen]
    return asyncio.run(go())


def _run(got, stream: bool):
    return _drain(got) if stream else asyncio.run(got)


# ---- wire.Target.with_output_cap ----
def test_a_cap_with_no_preset_is_the_calls():
    capped = CONN.with_output_cap(300)
    assert capped.sampling.params == {"max_tokens": 300}
    assert capped.sampling.call_cap == 300
    assert (capped.sampling.preset_id, capped.sampling.scope) == ("", "none")
    assert CONN.sampling == wire.Sampling() and CONN.sampling.call_cap is None
    assert replace(capped, sampling=CONN.sampling) == CONN


@pytest.mark.parametrize(("preset", "cap", "sent"), [(4000, 300, 300), (200, 300, 200)])
def test_a_cap_is_the_lower_of_the_presets_and_the_calls(preset, cap, sent):
    target = replace(CONN, sampling=replace(WARM, params={**WARM.params, "max_tokens": preset}))
    capped = target.with_output_cap(cap)
    assert capped.sampling.params == {"temperature": 0.9, "max_tokens": sent}
    assert capped.sampling.call_cap == cap
    # The preset's own identity, so the ledger still names what was sent.
    assert (capped.sampling.preset_id, capped.sampling.preset_name,
            capped.sampling.scope) == ("warm", "Warm", "global")
    assert target.sampling.params["max_tokens"] == preset


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "300", None])
def test_with_output_cap_refuses_what_is_not_a_positive_int(bad):
    with pytest.raises(ValueError):
        CONN.with_output_cap(bad)  # type: ignore[arg-type]


# ---- inference.call_chain and generate(max_tokens=) ----
def _resolved(*caps: str, sent: wire.Chain | wire.Target | None = None):
    resolved = wire_kit.resolution(wire.Chain(CONN, SPARE) if sent is None else sent)
    if not caps:
        return resolved
    attempts = tuple(replace(a, capabilities={"structured_output": Cap(v, "catalog")})
                     for a, v in zip(resolved.attempts, caps, strict=True))
    return replace(resolved, attempts=attempts)


def test_call_chain_with_nothing_asked_is_the_resolutions_own():
    resolved = _resolved()
    # `chain` is built per read, so equal is what "its own" can mean.
    assert inference.call_chain(resolved) == resolved.chain


def test_call_chain_caps_every_attempt_and_flags_the_capable():
    resolved = _resolved("yes", "unknown")
    chain = inference.call_chain(resolved, schema=SCHEMA, max_tokens=500)
    assert [t.sampling.params.get("max_tokens") for t in chain.attempts] == [500, 500]
    assert [t.sampling.call_cap for t in chain.attempts] == [500, 500]
    assert [t.structured for t in chain.attempts] == [True, False]
    assert resolved.chain == wire.Chain(CONN, SPARE)       # untouched
    assert inference.call_chain(resolved, max_tokens=500).primary.structured is False


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("schema", [None, SCHEMA])
def test_generate_sends_exactly_call_chain(stream, schema):
    resolved = _resolved("yes", "yes")
    before = [a.target for a in resolved.attempts]
    fake = FakeLLM([['{"title": "Dawn"}']])
    _run(inference.generate("chat", MESSAGES, client=fake, resolved=resolved,
                            schema=schema, max_tokens=500, stream=stream), stream)
    sent = fake.requests[0]["chain"]
    assert sent == inference.call_chain(resolved, schema=schema, max_tokens=500)
    assert sent.primary.sampling.params == {"max_tokens": 500}
    assert all(a.target is t for a, t in zip(resolved.attempts, before, strict=True))


@pytest.mark.parametrize("bad", [0, -5, True, "500", 1.0])
@pytest.mark.parametrize("stream", [True, False])
def test_a_cap_that_is_not_a_positive_int_is_refused_before_any_call(bad, stream):
    fake = FakeLLM([["never"]])
    holder: dict = {}
    with pytest.raises(ValueError, match="max_tokens"):
        inference.generate("chat", MESSAGES, client=fake, resolved=_resolved(),
                           usage=holder, max_tokens=bad, stream=stream)
    assert fake.calls == 0 and holder == {}


def test_the_cap_is_held_to_the_models_known_max_output():
    """01i-C1: a known maximum output (the user's or the catalog's) holds the
    caller's cap down to it; an unknown one holds nothing back."""
    import dataclasses

    from grimoire import wire

    assert CONN.limits.max_output == wire.UNKNOWN_LIMIT
    assert inference.clamp_to_max_output(CONN, 777) == 777
    capped = dataclasses.replace(CONN, limits=wire.Limits(
        max_output=wire.Limit(500, "catalog")))
    assert inference.clamp_to_max_output(capped, 777) == 500
    assert inference.clamp_to_max_output(capped, 300) == 300
    spare = dataclasses.replace(SPARE, limits=wire.Limits(
        max_output=wire.Limit(1000, "user")))
    chain = inference.call_chain(_resolved(sent=wire.Chain(capped, spare)), max_tokens=777)
    # The cap asked for is recorded as asked; each attempt sends what its
    # own maximum allows.
    assert (chain.primary.sampling.call_cap, chain.fallback.sampling.call_cap) == (777, 777)
    assert (chain.primary.sampling.params["max_tokens"],
            chain.fallback.sampling.params["max_tokens"]) == (500, 777)
    # "As much as the model allows" is held, never refused.
    assert inference.clamp_to_max_output(capped, 10**6) == 500
    assert inference.clamp_to_max_output(CONN, 10**6) == inference.MAX_OUTPUT_CAP


# ---- inference.cap_sent: where the adapter puts the cap on the wire ----
@pytest.mark.parametrize(("fields", "sent"), [
    ({"kind": "openrouter", "model_params": None}, True),              # unverified: sent
    ({"kind": "openrouter", "model_params": ("max_tokens", "temperature")}, True),
    ({"kind": "openrouter", "model_params": ("temperature",)}, False),  # catalog omits it
    ({"kind": "claude"}, False),                                        # no sampling at all
    ({"kind": "anthropic"}, True),
    ({"kind": "openai_compatible", "base_url": "http://localhost:1234/v1"}, True),
    ({"kind": "openai_compatible", "base_url": "https://api.openai.com/v1"}, True),
])
def test_cap_sent_answers_per_adapter(fields, sent):
    capped = replace(CONN, **fields).with_output_cap(256)
    assert inference.cap_sent(capped) is sent
    # An uncapped target with no preset sends no cap of its own.
    assert inference.cap_sent(replace(CONN, **fields)) is False


# ---- on the wire ----
OPENAI_SSE = ('data: {"choices":[{"delta":{"content":"{}"}}]}\n\n'
              "data: [DONE]\n\n")


def _recording(seen: list, respond):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return respond(request)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _generate(client, resolved, **kwargs) -> str:
    return asyncio.run(inference.generate("chat", MESSAGES, client=client, resolved=resolved,
                                          stream=False, **kwargs))


def test_the_cap_reaches_an_openrouter_body_and_not_one_whose_catalog_omits_it():
    seen: list = []
    client = LLMClient(openrouter=OpenRouterClient(
        http=_recording(seen, lambda r: httpx.Response(200, text=OPENAI_SSE))),
        timeout=0, retries=0)
    _generate(client, _resolved(sent=CONN), max_tokens=321)
    omitting = replace(CONN, model_params=("temperature",))
    _generate(client, _resolved(sent=omitting), max_tokens=321)
    _generate(client, _resolved(sent=CONN))
    assert seen[0]["max_tokens"] == 321
    assert "max_tokens" not in seen[1]
    assert "max_tokens" not in seen[2]          # no cap asked: today's body


def test_the_cap_is_the_anthropic_apis_required_max_tokens():
    seen: list = []
    sse = "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in (
            {"type": "message_start", "message": {"model": "claude-x", "usage": {}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "{}"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
            {"type": "message_stop"}))
    client = LLMClient(anthropic=AnthropicClient(
        http=_recording(seen, lambda r: httpx.Response(200, text=sse))), timeout=0, retries=0)
    target = replace(CONN, kind="anthropic", model="claude-x", requested_model="claude-x")
    _generate(client, _resolved(sent=target), max_tokens=1234)
    _generate(client, _resolved(sent=target))
    assert seen[0]["max_tokens"] == 1234
    assert seen[1]["max_tokens"] != 1234          # the API's default, as before


def test_the_claude_sdk_is_sent_no_cap():
    provider = ScriptedProvider(chunks=("{}",))
    client = LLMClient(claude=provider, timeout=0, retries=0)
    target = replace(CONN, kind="claude", model="sonnet", requested_model="sonnet")
    _generate(client, _resolved(sent=target), max_tokens=99)
    assert provider.requests and all("99" not in json.dumps(r["kwargs"], default=str)
                                     for r in provider.requests)
    assert inference.cap_sent(target.with_output_cap(99)) is False


# ---- a refused call cap is the call's ----
def _refusing(seen: list, message: str) -> OpenRouterClient:
    body = {"error": {"message": message, "code": 400}}
    return OpenRouterClient(http=_recording(seen, lambda r: httpx.Response(400, json=body)))


def _refused_with(sampling: wire.Sampling, message: str) -> tuple[LLMError, list, list]:
    seen: list = []
    fallback = ScriptedProvider(chunks=("{}",))
    client = LLMClient(openrouter=_refusing(seen, message), openai_compatible=fallback,
                       timeout=0, retries=0)
    resolved = _resolved(sent=wire.Chain(replace(CONN, sampling=sampling), SPARE))
    with pytest.raises(LLMError) as exc:
        _generate(client, resolved, max_tokens=500)
    return exc.value, seen, fallback.requests


def test_a_refused_call_cap_is_worded_as_the_calls():
    error, seen, fallback = _refused_with(wire.Sampling(),
                                          "max_tokens is not supported by this model")
    assert isinstance(error, llm.CapRefusalError)
    assert isinstance(error, llm.PresetRefusalError)
    assert "this call's output cap (max_tokens) was refused" in error.detail
    assert "sampler preset" not in error.detail
    assert error.status == 400 and error.kind == "bad_response"
    # Never re-sent without the cap, and the fallback is not tried.
    assert len(seen) == 1 and seen[0]["max_tokens"] == 500 and fallback == []


def test_a_preset_cap_below_the_calls_is_still_the_presets():
    preset = replace(WARM, params={"max_tokens": 200})
    error, _seen, fallback = _refused_with(preset, "max_tokens is not supported")
    assert type(error) is llm.PresetRefusalError
    assert "sampler preset “Warm”" in error.detail and fallback == []


def test_a_refusal_naming_another_control_beside_a_call_cap_is_the_presets():
    error, _seen, _fallback = _refused_with(WARM, "temperature and max_tokens are refused")
    assert type(error) is llm.PresetRefusalError


# ---- the capture reports what the call added ----
def test_the_sampling_report_names_the_flag_and_the_cap_only_when_set():
    plain = common._sampling_report(CONN)
    assert plain is not None and "structured" not in plain and "call_cap" not in plain
    sent = replace(CONN, structured=True).with_output_cap(400)
    report = common._sampling_report(sent)
    assert report is not None
    assert report["structured"] is True and report["call_cap"] == 400
    assert report["applied"]["max_tokens"] == 400
    assert common._sampling_report(None) is None


# ---- draft_completion's keywords (01f-S4) ----
def _draft(fake, **kwargs) -> dict:
    return asyncio.run(common.draft_completion(
        fake, _resolved(sent=CONN), MESSAGES, "chat", lambda text: text, **kwargs))


def test_draft_completion_passes_neither_keyword_unless_given(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    fake = FakeLLM([['{"title": "Dawn"}']])
    assert _draft(fake) == {"state": "landed", "result": '{"title": "Dawn"}'}
    # The resolution's own chain, no schema: what every other draft sends.
    assert fake.requests[0]["chain"] == _resolved(sent=CONN).chain
    assert fake.schemas == [None]


def test_draft_completion_forwards_a_schema_and_a_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    fake = FakeLLM([['{"title": "Dawn"}']])
    assert _draft(fake, schema=SCHEMA, max_tokens=256)["state"] == "landed"
    assert fake.schemas == [SCHEMA]
    assert fake.requests[0]["chain"].primary.sampling.params == {"max_tokens": 256}


def test_a_clamped_cap_on_a_preset_that_carried_one_is_the_presets():
    """Review S2: the preset sent `max_tokens` itself, so a refusal of it is
    the preset's -- even when the call's cap, held to the model's maximum,
    came out at the same figure. `call_cap` still records what was asked."""
    preset = replace(WARM, params={"max_tokens": 4096})
    target = replace(CONN, sampling=preset)
    capped = target.with_output_cap(8000, most=4096)
    assert capped.sampling.params["max_tokens"] == 4096
    assert (capped.sampling.call_cap, capped.sampling.preset_cap) == (8000, 4096)
    assert not llm._call_capped(capped)
    bare = CONN.with_output_cap(8000, most=4096)
    assert bare.sampling.params["max_tokens"] == 4096 and bare.sampling.preset_cap is None
    assert llm._call_capped(bare)
