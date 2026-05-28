"""Fixtures: Database + StateStore + TransientStateService per test."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
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
