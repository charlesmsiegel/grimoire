import httpx
import pytest

from grimoire.openrouter import OpenRouterClient, OpenRouterError

SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    "data: [DONE]\n\n"
)


def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://openrouter.ai")
    return OpenRouterClient(http=http)


async def test_stream_yields_deltas():
    def handler(request):
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    chunks = [c async for c in client.stream([{"role": "user", "content": "hi"}], "m", "sk-or-x")]
    assert "".join(chunks) == "Hello"


async def test_auth_error_normalized():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.kind == "auth"
    assert exc.value.detail == "bad key"


async def test_error_detail_extracts_nested_message():
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "No endpoints", "code": 404}})

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.detail == "No endpoints"


async def test_error_detail_falls_back_to_raw_text():
    def handler(request):
        return httpx.Response(500, text="upstream exploded")

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.detail == "upstream exploded"


async def test_client_ignores_invalid_ssl_cert_file(monkeypatch):
    # A stale SSL_CERT_FILE (e.g. left by a removed conda install) must not
    # crash client construction — fall back to a working CA bundle.
    monkeypatch.setenv("SSL_CERT_FILE", "Z:/no/such/cacert.pem")
    client = OpenRouterClient()
    http = client._client()
    assert isinstance(http, httpx.AsyncClient)
    await client.aclose()


async def test_stream_surfaces_unexpected_errors(monkeypatch):
    # Any failure building the HTTP client (not just httpx errors) must be
    # reported as an OpenRouterError, never escape as a silent empty stream.
    client = OpenRouterClient()

    def boom():
        raise FileNotFoundError("no cert")

    monkeypatch.setattr(client, "_client", boom)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.kind == "network"


# ---- liveness and who owns the read bound (#243) ----

REASONING_BODY = (
    ": OPENROUTER PROCESSING\n\n"
    'data: {"choices":[{"delta":{"reasoning":"thinking hard"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    "data: [DONE]\n\n"
)


async def test_non_content_frames_are_reported_as_liveness():
    """The facade's idle bound counts what the provider yields, so a model that
    streams only reasoning for minutes would look wedged. Every received frame
    yields an empty heartbeat: activity, no content."""
    def handler(request):
        return httpx.Response(200, text=REASONING_BODY)

    chunks = [c async for c in make_client(handler).stream([], "m", "sk-or-x")]
    assert "".join(chunks) == "Hello"       # nothing extra reaches the caller
    assert chunks.count("") >= 2            # the comment and the reasoning frame


async def test_streaming_leaves_the_read_bound_to_the_facade():
    """Otherwise llm_timeout is a lie above 120s, and "0 disables it" fails at
    120s with a network error."""
    seen = {}

    def handler(request):
        seen.update(request.extensions.get("timeout") or {})
        return httpx.Response(200, text=SSE_BODY)

    [c async for c in make_client(handler).stream([], "m", "sk-or-x")]
    assert seen["read"] is None
    assert seen["connect"] == 30.0


# ---- Retry-After (#144) ----

async def test_a_429_carries_the_providers_retry_after():
    """A guessed backoff is what you use for not knowing. When the provider
    names its own window, `llm._resilient` waits that long instead — so the
    number has to survive the raise rather than being dropped with the rest of
    the response headers."""
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "42"}, text='{"error":{"message":"slow down"}}')

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.kind == "rate_limit"
    assert exc.value.retry_after == 42.0


async def test_an_error_without_the_header_names_no_window():
    def handler(request):
        return httpx.Response(500, text="boom")

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.retry_after is None


@pytest.mark.parametrize("value", [
    "Wed, 21 Oct 2015 07:28:00 GMT",   # the HTTP-date form, deliberately unread
    "soon", "", "-5", "0", "nan", "inf",
])
async def test_an_unreadable_retry_after_is_the_same_as_none(value):
    """Every unreadable case has to mean "back off on our own schedule". The
    two that would actually hurt are the non-finite ones: `inf` compares past
    every cap and `nan` compares false against all of them, so one would refuse
    to retry forever and the other would sail through as if the header said
    nothing while still being used as a delay."""
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": value}, text="{}")

    client = make_client(handler)
    with pytest.raises(OpenRouterError) as exc:
        [c async for c in client.stream([], "m", "sk-or-x")]
    assert exc.value.retry_after is None


