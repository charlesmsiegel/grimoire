"""Verify ImageGenService threads metrics through both entry points (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path):
    database = Database(stamp_migrated_db(tmp_path / "ig.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_generate_sync_records_metric(db):
    from grimoire.imagegen.service import ImageGenService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    svc = ImageGenService.__new__(ImageGenService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    svc._generate_sync_inner = MethodType(_inner, svc)
    svc.generate_sync = MethodType(ImageGenService.generate_sync, svc)

    await svc.generate_sync("c1", object())  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'imagegen' AND metric = 'generate'"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_queue_generation_records_metric(db):
    from grimoire.imagegen.service import ImageGenService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    svc = ImageGenService.__new__(ImageGenService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return "job-id"

    svc._queue_generation_inner = MethodType(_inner, svc)
    svc.queue_generation = MethodType(ImageGenService.queue_generation, svc)

    await svc.queue_generation("c1", None, None)  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'imagegen' AND metric = 'generate'"
    )
    assert len(rows) == 1
