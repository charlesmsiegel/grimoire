"""Verify TimeEngineService threads metrics through advance() (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "te.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_time_engine_advance_records_metric(db):
    from grimoire.time_engine.service import TimeEngineService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    te = TimeEngineService.__new__(TimeEngineService)
    te._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    te._advance_inner = MethodType(_inner, te)
    te.advance = MethodType(TimeEngineService.advance, te)

    await te.advance("camp1", None, None)  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'time_engine' AND metric = 'advance'"
    )
    assert len(rows) == 1