# ---- usage capture (#152) ----
USAGE_BODY = (
    'data: {"model":"realm/opus","choices":[{"delta":{"content":"Hi"}}]}\n\n'
    'data: {"model":"realm/opus","choices":[{"delta":{}}],'
    '"usage":{"prompt_tokens":120,"completion_tokens":8,"cost":0.00042}}\n\n'
    "data: [DONE]\n\n"
)


async def test_stream_asks_for_the_usage_block():
    seen = {}

    def handler(request):
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([], "m", "sk-or-x")]
    assert seen["usage"] == {"include": True}


async def test_stream_fills_the_usage_holder_from_the_final_chunk():
    def handler(request):
        return httpx.Response(200, text=USAGE_BODY)

    usage = {}
    client = make_client(handler)
    chunks = [c async for c in client.stream([], "m", "sk-or-x", usage=usage)]

    assert "".join(chunks) == "Hi"
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 8
    assert usage["cost_usd"] == 0.00042
    assert usage["cost_basis"] == "billed"
    assert usage["model"] == "realm/opus"


async def test_a_reply_with_no_usage_block_leaves_the_holder_unpriced():
    def handler(request):
        return httpx.Response(200, text=SSE_BODY)

    usage = {}
    client = make_client(handler)
    [c async for c in client.stream([], "m", "sk-or-x", usage=usage)]
    assert "cost_usd" not in usage
    assert "prompt_tokens" not in usage


async def test_usage_without_a_cost_records_tokens_and_no_price():
    body = ('data: {"choices":[{"delta":{"content":"x"}}],'
            '"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n")

    def handler(request):
        return httpx.Response(200, text=body)

    usage = {}
    client = make_client(handler)
    [c async for c in client.stream([], "m", "sk-or-x", usage=usage)]
    assert usage["prompt_tokens"] == 5
    assert "cost_usd" not in usage


async def test_a_malformed_usage_block_is_dropped_not_fatal():
    body = ('data: {"choices":[{"delta":{"content":"x"}}],"usage":"lots"}\n\n'
            "data: [DONE]\n\n")

    def handler(request):
        return httpx.Response(200, text=body)

    usage = {}
    client = make_client(handler)
    chunks = [c async for c in client.stream([], "m", "sk-or-x", usage=usage)]
    assert "".join(chunks) == "x"
    assert usage == {}


# ---- the cache split (#148) ----

CACHED_BODY = (
    'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    'data: {"choices":[],"model":"realm/opus","usage":{"prompt_tokens":5000,'
    '"completion_tokens":120,"cost":0.002,"prompt_tokens_details":{"cached_tokens":4096}}}\n\n'
    "data: [DONE]\n\n"
)


async def test_a_cache_hit_is_read_out_of_the_prompt_token_details():
    """On this wire shape the cache count is nested under
    `prompt_tokens_details` rather than named at the top of the block, and
    `prompt_tokens` ALREADY includes it — so it lands beside the prompt count,
    never added to it."""
    def handler(request):
        return httpx.Response(200, text=CACHED_BODY)

    usage = {}
    [c async for c in make_client(handler).stream([], "m", "sk-or-x", usage=usage)]

    assert usage["prompt_tokens"] == 5000        # unchanged by the split
    assert usage["cache_read_tokens"] == 4096
    assert "cache_write_tokens" not in usage     # this reply wrote nothing


async def test_a_top_level_cache_read_field_is_read_too():
    """A provider routed through this endpoint can report the Anthropic
    spelling instead; both name the same quantity and map to one column."""
    body = ('data: {"choices":[],"usage":{"prompt_tokens":900,"completion_tokens":10,'
            '"cache_read_input_tokens":800,"cache_creation_input_tokens":50}}\n\n'
            "data: [DONE]\n\n")

    def handler(request):
        return httpx.Response(200, text=body)

    usage = {}
    [c async for c in make_client(handler).stream([], "m", "sk-or-x", usage=usage)]

    assert usage["cache_read_tokens"] == 800
    assert usage["cache_write_tokens"] == 50


async def test_a_reply_that_cached_nothing_records_no_cache_keys():
    def handler(request):
        return httpx.Response(200, text=USAGE_BODY)

    usage = {}
    [c async for c in make_client(handler).stream([], "m", "sk-or-x", usage=usage)]

    assert usage["prompt_tokens"] == 120         # the block is otherwise intact
    assert "cache_read_tokens" not in usage and "cache_write_tokens" not in usage


# ---- the model catalog (#149) and the key probe (#146) ----
async def test_list_models_normalizes_and_sorts_by_id():
    def handler(request):
        assert request.url.path == "/api/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "z/last", "name": "Last", "context_length": 8192,
             "pricing": {"prompt": "0.000002", "completion": "0.000006"}},
            {"id": "a/first"},
        ]})

    models = await make_client(handler).list_models("sk-or-x")
    assert models == [
        {"id": "a/first", "name": "a/first", "context": None,
         "prompt": None, "completion": None},
        {"id": "z/last", "name": "Last", "context": 8192,
         "prompt": "0.000002", "completion": "0.000006"},
    ]


