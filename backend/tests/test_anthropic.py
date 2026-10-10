"""The direct Anthropic Messages API adapter (slice B, Task 7).

Raw `httpx` against `httpx.MockTransport` -- no network, no SDK. Keys are
invented strings that match no real key pattern.
"""

import json

import httpx
import pytest

from grimoire import catalog, llm_capture, llm_reasoning, llm_sampling, tool_calls, wire
from grimoire.anthropic import (
    API_VERSION,
    PROBE_TIMEOUT,
    AnthropicClient,
    AnthropicError,
)
from grimoire.llm_errors import LLMError, account_limit

KEY = "test-key-anthropic-0000"
MSG = [{"role": "user", "content": "hi"}]


def _sse(*events: dict) -> str:
    """An SSE body: one `event:` / `data:` pair per event, as the API frames it."""
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


START = {"type": "message_start", "message": {
    "id": "msg_1", "model": "claude-test-1", "role": "assistant", "content": [],
    "usage": {"input_tokens": 10, "cache_read_input_tokens": 100,
              "cache_creation_input_tokens": 5, "output_tokens": 1}}}
STOP = {"type": "message_stop"}


def _text(text, index=0):
    return {"type": "content_block_delta", "index": index,
            "delta": {"type": "text_delta", "text": text}}


def _done(stop_reason="end_turn", output_tokens=7, **delta):
    return {"type": "message_delta", "delta": {"stop_reason": stop_reason, **delta},
            "usage": {"output_tokens": output_tokens}}


HELLO = _sse(START,
             {"type": "content_block_start", "index": 0,
              "content_block": {"type": "text", "text": ""}},
             {"type": "ping"},
             _text("Hel"), _text("lo"),
             {"type": "content_block_stop", "index": 0},
             _done(), STOP)


#: The lines of `HELLO` the adapter reads: all of them up to `message_stop`,
#: after which it stops reading (only the frame's closing blank line is left).
_READ = HELLO.splitlines()[:-1]


def _client(handler, seen=None):
    def record(request):
        if seen is not None:
            seen.append(request)
        return handler(request)
    return AnthropicClient(http=httpx.AsyncClient(transport=httpx.MockTransport(record)))


def _ok(body=HELLO):
    return lambda request: httpx.Response(200, text=body)


async def _drain(agen):
    return [c async for c in agen]


async def _body_for(messages, **kw):
    """The JSON body one stream call sends."""
    seen: list = []
    await _drain(_client(_ok(), seen).stream(messages, "claude-test-1", KEY, **kw))
    return json.loads(seen[0].content)


# ---- the request ----
async def test_the_request_goes_to_the_messages_endpoint_with_the_api_headers():
    seen: list = []
    chunks = await _drain(_client(_ok(), seen).stream(MSG, "claude-test-1", KEY))
    assert "".join(chunks) == "Hello"
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == KEY
    assert req.headers["anthropic-version"] == API_VERSION == "2023-06-01"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["accept"] == "text/event-stream"
    assert "authorization" not in req.headers


@pytest.mark.parametrize("base_url", ["https://proxy.example.com",
                                      "https://proxy.example.com/",
                                      "https://proxy.example.com/v1"])
async def test_the_connections_base_url_is_used(base_url):
    seen: list = []
    await _drain(_client(_ok(), seen).stream(MSG, "m", KEY, base_url=base_url))
    assert str(seen[0].url) == "https://proxy.example.com/v1/messages"


async def test_the_body_carries_model_messages_stream_and_a_default_max_tokens():
    body = await _body_for(MSG)
    assert body == {"model": "claude-test-1", "stream": True,
                    "max_tokens": llm_sampling.ANTHROPIC_MAX_TOKENS,
                    "messages": [{"role": "user",
                                  "content": [{"type": "text", "text": "hi"}]}]}
    assert body["max_tokens"] == 16000


