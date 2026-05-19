"""Fixtures: Database + StateStore + TransientStateService per test."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    try:
        yield StateStore(db, data_root)
    finally:
        await db.close()


@pytest.fixture
def service(store: StateStore) -> TransientStateService:
    return TransientStateService(store, config=TransientStateConfig())


@pytest.fixture
async def seeded_campaign(store: StateStore) -> str:
    await store.upsert_campaign(
        campaign_id="c_test",
        name="Test campaign",
    )
    return "c_test"
