"""Tests for the bundled `llm-openai-compatible` plugin.

The plugin is a generic OpenAI-compatible client used for Ollama, LM
Studio, vLLM, llama.cpp server, and many hosted services. Tests cover
the preset → base URL resolution and the same wire-format behavior we
verify on the other providers.
"""

from __future__ import annotations

import json

import httpx
import pytest

from grimoire.types.common import HealthLevel
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.plugins import PluginKind

from .conftest import load_bundled


@pytest.fixture(autouse=True)
def _clear_ssl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove host-env SSL overrides that may point at nonexistent cert files."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def _install_mock_transport(provider, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider._client = httpx.AsyncClient(
        base_url=provider._base_url,
        headers={"Content-Type": "application/json"},
        transport=httpx.MockTransport(_capture),
    )
    return requests


def test_manifest_loads_and_protocol_satisfied() -> None:
    result = load_bundled(
        "llm-openai-compatible",
        config={"preset": "ollama"},
    )
    assert result.ok, result.errors
    manifest = result.manifest
    assert manifest is not None
    assert PluginKind.LLM_PROVIDER in manifest.implements
    provider = result.instances[0].instance
    assert provider.id == "openai-compat:ollama"


def test_ollama_preset_defaults(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "ollama"})
    assert provider._base_url == "http://localhost:11434/v1"
    assert provider._auth_scheme == "none"


def test_vllm_preset_defaults(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "vllm", "api_key": "k"}
    )
    assert provider._base_url == "http://localhost:8000/v1"
    assert provider._auth_scheme == "bearer"


def test_lmstudio_preset_defaults(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "lmstudio"})
    assert provider._base_url == "http://localhost:1234/v1"


def test_llamacpp_server_preset_defaults(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "llamacpp-server"}
    )
    assert provider._base_url == "http://localhost:8080/v1"


def test_custom_preset_requires_base_url(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "custom"})
    assert provider._base_url == ""
    # Health check should report unconfigured rather than crashing.


def test_custom_provider_id_and_display(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={
            "preset": "ollama",
            "provider_id": "my-ollama",
            "display_name": "Workstation Ollama",
        }
    )
    assert provider.id == "my-ollama"
    assert provider.name == "Workstation Ollama"


def test_local_url_is_detected(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "ollama"})
    assert provider._is_local() is True


@pytest.mark.asyncio
async def test_complete_round_trip(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={
            "preset": "ollama",
            "default_model": "llama3.1:8b",
            "supports_streaming": True,
        }
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "llama3.1:8b"
        assert body["messages"][-1]["content"] == "hi"
        return httpx.Response(
            200,
            json={
                "model": "llama3.1:8b",
                "choices": [
                    {
                        "message": {"content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    _install_mock_transport(provider, _handler)
    response = await provider.complete(
        CompletionRequest(
            model="",
            messages=[Message(role=MessageRole.USER, content="hi")],
        )
    )
    assert response.text == "Hello!"
    assert response.model == "llama3.1:8b"
    # Local URLs report zero cost so usage rollups still work.
    assert response.cost_estimate_usd == 0.0


@pytest.mark.asyncio
async def test_cached_prompt_tokens_surfaced(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "ollama", "default_model": "m"}
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1500,
                    "completion_tokens": 20,
                    "total_tokens": 1520,
                    "prompt_tokens_details": {"cached_tokens": 1200},
                },
            },
        )

    _install_mock_transport(provider, _handler)
    response = await provider.complete(
        CompletionRequest(model="", messages=[Message(role=MessageRole.USER, content="hi")])
    )
    # cached_tokens is a subset of prompt_tokens — surfaced, not double-counted.
    assert response.usage.input_tokens == 1500
    assert response.usage.total_tokens == 1520
    assert response.usage.cache_read_input_tokens == 1200


@pytest.mark.asyncio
async def test_deepseek_cache_hit_tokens_surfaced(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "ollama", "default_model": "m"}
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 800,
                    "completion_tokens": 5,
                    "total_tokens": 805,
                    "prompt_cache_hit_tokens": 600,
                },
            },
        )

    _install_mock_transport(provider, _handler)
    response = await provider.complete(
        CompletionRequest(model="", messages=[Message(role=MessageRole.USER, content="hi")])
    )
    assert response.usage.cache_read_input_tokens == 600


@pytest.mark.asyncio
async def test_request_without_model_raises(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "ollama"})
    _install_mock_transport(provider, lambda r: httpx.Response(200, json={}))
    with pytest.raises(RuntimeError, match="no model"):
        await provider.complete(
            CompletionRequest(
                model="",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )


@pytest.mark.asyncio
async def test_stream_yields_deltas(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "ollama", "default_model": "llama3.1"}
    )
    body = b"\n".join(
        [
            b'data: {"choices":[{"delta":{"content":"a"}}]}',
            b'data: {"choices":[{"delta":{"content":"b"}}]}',
            b"data: [DONE]",
            b"",
        ]
    )
    _install_mock_transport(
        provider,
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}),
    )
    chunks = [
        c
        async for c in provider.stream(
            CompletionRequest(
                model="llama3.1",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )
    ]
    assert [c.delta for c in chunks if not c.is_final] == ["a", "b"]


@pytest.mark.asyncio
async def test_list_models_parses_openai_shape(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "ollama"})

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "llama3.1:8b", "context_length": 131072},
                    {"id": "qwen2.5:14b"},
                ]
            },
        )

    _install_mock_transport(provider, _handler)
    models = await provider.list_models()
    by_id = {m.id: m for m in models}
    assert by_id["llama3.1:8b"].context_window == 131072
    assert "qwen2.5:14b" in by_id


@pytest.mark.asyncio
async def test_list_models_accepts_plain_string_list(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "lmstudio", "context_window": 4096}
    )
    _install_mock_transport(
        provider,
        lambda r: httpx.Response(200, json={"data": ["mistral-7b"]}),
    )
    models = await provider.list_models()
    assert models[0].id == "mistral-7b"
    assert models[0].context_window == 4096


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_base_url(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(config={"preset": "custom"})
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_degraded_when_models_endpoint_404(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "text-generation-webui"}
    )
    _install_mock_transport(provider, lambda r: httpx.Response(404, text="nope"))
    status = await provider.health_check()
    assert status.level == HealthLevel.DEGRADED


@pytest.mark.asyncio
async def test_auth_scheme_none_omits_authorization(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={"preset": "ollama", "auth_scheme": "none", "api_key": "ignored"}
    )
    client = await provider._ensure_client()
    assert "Authorization" not in client.headers


@pytest.mark.asyncio
async def test_auth_scheme_header_uses_custom_header(openai_compat_module) -> None:
    provider = openai_compat_module.OpenAICompatibleLLMProvider(
        config={
            "preset": "openai",
            "auth_scheme": "header",
            "api_key_header": "x-my-key",
            "api_key": "abc",
        }
    )
    client = await provider._ensure_client()
    assert client.headers.get("x-my-key") == "abc"
    assert "Authorization" not in client.headers
