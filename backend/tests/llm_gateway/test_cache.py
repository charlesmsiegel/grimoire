"""Tests for the embedding cache."""

from __future__ import annotations

from grimoire.llm_gateway.cache import EmbeddingCache


async def test_set_and_get_roundtrip(db) -> None:
    cache = EmbeddingCache(db)
    await cache.set_many([("hello", [0.1, 0.2, 0.3])], "model-a")
    got = await cache.get("hello", "model-a")
    assert got is not None
    assert len(got) == 3
    assert got[0] == 0.10000000149011612 or got[0] == 0.1 or abs(got[0] - 0.1) < 1e-6


async def test_get_returns_none_on_miss(db) -> None:
    cache = EmbeddingCache(db)
    assert await cache.get("nope", "model-a") is None


async def test_get_keyed_by_model(db) -> None:
    cache = EmbeddingCache(db)
    await cache.set_many([("hello", [1.0, 2.0])], "model-a")
    assert await cache.get("hello", "model-b") is None


async def test_get_many_returns_only_hits(db) -> None:
    cache = EmbeddingCache(db)
    await cache.set_many(
        [("alpha", [1.0]), ("beta", [2.0])],
        "model-a",
    )
    hits = await cache.get_many(["alpha", "gamma", "beta"], "model-a")
    assert set(hits) == {"alpha", "beta"}
    assert abs(hits["alpha"][0] - 1.0) < 1e-6
    assert abs(hits["beta"][0] - 2.0) < 1e-6


async def test_eviction_drops_oldest_entries(db) -> None:
    cache = EmbeddingCache(db, max_entries=3)
    for i in range(5):
        await cache.set_many([(f"t{i}", [float(i)])], "m")
    assert await cache.count() == 3
    # First two entries should have been evicted.
    assert await cache.get("t0", "m") is None
    assert await cache.get("t1", "m") is None
    assert await cache.get("t4", "m") is not None


async def test_clear_empties_cache(db) -> None:
    cache = EmbeddingCache(db)
    await cache.set_many([("x", [1.0])], "m")
    await cache.clear()
    assert await cache.count() == 0