async def test_list_models_without_a_key_sends_no_authorization_header():
    """The wizard lists the catalog before a key has been typed (#149), and
    `Bearer ` with nothing after it is a malformed credential rather than an
    absent one."""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    await make_client(handler).list_models("")
    assert seen["auth"] is None


async def test_list_models_sends_the_key_when_there_is_one():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    await make_client(handler).list_models("sk-or-x")
    assert seen["auth"] == "Bearer sk-or-x"


async def test_list_models_error_is_normalized_like_a_generation():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(OpenRouterError) as exc:
        await make_client(handler).list_models("sk-or-x")
    assert (exc.value.kind, exc.value.detail) == ("rate_limit", "slow down")


async def test_list_models_rejects_a_body_that_is_not_json():
    """An HTML error page from a captive portal or a proxy is a
    `bad_response`, not a crash inside the picker's fetch."""
    def handler(request):
        return httpx.Response(200, text="<html>captive portal</html>")

    with pytest.raises(OpenRouterError) as exc:
        await make_client(handler).list_models("")
    assert exc.value.kind == "bad_response"


async def test_probe_asks_the_authenticated_endpoint_not_the_public_catalog():
    """`/models` answers 200 for a revoked key, so a check built on it would
    report a dead connection healthy — the exact complaint #146 opens with."""
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"data": {"label": "grimoire"}})

    await make_client(handler).probe("sk-or-x")
    assert seen["path"] == "/api/v1/key"


async def test_probe_reports_a_rejected_key_as_auth():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})

    with pytest.raises(OpenRouterError) as exc:
        await make_client(handler).probe("sk-or-dead")
    assert (exc.value.kind, exc.value.detail) == ("auth", "No auth credentials found")


async def test_probe_reports_an_unreachable_host_as_network():
    def handler(request):
        raise httpx.ConnectError("nope")

    with pytest.raises(OpenRouterError) as exc:
        await make_client(handler).probe("sk-or-x")
    assert exc.value.kind == "network"


async def test_the_probe_is_bounded_well_under_a_generations_timeout():
    """A reader who clicked Test connection is watching a spinner; the client's
    120s generation default is not a bound they can sit through."""
    seen = {}

    def handler(request):
        seen.update(request.extensions.get("timeout") or {})
        return httpx.Response(200, json={"data": {}})

    await make_client(handler).probe("sk-or-x")
    assert 0 < seen["read"] <= 30 and 0 < seen["connect"] <= 30
