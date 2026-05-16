"""Tests for the bundled `embed-openrouter` plugin.

The HTTP layer is mocked with `httpx.MockTransport` so tests don't need
network access. The fake transport records each request and returns
canned `/embeddings` or `/models` responses in the same shape OpenRouter
serves.
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
    target = next(d for d in discovered if d.raw_manifest["id"] == "embed-openrouter")
    result = load_plugin(target, config={"api_key": "sk-or-test"})
    assert result.ok, result.errors
    assert result.manifest is not None
    assert PluginKind.EMBEDDING_PROVIDER in result.manifest.implements
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "model_id", "dimensions", "embed", "list_models", "health_check"],
    )


def test_defaults_match_manifest(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})
    assert provider.id == "embed-openrouter"
    assert provider.model_id == "openai/text-embedding-3-small"
    # Default model has a known native dimension.
    assert provider.dimensions == 1536


def test_legacy_model_key_still_honored(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(
        config={"api_key": "sk", "model": "openai/text-embedding-3-large"}
    )
    assert provider.model_id == "openai/text-embedding-3-large"
    assert provider.dimensions == 3072


@pytest.mark.asyncio
async def test_embed_posts_to_openrouter(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(
        config={"api_key": "sk", "active_model": "openai/text-embedding-3-small"}
    )

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
    assert requests[0].url.path.endswith("/embeddings")
    sent = json.loads(requests[0].content.decode("utf-8"))
    assert sent["model"] == "openai/text-embedding-3-small"
    assert sent["input"] == ["a", "b"]
    assert "dimensions" not in sent


@pytest.mark.asyncio
async def test_embed_forwards_dimensions_parameter(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(
        config={"api_key": "sk", "dimensions": 256}
    )
    requests = _install_mock_transport(
        provider,
        lambda r: httpx.Response(200, json={"data": [{"embedding": [0.0] * 256, "index": 0}]}),
    )
    await provider.embed(["x"])
    sent = json.loads(requests[0].content.decode("utf-8"))
    assert sent["dimensions"] == 256


@pytest.mark.asyncio
async def test_embed_raises_on_http_error(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(
        provider, lambda r: httpx.Response(401, json={"error": {"message": "nope"}})
    )
    with pytest.raises(RuntimeError, match="401"):
        await provider.embed(["x"])


@pytest.mark.asyncio
async def test_embed_empty_input_short_circuits(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})
    assert await provider.embed([]) == []


@pytest.mark.asyncio
async def test_embed_requires_api_key(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider()
    with pytest.raises(RuntimeError, match="api_key"):
        await provider.embed(["x"])


@pytest.mark.asyncio
async def test_list_models_filters_for_embeddings(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "architecture": {"output_modalities": ["text"]},
                        "pricing": {"prompt": "0.000005", "completion": "0.000015"},
                    },
                    {
                        "id": "openai/text-embedding-3-small",
                        "name": "OpenAI Embed 3 small",
                        "architecture": {"output_modalities": ["embedding"]},
                        "pricing": {"prompt": "0.00000002"},
                    },
                    {
                        "id": "mistralai/mistral-embed",
                        "name": "Mistral Embed",
                        "architecture": {"modality": "text->embedding"},
                        "pricing": {"prompt": "0.0000001"},
                    },
                    {
                        "id": "weird/some-embed-thing",
                        "name": "Weird embed",
                        # Neither architecture field set; falls back to id substring.
                    },
                ]
            },
        )

    _install_mock_transport(provider, _handler)
    models = await provider.list_models()
    ids = {m.id for m in models}
    assert "openai/gpt-4o" not in ids
    assert "openai/text-embedding-3-small" in ids
    assert "mistralai/mistral-embed" in ids
    assert "weird/some-embed-thing" in ids

    by_id = {m.id: m for m in models}
    # Pricing converted from per-token string to per-1k float.
    assert by_id["openai/text-embedding-3-small"].input_cost_per_1k == pytest.approx(0.00002)
    assert by_id["openai/text-embedding-3-small"].dimensions == 1536


@pytest.mark.asyncio
async def test_list_models_falls_back_when_catalog_unreachable(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(provider, lambda r: httpx.Response(503, text="busy"))
    models = await provider.list_models()
    ids = {m.id for m in models}
    # Fallback list kicks in so the picker remains usable.
    assert "openai/text-embedding-3-small" in ids


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_key(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider()
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_healthy_on_success(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(provider, lambda r: httpx.Response(200, json={"data": []}))
    status = await provider.health_check()
    assert status.level == HealthLevel.HEALTHY


@pytest.mark.asyncio
async def test_health_check_unhealthy_on_http_error(embed_openrouter_module) -> None:
    provider = embed_openrouter_module.OpenRouterEmbeddingProvider(config={"api_key": "sk"})
    _install_mock_transport(provider, lambda r: httpx.Response(500, text="boom"))
    status = await provider.health_check()
    assert status.level == HealthLevel.UNHEALTHY