async def test_effective_is_merged_into_the_body_and_the_adapter_owns_its_fields():
    effective = {"max_tokens": 900, "stop_sequences": ["END"],
                 "thinking": {"type": "adaptive"}, "output_config": {"effort": "low"},
                 "temperature": 0.4, "model": "not-this", "stream": False}
    body = await _body_for(MSG, effective=effective)
    assert body["max_tokens"] == 900
    assert body["stop_sequences"] == ["END"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["temperature"] == 0.4
    assert body["model"] == "claude-test-1" and body["stream"] is True


async def test_an_off_preset_on_a_model_that_can_turn_thinking_off_sends_disabled():
    """End to end from the catalog row: an adaptive model that reports
    `thinking.types.disabled.supported` is sent `thinking: disabled` for off."""
    row = catalog.entry({"id": "claude-test-1", "type": "model", "display_name": "T",
                         "capabilities": {"thinking": {"types": {
                             "adaptive": {"supported": True}, "enabled": {"supported": True},
                             "disabled": {"supported": True}}}}})
    conn = wire.Target(provider_id="", kind="anthropic", model="claude-test-1",
                       model_features=row["features"],
                       sampling=wire.Sampling(params={"reasoning_effort": "off"}))
    body = await _body_for(MSG, effective=llm_sampling.effective(conn)["effective"])
    assert body["thinking"] == {"type": "disabled"}
    assert "output_config" not in body


async def test_leading_system_messages_become_the_top_level_system():
    body = await _body_for([{"role": "system", "content": "You are the narrator."},
                            {"role": "system", "content": "Be brief."},
                            {"role": "user", "content": "hi"}])
    assert body["system"] == "You are the narrator.\n\nBe brief."
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


async def test_no_system_key_when_there_is_no_system_text():
    assert "system" not in await _body_for([{"role": "system", "content": "  "}, *MSG])


async def test_a_later_system_message_leads_the_next_user_turn():
    body = await _body_for([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "system", "content": "note"},
        {"role": "user", "content": "c"}])
    assert body["system"] == "S"
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "a"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
        {"role": "user", "content": [{"type": "text", "text": "note"},
                                     {"type": "text", "text": "c"}]}]


async def test_a_trailing_system_message_becomes_a_user_turn_and_merges():
    body = await _body_for([
        {"role": "user", "content": "a"},
        {"role": "system", "content": "post-history"}])
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "a"},
                                     {"type": "text", "text": "post-history"}]}]


async def test_a_system_message_before_an_assistant_turn_keeps_its_place():
    """A steer before a prefill: the system text is a user turn of its own,
    ahead of the assistant turn, so the prefill stays the LAST message --
    nothing here moves or drops a trailing assistant turn."""
    body = await _body_for([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "system", "content": "steer"},
        {"role": "assistant", "content": "The partial"}])
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "a"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
        {"role": "user", "content": [{"type": "text", "text": "steer"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "The partial"}]}]


async def test_consecutive_same_role_turns_merge():
    body = await _body_for([{"role": "user", "content": "a"},
                            {"role": "user", "content": "b"},
                            {"role": "assistant", "content": "c"},
                            {"role": "assistant", "content": "d"}])
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "a"},
                                     {"type": "text", "text": "b"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "c"},
                                          {"type": "text", "text": "d"}]}]


async def test_empty_text_blocks_and_empty_messages_are_dropped():
    body = await _body_for([{"role": "user", "content": "a"},
                            {"role": "assistant", "content": ""},
                            {"role": "user", "content": [{"type": "text", "text": ""},
                                                         {"type": "text", "text": "b"}]},
                            {"role": "user", "content": "   "}])
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "a"},
                                     {"type": "text", "text": "b"}]}]


