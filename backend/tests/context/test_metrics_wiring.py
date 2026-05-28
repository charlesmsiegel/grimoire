"""Verify ContextBuilderService threads metrics through `build()` (#355)."""

from __future__ import annotations

import json
from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path):
    database = Database(stamp_migrated_db(tmp_path / "ctx.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_context_builder_build_records_metric(db):
    from grimoire.context.builder import ContextBuilderService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    svc = ContextBuilderService.__new__(ContextBuilderService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    svc._build_inner = MethodType(_inner, svc)
    svc.build = MethodType(ContextBuilderService.build, svc)

    await svc.build("hi", "camp1")  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric, labels FROM metric_samples "
        "WHERE module = 'context_builder' AND metric = 'build'"
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["labels"])["success"] is True
