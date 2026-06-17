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