async def test_an_image_url_data_uri_becomes_a_base64_image_block():
    body = await _body_for([{"role": "user", "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]}])
    assert body["messages"][0]["content"] == [
        {"type": "text", "text": "What is this?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "QUJD"}}]


async def test_a_trailing_assistant_turn_is_sent_as_given():
    body = await _body_for([*MSG, {"role": "assistant", "content": "Once upon"}])
    assert body["messages"][-1] == {"role": "assistant",
                                    "content": [{"type": "text", "text": "Once upon"}]}


async def test_an_assistant_first_conversation_opens_on_a_user_turn():
    """A scene started from a greeting: the API refuses a first assistant turn,
    so a placeholder user turn opens it (`openai_compatible`'s strict text)."""
    body = await _body_for([{"role": "system", "content": "S"},
                            {"role": "assistant", "content": "Welcome, traveller."},
                            {"role": "user", "content": "hello"}])
    assert body["system"] == "S"
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "(continue)"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Welcome, traveller."}]},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]}]


async def test_a_prompt_with_nothing_in_it_is_refused_before_sending():
    seen: list = []
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(), seen).stream(
            [{"role": "system", "content": " "}, {"role": "user", "content": ""}], "m", KEY))
    assert err.value.kind == "bad_response" and "nothing to send" in err.value.detail
    assert seen == []


async def test_a_data_uri_with_parameters_keeps_only_its_media_type():
    body = await _body_for([{"role": "user", "content": [
        {"type": "image_url",
         "image_url": {"url": "data:image/png;charset=binary;base64,QUJD"}}]}])
    assert body["messages"][0]["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "QUJD"}}]


async def test_a_data_uri_that_is_not_base64_is_refused_not_sent_as_a_url():
    seen: list = []
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(), seen).stream([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/svg+xml,%3Csvg%3E"}}]}],
            "m", KEY))
    assert err.value.kind == "bad_response"
    assert seen == []


async def test_an_http_image_url_is_sent_as_a_url_source():
    body = await _body_for([{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://img.example.com/a.png"}}]}])
    assert body["messages"][0]["content"] == [
        {"type": "image", "source": {"type": "url", "url": "https://img.example.com/a.png"}}]


async def test_a_system_only_prompt_is_sent_as_the_user_turn():
    body = await _body_for([{"role": "system", "content": "Describe a harbour."}])
    assert "system" not in body
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Describe a harbour."}]}]


async def test_an_unknown_role_is_refused_before_sending():
    seen: list = []
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(), seen).stream([{"role": "tool", "content": "x"}], "m", KEY))
    assert err.value.kind == "bad_response"
    assert seen == []


async def test_a_missing_key_is_refused_before_sending():
    seen: list = []
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(), seen).stream(MSG, "m", ""))
    assert err.value.kind == "missing_key"
    assert seen == []


# ---- the stream ----
async def test_every_line_is_proof_of_life():
    """One `""` per received line, before anything is parsed: a model thinking
    silently, or a ping, still reaches the facade's idle bound as activity."""
    chunks = await _drain(_client(_ok()).stream(MSG, "m", KEY))
    assert chunks.count("") >= len(_READ)
    assert [c for c in chunks if c] == ["Hel", "lo"]


async def test_usage_is_read_from_message_start_and_message_delta():
    usage: dict = {}
    await _drain(_client(_ok()).stream(MSG, "m", KEY, usage=usage))
    assert usage["prompt_tokens"] == 10 + 100 + 5
    assert usage["cache_read_tokens"] == 100
    assert usage["cache_write_tokens"] == 5
    assert usage["completion_tokens"] == 7
    assert usage["model"] == "claude-test-1"
    assert "cost_usd" not in usage


async def test_a_later_cumulative_usage_block_updates_the_prompt_count():
    body = _sse(START, _text("x"),
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                 "usage": {"input_tokens": 12, "cache_read_input_tokens": 100,
                           "cache_creation_input_tokens": 5, "output_tokens": 9}},
                STOP)
    usage: dict = {}
    await _drain(_client(_ok(body)).stream(MSG, "m", KEY, usage=usage))
    assert usage["prompt_tokens"] == 117 and usage["completion_tokens"] == 9


async def test_thinking_goes_to_the_reasoning_side_channel_not_the_text():
    body = _sse(START,
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "thinking", "thinking": ""}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "thinking_delta", "thinking": "Let me see"}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "signature_delta", "signature": "c2ln"}},
                {"type": "content_block_stop", "index": 0},
                _text("Answer", index=1), _done(), STOP)
    buffer = llm_reasoning.Buffer()
    usage = {llm_reasoning.KEY: buffer}
    chunks = await _drain(_client(_ok(body)).stream(MSG, "m", KEY, usage=usage))
    assert [c for c in chunks if c] == ["Answer"]
    assert buffer.drain() == [{"thinking_delta": "Let me see"}]


