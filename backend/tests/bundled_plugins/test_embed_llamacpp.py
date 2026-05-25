"""Tests for the bundled `embed-llamacpp` plugin.

The real llama-cpp-python library is a heavy C extension, so the tests
stub the model loader: `_get_llama` is replaced with a fake Llama-shaped
object. This keeps the suite fast and dependency-free while exercising
the threading wrapper, normalization, and dimension wiring.
"""

from __future__ import annotations

import math

import pytest

from grimoire.plugins.discovery import discover
from grimoire.plugins.loader import load_plugin
from grimoire.types.common import HealthLevel
from grimoire.types.plugins import PluginKind

from .conftest import BUNDLED_PLUGINS_ROOT, assert_protocol_attrs


class _FakeLlama:
    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i + 1) for i in range(self._dim)] for _ in texts]


def test_manifest_discovers_and_loads() -> None:
    discovered, errors = discover([], bundled_roots=[BUNDLED_PLUGINS_ROOT])
    assert not errors
    ids = [d.raw_manifest["id"] for d in discovered]
    assert "embed-llamacpp" in ids
    target = next(d for d in discovered if d.raw_manifest["id"] == "embed-llamacpp")
    result = load_plugin(target)
    assert result.ok, result.errors
    assert result.manifest is not None
    assert result.manifest.id == "embed-llamacpp"
    assert PluginKind.EMBEDDING_PROVIDER in result.manifest.implements
    assert len(result.instances) == 1
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "model_id", "dimensions", "embed", "list_models", "health_check"],
    )


def test_defaults(embed_llamacpp_module) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider()
    assert provider.id == "embed-llamacpp"
    assert provider.model_id == "local-gguf"
    assert provider.dimensions == 0
    assert provider.max_batch_size == 32


def test_model_id_from_path(embed_llamacpp_module) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": "/models/nomic-embed-text-v2-moe.Q4_K_M.gguf"}
    )
    assert provider.model_id == "nomic-embed-text-v2-moe.Q4_K_M"


def test_model_id_override(embed_llamacpp_module) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={
            "model_path": "/models/some-model.gguf",
            "model_id": "my-custom-embedder",
        }
    )
    assert provider.model_id == "my-custom-embedder"


@pytest.mark.asyncio
async def test_embed_returns_normalized_vectors(monkeypatch, embed_llamacpp_module) -> None:
    fake = _FakeLlama(dim=4)
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": "/fake/model.gguf"}
    )
    monkeypatch.setattr(provider, "_get_llama", lambda: fake)

    vectors = await provider.embed(["hello", "world"])

    assert len(vectors) == 2
    assert all(len(v) == 4 for v in vectors)
    assert provider.dimensions == 4
    for vec in vectors:
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_embed_without_normalization(monkeypatch, embed_llamacpp_module) -> None:
    fake = _FakeLlama(dim=3)
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": "/fake/model.gguf", "normalize": False}
    )
    monkeypatch.setattr(provider, "_get_llama", lambda: fake)

    vectors = await provider.embed(["test"])

    assert vectors == [[1.0, 2.0, 3.0]]


@pytest.mark.asyncio
async def test_embed_empty_input(embed_llamacpp_module) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider()
    assert await provider.embed([]) == []


@pytest.mark.asyncio
async def test_health_check_unconfigured(embed_llamacpp_module) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider()
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_missing_file(embed_llamacpp_module, tmp_path) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": str(tmp_path / "nonexistent.gguf")}
    )
    status = await provider.health_check()
    assert status.level == HealthLevel.UNHEALTHY
    assert "not found" in status.message


@pytest.mark.asyncio
async def test_health_check_healthy_before_load(
    embed_llamacpp_module, tmp_path, monkeypatch
) -> None:
    model_file = tmp_path / "test.gguf"
    model_file.write_bytes(b"fake")
    monkeypatch.setitem(__import__("sys").modules, "llama_cpp", type("fake", (), {}))
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": str(model_file)}
    )
    status = await provider.health_check()
    assert status.level == HealthLevel.HEALTHY
    assert "lazy load" in status.message


@pytest.mark.asyncio
async def test_list_models(embed_llamacpp_module) -> None:
    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": "/fake/model.gguf", "model_id": "test-model"}
    )
    models = await provider.list_models()
    assert len(models) == 1
    assert models[0].id == "test-model"


@pytest.mark.asyncio
async def test_model_loaded_once(monkeypatch, embed_llamacpp_module) -> None:
    calls = {"n": 0}
    fake = _FakeLlama()

    def _counting_get(self):
        calls["n"] += 1
        return fake

    provider = embed_llamacpp_module.LlamaCppEmbeddingProvider(
        config={"model_path": "/fake/model.gguf"}
    )
    monkeypatch.setattr(provider, "_get_llama", lambda: _counting_get(provider))

    await provider.embed(["a"])
    await provider.embed(["b"])
    assert calls["n"] == 2  # _get_llama called each time, but real Llama init'd once via lock
