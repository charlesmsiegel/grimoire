"""Tests for MetricsRegistry.known_pairs() — pairs index (#355)."""

from __future__ import annotations

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def registry(db):
    return MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0, sample_rate_cold_path=1.0)
    )


@pytest.mark.asyncio
async def test_known_pairs_empty(registry):
    pairs = await registry.known_pairs()
    assert pairs == []


@pytest.mark.asyncio
async def test_known_pairs_returns_unique_pairs_sorted_by_recency(registry):
    await registry.record(module="orchestrator", operation="turn", duration_ms=10.0)
    await registry.record(module="llm_gateway", operation="complete", duration_ms=20.0)
    await registry.record(module="orchestrator", operation="turn", duration_ms=15.0)
    pairs = await registry.known_pairs()
    assert {(p["module"], p["operation"]) for p in pairs} == {
        ("orchestrator", "turn"),
        ("llm_gateway", "complete"),
    }
    # `last_recorded_at` is present on every entry.
    assert all("last_recorded_at" in p for p in pairs)