async def test_an_error_frame_raises_overloaded_as_a_rate_limit():
    body = _sse(START, {"type": "error", "error": {"type": "overloaded_error",
                                                   "message": "Overloaded"}})
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(body)).stream(MSG, "m", KEY))
    assert err.value.kind == "rate_limit"
    assert err.value.detail == "Overloaded"


@pytest.mark.parametrize("etype, kind", [("api_error", "network"),
                                         ("invalid_request_error", "bad_response"),
                                         ("rate_limit_error", "rate_limit")])
async def test_other_error_frames_map_by_type(etype, kind):
    body = _sse(START, _text("partial"), {"type": "error",
                                          "error": {"type": etype, "message": "nope"}})
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(body)).stream(MSG, "m", KEY))
    assert err.value.kind == kind


async def test_an_error_frame_message_is_scrubbed():
    body = _sse({"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "bad image data:image/png;base64,QUJDREVGR0g="}})
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(body)).stream(MSG, "m", KEY))
    assert "QUJD" not in err.value.detail and "[elided]" in err.value.detail


async def test_a_refusal_with_no_text_is_a_bad_response_naming_the_category():
    body = _sse(START, _done("refusal", stop_details={"category": "cyber"}), STOP)
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(body)).stream(MSG, "m", KEY))
    assert err.value.kind == "bad_response"
    assert err.value.detail == "the model declined (cyber)"


async def test_a_refusal_with_no_category_still_says_it_declined():
    body = _sse(START, _done("refusal"), STOP)
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_ok(body)).stream(MSG, "m", KEY))
    assert err.value.detail == "the model declined"


async def test_a_refusal_after_partial_text_still_raises():
    """A refused reply is not a complete one, however much of it arrived."""
    body = _sse(START, _text("Partial"), _done("refusal", stop_details={"category": "cyber"}),
                STOP)
    agen = _client(_ok(body)).stream(MSG, "m", KEY)
    chunks: list = []
    with pytest.raises(AnthropicError) as err:
        while True:
            chunks.append(await anext(agen))
    assert "Partial" in chunks
    assert err.value.kind == "bad_response"
    assert err.value.detail == "the model declined (cyber)"


async def test_a_stream_that_ends_without_message_stop_is_a_network_failure():
    """Content deltas and no `message_stop`: the reply was cut off, and a
    truncated reply must not be accepted as a complete one. `network` is the
    kind an upstream failure carries, so the facade treats it as a failed
    attempt (retry, fallback) rather than an answer."""
    body = _sse(START, _text("Hel"), _text("lo"))
    agen = _client(_ok(body)).stream(MSG, "m", KEY)
    chunks: list = []
    with pytest.raises(AnthropicError) as err:
        while True:
            chunks.append(await anext(agen))
    assert "Hel" in chunks
    assert err.value.kind == "network"
    assert err.value.status is None
    assert "message_stop" in err.value.detail


async def test_a_stream_cut_after_message_delta_is_still_truncated():
    body = _sse(START, _text("Hello"), _done())
    with pytest.raises(AnthropicError) as err:
        await _client(_ok(body)).complete(MSG, "m", KEY)
    assert err.value.kind == "network"


async def test_an_empty_200_stream_is_a_network_failure():
    with pytest.raises(AnthropicError) as err:
        await _client(_ok("")).complete(MSG, "m", KEY)
    assert err.value.kind == "network"


