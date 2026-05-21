"""Verify StateStore threads metrics through writes and reads (#355)."""

from __future__ import annotations

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ss.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def metrics(db):
    return MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )


@pytest.mark.asyncio
async def test_txn_records_state_store_write_metric(db, metrics, tmp_path):
    store = StateStore(db=db, data_root=tmp_path, metrics=metrics)
    async with store._txn() as conn:
        # Body intentionally trivial — the test only checks that the
        # wrapping `measure()` recorded a write row.
        await conn.execute("SELECT 1")
    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'state_store' AND metric = 'write'"
    )
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_list_scenes_records_query_metric(db, metrics, tmp_path):
    store = StateStore(db=db, data_root=tmp_path, metrics=metrics)
    await store.list_scenes(campaign_id="c1")
    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'state_store' AND metric = 'query'"
    )
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_list_world_refs_records_query_metric(db, metrics, tmp_path):
    store = StateStore(db=db, data_root=tmp_path, metrics=metrics)
    await store.list_world_refs(campaign_id="c1")
    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'state_store' AND metric = 'query'"
    )
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_list_pcs_records_query_metric(db, metrics, tmp_path):
    store = StateStore(db=db, data_root=tmp_path, metrics=metrics)
    await store.list_pcs(campaign_id="c1")
    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'state_store' AND metric = 'query'"
    )
    assert len(rows) >= 1
