import httpx
import pytest
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import (
    OpenAICompatibleClient,
    OpenAICompatibleError,
    _strict_messages,
)

SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    "data: [DONE]\n\n"
)


def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://custom.example.com")
    return OpenAICompatibleClient(http=http)


async def test_stream_yields_deltas():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    chunks = [c async for c in client.stream(
        [{"role": "user", "content": "hi"}], "m", "sk-x", "https://custom.example.com/v1")]
    assert "".join(chunks) == "Hello"


async def test_key_omitted_from_headers_when_empty():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([], "m", "", "https://custom.example.com/v1")]
    assert captured["auth"] is None


async def test_key_present_sends_bearer_header():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([], "m", "sk-secret", "https://custom.example.com/v1")]
    assert captured["auth"] == "Bearer sk-secret"


async def test_missing_base_url_raises():
    client = OpenAICompatibleClient()
    with pytest.raises(OpenAICompatibleError) as exc:
        [c async for c in client.stream([], "m", "k", "")]
    assert exc.value.kind == "missing_key"


async def test_auth_error_normalized():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    client = make_client(handler)
    with pytest.raises(OpenAICompatibleError) as exc:
        [c async for c in client.stream([], "m", "k", "https://custom.example.com/v1")]
    assert exc.value.kind == "auth"
    assert exc.value.detail == "bad key"
    assert isinstance(exc.value, LLMError)


async def test_list_models_parses_id_name_context_pricing():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "glm-4.6", "name": "GLM-4.6", "context_length": 128000,
             "pricing": {"prompt": "0.000002", "completion": "0.000006"}},
        ]})

    client = make_client(handler)
    models = await client.list_models("https://custom.example.com/v1", "sk-x")
    assert models == [{"id": "glm-4.6", "name": "GLM-4.6", "context": 128000,
                        "prompt": "0.000002", "completion": "0.000006"}]


async def test_list_models_missing_pricing_and_context_come_back_none():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    client = make_client(handler)
    models = await client.list_models("https://custom.example.com/v1", "")
    assert models == [{"id": "local-model", "name": "local-model",
                        "context": None, "prompt": None, "completion": None}]


# ---- _strict_messages ----

def test_strict_system_before_user_folds_into_it():
    out = _strict_messages([
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Hello"},
    ])
    assert out == [{"role": "user", "content": "Be terse.\n\nHello"}]


def test_strict_system_before_assistant_folds_into_the_preceding_user_turn():
    out = _strict_messages([
        {"role": "user", "content": "Hi"},
        {"role": "system", "content": "Stay in character."},
        {"role": "assistant", "content": "Hello there."},
    ])
    assert out == [
        {"role": "user", "content": "Hi\n\nStay in character."},
        {"role": "assistant", "content": "Hello there."},
    ]


def test_strict_trailing_system_message_becomes_a_final_user_turn():
    out = _strict_messages([
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "system", "content": "npc cards go here"},
    ])
    assert out[-1] == {"role": "user", "content": "npc cards go here"}


def test_strict_folding_induced_adjacent_same_role_runs_remerge():
    out = _strict_messages([
        {"role": "system", "content": "Sys A"},
        {"role": "system", "content": "Sys B"},
        {"role": "user", "content": "Hi"},
    ])
    assert out == [{"role": "user", "content": "Sys A\n\nSys B\n\nHi"}]


def test_strict_assistant_first_gets_a_defensive_placeholder_user_turn():
    out = _strict_messages([{"role": "assistant", "content": "Hello."}])
    assert out[0] == {"role": "user", "content": "(continue)"}
    assert out[1] == {"role": "assistant", "content": "Hello."}


def test_strict_empty_list_gets_a_placeholder():
    assert _strict_messages([]) == [{"role": "user", "content": "(continue)"}]


def test_strict_unrecognized_role_raises():
    with pytest.raises(OpenAICompatibleError) as exc:
        _strict_messages([{"role": "tool", "content": "x"}])
    assert exc.value.kind == "bad_response"


async def test_strict_mode_transforms_payload_before_sending():
    captured = {}

    def handler(request):
        captured["body"] = request.content
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream(
        [{"role": "system", "content": "Be terse."}, {"role": "user", "content": "Hi"}],
        "m", "k", "https://custom.example.com/v1", strict=True)]
    import json
    body = json.loads(captured["body"])
    assert body["messages"] == [{"role": "user", "content": "Be terse.\n\nHi"}]


async def test_no_tools_field_in_payload():
    captured = {}

    def handler(request):
        captured["body"] = request.content
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([{"role": "user", "content": "hi"}], "m", "k",
                                     "https://custom.example.com/v1")]
    import json
    assert "tools" not in json.loads(captured["body"])


# ---- liveness and who owns the read bound (#243) ----

REASONING_BODY = (
    ": keep-alive\n\n"
    'data: {"choices":[{"delta":{"reasoning_content":"thinking hard"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    "data: [DONE]\n\n"
)


async def test_non_content_frames_are_reported_as_liveness():
    """A local reasoning endpoint can send nothing displayable for minutes; the
    facade's idle bound has to see that as activity, not silence."""
    def handler(request):
        return httpx.Response(200, text=REASONING_BODY)

    chunks = [c async for c in make_client(handler).stream([], "m", "", "https://x/v1")]
    assert "".join(chunks) == "Hello"
    assert chunks.count("") >= 2


async def test_streaming_leaves_the_read_bound_to_the_facade():
    seen = {}

    def handler(request):
        seen.update(request.extensions.get("timeout") or {})
        return httpx.Response(200, text=SSE_BODY)

    [c async for c in make_client(handler).stream([], "m", "", "https://x/v1")]
    assert seen["read"] is None
    assert seen["connect"] == 30.0


async def test_list_models_keeps_its_own_read_bound():
    """Only the streaming call hands its read bound to the facade — a hung
    model-list fetch has no guard behind it and must still time out."""
    seen = {}

    def handler(request):
        seen.update(request.extensions.get("timeout") or {})
        return httpx.Response(200, json={"data": []})

    await make_client(handler).list_models("https://x/v1", "")
    assert seen["read"] is not None


# ---- usage capture (#152) ----
async def test_usage_is_read_when_the_endpoint_volunteers_it():
    body = ('data: {"model":"local/glm","choices":[{"delta":{"content":"x"}}],'
            '"usage":{"prompt_tokens":9,"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n")

    def handler(request):
        return httpx.Response(200, text=body)

    usage = {}
    client = make_client(handler)
    [c async for c in client.stream([], "m", "", "https://api.example/v1", usage=usage)]
    assert usage["prompt_tokens"] == 9
    assert usage["completion_tokens"] == 2
    assert usage["model"] == "local/glm"


async def test_no_usage_option_is_sent_to_an_arbitrary_endpoint():
    """A strict endpoint 400s on a request field it does not know, and losing
    generation outright is a far worse trade than losing a token count."""
    seen = {}

    def handler(request):
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n\n")

    client = make_client(handler)
    [c async for c in client.stream([], "m", "", "https://api.example/v1")]
    assert "stream_options" not in seen
    assert "usage" not in seen