async def test_a_refusal_wins_over_a_missing_message_stop():
    """The refusal arrived (on `message_delta`) before the cut: it is the more
    exact account of what happened, and it is the model's answer."""
    body = _sse(START, _done("refusal", stop_details={"category": "cyber"}))
    with pytest.raises(AnthropicError) as err:
        await _client(_ok(body)).complete(MSG, "m", KEY)
    assert err.value.kind == "bad_response"
    assert err.value.detail == "the model declined (cyber)"


async def test_a_complete_stream_still_passes():
    assert await _client(_ok()).complete(MSG, "m", KEY) == "Hello"
    # Nothing but `message_stop` is still a completed (empty) response.
    assert await _client(_ok(_sse(START, _done(), STOP))).complete(MSG, "m", KEY) == ""


async def test_complete_joins_the_stream():
    assert await _client(_ok()).complete(MSG, "m", KEY) == "Hello"


# ---- HTTP errors ----
def _error(status, etype="invalid_request_error", message="bad request", headers=None):
    return lambda request: httpx.Response(
        status, headers=headers or {},
        json={"type": "error", "error": {"type": etype, "message": message}})


@pytest.mark.parametrize("status, kind", [
    (400, "bad_response"), (401, "auth"), (403, "auth"), (404, "bad_response"),
    (413, "bad_response"), (429, "rate_limit"), (500, "network"),
    (529, "rate_limit"), (503, "network")])
async def test_each_status_maps_to_a_kind_and_carries_the_status(status, kind):
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_error(status, message="said so")).stream(MSG, "m", KEY))
    assert isinstance(err.value, LLMError)
    assert err.value.kind == kind
    assert err.value.status == status
    assert err.value.detail == "said so"


@pytest.mark.parametrize("status", [429, 529])
async def test_a_rate_limit_carries_the_providers_retry_after(status):
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_error(status, "overloaded_error",
                                    headers={"retry-after": "12"})).stream(MSG, "m", KEY))
    assert err.value.retry_after == 12.0


def _spend_cap(_request):
    return httpx.Response(429, json={"type": "error", "error": {
        "type": "rate_limit_error", "message": "Spend limit reached.",
        "details": {"error_code": "enforced_spend_limit_reached"}}})


async def test_a_spend_caps_error_code_survives_on_the_error():
    """The message alone cannot tell a spend cap's 429 from a rate limit's."""
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_spend_cap).stream(MSG, "m", KEY))
    assert err.value.code == "enforced_spend_limit_reached"
    assert err.value.detail == "Spend limit reached."
    assert account_limit(err.value)
    with pytest.raises(AnthropicError) as plain:
        await _drain(_client(_error(429, "rate_limit_error")).stream(MSG, "m", KEY))
    assert plain.value.code is None
    assert not account_limit(plain.value)


async def test_the_catalog_request_keeps_the_error_code_too():
    with pytest.raises(AnthropicError) as err:
        await _client(_spend_cap).list_models(KEY)
    assert err.value.code == "enforced_spend_limit_reached"


@pytest.mark.parametrize("status, message, code, limited", [
    (400, ("You have reached your specified API usage limits. You will regain access "
           "on 2026-11-01 at 00:00 UTC."), None, True),
    (400, "you have reached your specified workspace API usage limits.", None, True),
    (400, "max_tokens: you have reached your specified ... not at the start", None, False),
    (400, "prompt is too long", None, False),
    (429, "Spend limit reached.", "enforced_spend_limit_reached", True),
    (429, "Number of requests exceeded.", None, False),
    (429, "Number of requests exceeded.", "rate_limited", False),
    (402, "Your credit balance is too low.", None, True),
    (500, "You have reached your specified API usage limits.", None, False),
    (None, "You have reached your specified API usage limits.", None, False)])
def test_account_limit_reads_the_status_the_message_and_the_code(status, message, code,
                                                                  limited):
    assert account_limit(LLMError("bad_response", message, status=status, code=code)) is limited


