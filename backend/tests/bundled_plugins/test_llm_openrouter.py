"""Tests for the bundled `llm-openrouter` plugin.

HTTP traffic is intercepted with `httpx.MockTransport` so no network call
is made. The handler asserts on the wire shape that OpenRouter expects
(OpenAI chat-completions) and returns canned responses.
"""

from __future__ import annotations

import json

import httpx
import pytest

from grimoire.types.common import HealthLevel
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.plugins import PluginKind

from .conftest import load_bundled


def _install_mock_transport(provider, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider._client = httpx.AsyncClient(
        base_url=provider._base_url,
        headers={"Authorization": f"Bearer {provider._api_key}"},
        transport=httpx.MockTransport(_capture),
    )
    return requests


def test_manifest_loads_and_protocol_satisfied() -> None:
    result = load_bundled("llm-openrouter", config={"api_key": "sk-or-test"})
    assert result.ok, result.errors
    manifest = result.manifest
    assert manifest is not None
    assert manifest.id == "llm-openrouter"
    assert PluginKind.LLM_PROVIDER in manifest.implements
    provider = result.instances[0].instance
    assert provider.id == "openrouter"
    assert provider.capabilities.streaming is True


def test_defaults_picked_up_from_manifest(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    assert provider._base_url == "https://openrouter.ai/api/v1"
    assert provider._default_model == "anthropic/claude-sonnet-4-6"


def test_extra_headers_and_referer_applied(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={
            "api_key": "k",
            "http_referer": "https://example.test",
            "app_title": "MyApp",
            "extra_headers": {"x-trace": "abc"},
        }
    )
    assert provider._http_referer == "https://example.test"
    assert provider._app_title == "MyApp"
    assert provider._extra_headers == {"x-trace": "abc"}


@pytest.mark.asyncio
async def test_complete_posts_chat_completions(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={"api_key": "sk-or-x", "default_model": "openai/gpt-4o"}
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "openai/gpt-4o"
        assert body["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": "openai/gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello there."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    requests = _install_mock_transport(provider, _handler)
    response = await provider.complete(
        CompletionRequest(
            model="openai/gpt-4o",
            messages=[Message(role=MessageRole.USER, content="hi")],
            system="be brief",
        )
    )
    assert response.text == "Hello there."
    assert response.model == "openai/gpt-4o"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 10
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_complete_raises_on_http_error(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    _install_mock_transport(provider, lambda r: httpx.Response(401, text='{"error":"bad key"}'))
    with pytest.raises(RuntimeError, match="401"):
        await provider.complete(
            CompletionRequest(
                model="x",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )


@pytest.mark.asyncio
async def test_stream_parses_sse_chunks(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    usage_event = (
        b'data: {"choices":[{"delta":{}}],'
        b'"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}'
    )
    body = b"\n".join(
        [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            b'data: {"choices":[{"delta":{"content":" there"}}]}',
            usage_event,
            b"data: [DONE]",
            b"",
        ]
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    _install_mock_transport(provider, _handler)
    chunks = [
        c
        async for c in provider.stream(
            CompletionRequest(model="x", messages=[Message(role=MessageRole.USER, content="hi")])
        )
    ]
    deltas = [c.delta for c in chunks if not c.is_final]
    assert deltas == ["Hello", " there"]
    final = chunks[-1]
    assert final.is_final
    assert final.usage is not None
    assert final.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_list_models_parses_pricing(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.0025", "completion": "0.01"},
                    },
                    {"id": "no-pricing", "name": "no", "context_length": 4096},
                ]
            },
        )

    _install_mock_transport(provider, _handler)
    models = await provider.list_models()
    by_id = {m.id: m for m in models}
    assert by_id["openai/gpt-4o"].context_window == 128000
    # OpenRouter reports per-token; provider rescales to per-1k.
    assert by_id["openai/gpt-4o"].input_cost_per_1k == pytest.approx(2.5)
    assert by_id["openai/gpt-4o"].output_cost_per_1k == pytest.approx(10.0)
    assert by_id["no-pricing"].input_cost_per_1k is None


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_key(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider()
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_healthy_when_models_reachable(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    _install_mock_transport(provider, lambda r: httpx.Response(200, json={"data": []}))
    status = await provider.health_check()
    assert status.level == HealthLevel.HEALTHY
