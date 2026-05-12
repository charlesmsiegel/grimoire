"""Tests for the bundled `embed-sentence-transformers` plugin.

The real `sentence-transformers` library is heavy and pulls in torch, so
the tests stub the loader: `_load_model_blocking` is replaced with a
fake `SentenceTransformer`-shaped object that records the calls. This
keeps the test suite fast and dependency-free while still exercising
the threading wrapper and dimension wiring.
"""

from __future__ import annotations

import pytest

from grimoire.plugins.discovery import discover
from grimoire.plugins.loader import load_plugin
from grimoire.types.common import HealthLevel
from grimoire.types.plugins import PluginKind

from .conftest import BUNDLED_PLUGINS_ROOT, assert_protocol_attrs


class _FakeModel:
    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.calls: list[dict] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts, **kwargs):
        self.calls.append({"texts": list(texts), **kwargs})
        return [[float(i) for i in range(self._dim)] for _ in texts]


def test_manifest_discovers_and_loads() -> None:
    discovered, errors = discover([], bundled_roots=[BUNDLED_PLUGINS_ROOT])
    assert not errors
    ids = [d.raw_manifest["id"] for d in discovered]
    assert "embed-sentence-transformers" in ids
    target = next(d for d in discovered if d.raw_manifest["id"] == "embed-sentence-transformers")
    result = load_plugin(target)
    assert result.ok, result.errors
    assert result.manifest is not None
    assert result.manifest.id == "embed-sentence-transformers"
    assert PluginKind.EMBEDDING_PROVIDER in result.manifest.implements
    assert len(result.instances) == 1
    instance = result.instances[0].instance
    assert_protocol_attrs(
        instance,
        ["id", "name", "model_id", "dimensions", "embed", "health_check"],
    )


def test_defaults_match_spec(st_module) -> None:
    provider = st_module.SentenceTransformersEmbeddingProvider()
    assert provider.id == "embed-sentence-transformers"
    assert provider.model_id == "sentence-transformers/all-mpnet-base-v2"
    assert provider.dimensions == 0  # filled in after model load


def test_config_overrides_model(st_module) -> None:
    provider = st_module.SentenceTransformersEmbeddingProvider(
        config={"model": "BAAI/bge-small-en", "batch_size": 8, "normalize": False}
    )
    assert provider.model_id == "BAAI/bge-small-en"
    assert provider._batch_size == 8
    assert provider._normalize is False


@pytest.mark.asyncio
async def test_embed_returns_vectors_and_sets_dimensions(monkeypatch, st_module) -> None:
    fake = _FakeModel(dim=4)
    provider = st_module.SentenceTransformersEmbeddingProvider()
    monkeypatch.setattr(provider, "_load_model_blocking", lambda: fake)

    vectors = await provider.embed(["hello", "world"])

    assert len(vectors) == 2
    assert all(len(v) == 4 for v in vectors)
    assert provider.dimensions == 4
    assert fake.calls and fake.calls[0]["normalize_embeddings"] is True


@pytest.mark.asyncio
async def test_embed_empty_input_short_circuits(st_module) -> None:
    provider = st_module.SentenceTransformersEmbeddingProvider()
    assert await provider.embed([]) == []


@pytest.mark.asyncio
async def test_health_check_unconfigured_when_library_missing(monkeypatch, st_module) -> None:
    provider = st_module.SentenceTransformersEmbeddingProvider()

    def _missing() -> None:
        raise ModuleNotFoundError(name="sentence_transformers")

    monkeypatch.setattr(provider, "_load_model_blocking", _missing)

    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED
    assert "sentence-transformers" in status.message


@pytest.mark.asyncio
async def test_health_check_healthy_after_load(monkeypatch, st_module) -> None:
    fake = _FakeModel(dim=8)
    provider = st_module.SentenceTransformersEmbeddingProvider()
    monkeypatch.setattr(provider, "_load_model_blocking", lambda: fake)

    status = await provider.health_check()
    assert status.level == HealthLevel.HEALTHY
    assert provider.dimensions == 8


@pytest.mark.asyncio
async def test_model_loaded_once(monkeypatch, st_module) -> None:
    calls = {"n": 0}

    def _load() -> _FakeModel:
        calls["n"] += 1
        return _FakeModel()

    provider = st_module.SentenceTransformersEmbeddingProvider()
    monkeypatch.setattr(provider, "_load_model_blocking", _load)

    await provider.embed(["a"])
    await provider.embed(["b"])
    assert calls["n"] == 1