async def test_an_error_body_is_captured_and_scrubbed():
    echo = "data:image/png;base64,QUJDREVGR0g="
    events: list = []
    usage = {llm_capture.KEY: llm_capture.Capture(events.append, "c", 1, "m", "anthropic")}
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(_error(400, message=f"could not read {echo}"))
                     .stream(MSG, "m", KEY, usage=usage))
    assert "QUJD" not in err.value.detail and "[elided]" in err.value.detail
    bodies = [e["payload"] for e in events if e["event"] == "http_error_body"]
    assert bodies and "QUJD" not in bodies[0] and "[elided]" in bodies[0]


async def test_a_non_json_error_body_falls_back_to_its_text():
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(lambda r: httpx.Response(502, text="bad gateway"))
                     .stream(MSG, "m", KEY))
    assert err.value.detail == "bad gateway" and err.value.kind == "network"


async def test_a_transport_failure_is_a_network_error():
    def boom(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(AnthropicError) as err:
        await _drain(_client(boom).stream(MSG, "m", KEY))
    assert err.value.kind == "network"


async def test_every_sse_line_is_captured():
    events: list = []
    usage = {llm_capture.KEY: llm_capture.Capture(events.append, "c", 1, "m", "anthropic")}
    await _drain(_client(_ok()).stream(MSG, "m", KEY, usage=usage))
    lines = [e["payload"] for e in events if e["event"] == "sse_line"]
    assert lines == _READ


async def test_the_request_body_is_never_captured():
    events: list = []
    usage = {llm_capture.KEY: llm_capture.Capture(events.append, "c", 1, "m", "anthropic")}
    await _drain(_client(_ok()).stream([{"role": "user", "content": "secret prompt"}],
                                       "m", KEY, usage=usage))
    assert not any("secret prompt" in json.dumps(e) for e in events)
    assert not any(KEY in json.dumps(e) for e in events)


# ---- the catalog and the probe ----
def _page(ids, has_more, last_id=None):
    return {"data": [{"id": i, "type": "model", "display_name": i.upper(),
                      "max_input_tokens": 200000, "max_tokens": 64000,
                      "capabilities": {"image_input": {"supported": True}}}
                     for i in ids],
            "has_more": has_more, "first_id": ids[0] if ids else None,
            "last_id": last_id if last_id is not None else (ids[-1] if ids else None)}


async def test_list_models_follows_the_pages():
    pages = {None: _page(["m-a", "m-b"], True), "m-b": _page(["m-c"], False)}
    seen: list = []

    def handler(request):
        return httpx.Response(200, json=pages[request.url.params.get("after_id")])

    models = await _client(handler, seen).list_models(KEY)
    assert [m["id"] for m in models] == ["m-a", "m-b", "m-c"]
    assert models[0]["name"] == "M-A" and models[0]["context"] == 200000
    assert all(r.url.path == "/v1/models" and r.method == "GET" for r in seen)
    assert seen[0].headers["x-api-key"] == KEY
    assert seen[0].headers["anthropic-version"] == API_VERSION
    assert "after_id" not in seen[0].url.params
    assert seen[1].url.params["after_id"] == "m-b"
    assert int(seen[0].url.params["limit"]) > 1


async def test_list_models_stops_after_twenty_pages():
    seen: list = []

    def handler(request):
        n = len(seen)
        return httpx.Response(200, json=_page([f"m-{n}"], True))

    models = await _client(handler, seen).list_models(KEY)
    assert len(seen) == 20 and len(models) == 20


async def test_list_models_refuses_a_body_that_is_not_a_catalog():
    with pytest.raises(AnthropicError) as err:
        await _client(lambda r: httpx.Response(200, json={"data": None})).list_models(KEY)
    assert err.value.kind == "bad_response"


async def test_list_models_maps_a_status():
    with pytest.raises(AnthropicError) as err:
        await _client(_error(401, "authentication_error", "invalid x-api-key")).list_models(KEY)
    assert err.value.kind == "auth" and err.value.status == 401
    assert err.value.detail == "invalid x-api-key"


async def test_list_models_needs_a_key():
    seen: list = []
    with pytest.raises(AnthropicError) as err:
        await _client(_ok(), seen).list_models("")
    assert err.value.kind == "missing_key" and seen == []


async def test_list_models_uses_the_base_url():
    seen: list = []
    await _client(lambda r: httpx.Response(200, json=_page([], False)), seen).list_models(
        KEY, base_url="https://proxy.example.com/")
    assert str(seen[0].url).startswith("https://proxy.example.com/v1/models?")


async def test_the_probe_is_one_free_get():
    seen: list = []
    await _client(lambda r: httpx.Response(200, json=_page(["m"], True)), seen).probe(KEY)
    assert len(seen) == 1
    assert seen[0].method == "GET" and seen[0].url.path == "/v1/models"
    assert seen[0].url.params["limit"] == "1"
    assert seen[0].headers["x-api-key"] == KEY


async def test_the_probe_reports_a_rejected_key_as_auth():
    with pytest.raises(AnthropicError) as err:
        await _client(_error(401, "authentication_error", "invalid x-api-key")).probe(KEY)
    assert err.value.kind == "auth"


async def test_the_probe_is_bounded():
    assert PROBE_TIMEOUT.read is not None and PROBE_TIMEOUT.read <= 30


async def test_aclose_resets_an_owned_pool():
    client = AnthropicClient()
    first = client._client()
    await client.aclose()
    assert first.is_closed
    second = client._client()
    assert second is not first
    await client.aclose()


async def test_aclose_leaves_an_injected_pool_alone():
    http = httpx.AsyncClient(transport=httpx.MockTransport(_ok()))
    await AnthropicClient(http=http).aclose()
    assert not http.is_closed
    await http.aclose()


# ---- tools (01g-S1) ----
PING = {"name": "ping", "description": "Says the caller is here.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}}


async def test_tools_are_sent_in_the_messages_api_spelling_only_when_offered():
    plain = await _body_for(MSG)
    assert "tools" not in plain and "tool_choice" not in plain
    offered = await _body_for(MSG, tools=(PING,), tool_choice="required")
    assert offered["tools"] == tool_calls.anthropic_tools((PING,))
    assert offered["tool_choice"] == {"type": "any"}


async def test_the_adapter_never_rewrites_a_required_choice():
    """Forced tool use beside thinking is the caller's to avoid (the probe
    asks `auto` there, the loop's downgrade is 01g-S4's): the client lowers
    what it is handed."""
    body = await _body_for(MSG, effective={"thinking": {"type": "adaptive"}, "max_tokens": 64},
                           tools=(PING,), tool_choice="required")
    assert body["tool_choice"] == {"type": "any"}


async def test_a_tool_use_block_is_noted_and_yields_no_text():
    body = _sse(START,
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "tool_use", "id": "toolu_1", "name": "ping",
                                   "input": {}}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "input_json_delta", "partial_json": "{}"}},
                {"type": "content_block_stop", "index": 0},
                _done("tool_use"), STOP)
    usage = {tool_calls.KEY: tool_calls.Collector()}
    chunks = await _drain(_client(_ok(body)).stream(MSG, "claude-test-1", KEY, usage=usage,
                                                    tools=(PING,)))
    assert "".join(chunks) == ""
    found = usage[tool_calls.KEY]
    assert found.names() == ("ping",) and found.finish_reason == "tool_calls"


async def test_a_text_reply_leaves_the_collector_uncalled():
    usage = {tool_calls.KEY: tool_calls.Collector()}
    await _drain(_client(_ok()).stream(MSG, "claude-test-1", KEY, usage=usage, tools=(PING,)))
    assert not usage[tool_calls.KEY].called
    assert usage[tool_calls.KEY].finish_reason == "stop"
