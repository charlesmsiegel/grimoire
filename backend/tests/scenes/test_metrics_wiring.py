"""Verify SceneManager threads metrics through on_advance_requested() (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path):
    database = Database(stamp_migrated_db(tmp_path / "sc.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scene_manager_advance_records_metric(db):
    from grimoire.scenes.manager import SceneManager

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    sm = SceneManager.__new__(SceneManager)
    sm._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    sm._on_advance_requested_inner = MethodType(_inner, sm)
    sm.on_advance_requested = MethodType(SceneManager.on_advance_requested, sm)

    await sm.on_advance_requested("scene1")

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'scene_manager' AND metric = 'scene_resolve'"
    )
    assert len(rows) == 1
