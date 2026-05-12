"""Tests for the bundled `embed-openai` plugin.

The HTTP layer is mocked with an `httpx.MockTransport` so tests don't
need network access. The fake transport records the request and returns
a canned embeddings response in the same shape OpenAI uses.
"""

from __future__ import annotations

import json

import httpx
import pytest

from grimoire.plugins.discovery import discover
from grimoire.plugins.loader import load_plugin
from grimoire.types.common import HealthLevel
from grimoire.types.plugins import PluginKind

from .conftest import BUNDLED_PLUGINS_ROOT, assert_protocol_attrs


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


def test_manifest_discovers_and_loads() -> None:
    discovered, errors = discover([], bundled_roots=[BUNDLED_PLUGINS_ROOT])
    assert not errors
    target = next(d for d in discovered if d.raw_manifest["id"] == "embed-openai")
    result = load_plugin(target, config={"api_key": "sk-test"})
    assert result.ok, result.errors
    assert result.manifest is not None
    assert PluginKind.EMBEDDING_PROVIDER in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "model_id", "dimensions", "embed", "health_check"],
    )


def test_defaults_match_spec(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk-test"})
    assert provider.id == "embed-openai"
    assert provider.model_id == "text-embedding-3-small"
    # Default model has a known native dimension.
    assert provider.dimensions == 1536


def test_dimensions_override(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(
        config={"api_key": "sk", "model": "text-embedding-3-large", "dimensions": 256}
    )
    assert provider.dimensions == 256
    assert provider._configured_dimensions == 256


@pytest.mark.asyncio
async def test_embed_posts_to_openai(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk-abc"})

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2, 0.3], "index": i} for i, _ in enumerate(body["input"])
                ],
                "model": body["model"],
            },
        )

    requests = _install_mock_transport(provider, _handler)
    vectors = await provider.embed(["a", "b"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert provider.dimensions == 3
    assert len(requests) == 1
    sent = json.loads(requests[0].content.decode("utf-8"))
    assert sent["model"] == "text-embedding-3-small"
    assert sent["input"] == ["a", "b"]
    assert "dimensions" not in sent


@pytest.mark.asyncio
async def test_embed_forwards_dimensions_parameter(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk", "dimensions": 256})
    requests = _install_mock_transport(
        provider,
        lambda r: httpx.Response(200, json={"data": [{"embedding": [0.0] * 256, "index": 0}]}),
    )
    await provider.embed(["x"])
    sent = json.loads(requests[0].content.decode("utf-8"))
    assert sent["dimensions"] == 256


@pytest.mark.asyncio
async def test_embed_raises_on_http_error(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(
        provider, lambda r: httpx.Response(401, json={"error": {"message": "nope"}})
    )
    with pytest.raises(RuntimeError, match="401"):
        await provider.embed(["x"])


@pytest.mark.asyncio
async def test_embed_empty_input_short_circuits(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk"})
    assert await provider.embed([]) == []


@pytest.mark.asyncio
async def test_embed_requires_api_key(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider()
    with pytest.raises(RuntimeError, match="api_key"):
        await provider.embed(["x"])


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_key(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider()
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_healthy_on_success(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(
        provider,
        lambda r: httpx.Response(200, json={"data": [{"embedding": [0.0] * 1536, "index": 0}]}),
    )
    status = await provider.health_check()
    assert status.level == HealthLevel.HEALTHY


@pytest.mark.asyncio
async def test_health_check_unhealthy_on_failure(openai_module) -> None:
    provider = openai_module.OpenAIEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(provider, lambda r: httpx.Response(500, text="boom"))
    status = await provider.health_check()
    assert status.level == HealthLevel.UNHEALTHY
