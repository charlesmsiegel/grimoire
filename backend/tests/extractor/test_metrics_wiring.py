"""Verify ExtractorService threads metrics through extract() (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ex.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_extract_records_metric(db):
    from grimoire.extractor.service import ExtractorService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    svc = ExtractorService.__new__(ExtractorService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    svc._extract_inner = MethodType(_inner, svc)
    svc.extract = MethodType(ExtractorService.extract, svc)

    await svc.extract("resp", object(), "c1", object())  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'extractor' AND metric = 'extract'"
    )
    assert len(rows) == 1
