"""Tests for MetricsRegistry.measure() context manager (#355)."""

from __future__ import annotations

import asyncio
import json

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
    return MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))


async def _rows(db):
    return await db.fetchall("SELECT module, metric, value, labels FROM metric_samples")


@pytest.mark.asyncio
async def test_measure_records_success_on_clean_exit(registry, db):
    async with registry.measure("orchestrator", "turn"):
        await asyncio.sleep(0.001)

    rows = await _rows(db)
    assert len(rows) == 1
    assert rows[0]["module"] == "orchestrator"
    assert rows[0]["metric"] == "turn"
    assert float(rows[0]["value"]) >= 0.5  # at least 0.5ms slept
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is True


@pytest.mark.asyncio
async def test_measure_records_failure_and_reraises(registry, db):
    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with registry.measure("llm_gateway", "complete"):
            raise _Boom("nope")

    rows = await _rows(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is False


@pytest.mark.asyncio
async def test_measure_records_failure_on_cancellation(registry, db):
    async def _cancelled():
        async with registry.measure("orchestrator", "turn"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _cancelled()

    rows = await _rows(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is False


@pytest.mark.asyncio
async def test_measure_noop_when_disabled(db):
    registry = MetricsRegistry(db, config=MetricsConfig(enabled=False))
    async with registry.measure("orchestrator", "turn"):
        pass
    rows = await _rows(db)
    assert rows == []


@pytest.mark.asyncio
async def test_measure_swallows_record_failure(registry, db, monkeypatch, caplog):
    async def _broken(*_a, **_kw):
        raise RuntimeError("db dead")

    monkeypatch.setattr(registry, "record", _broken)

    # Caller still completes — measure() doesn't propagate the record failure.
    async with registry.measure("orchestrator", "turn"):
        pass
    assert "metrics.measure: record failed" in caplog.text


@pytest.mark.asyncio
async def test_measure_passes_labels_through(registry, db):
    async with registry.measure(
        "llm_gateway", "complete", labels={"provider": "anthropic", "model": "opus"}
    ):
        pass
    rows = await _rows(db)
    payload = json.loads(rows[0]["labels"])
    assert payload["labels"] == {"provider": "anthropic", "model": "opus"}
