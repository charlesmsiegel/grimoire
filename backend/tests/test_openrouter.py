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
