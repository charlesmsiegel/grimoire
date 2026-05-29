"""Fixtures for the inventory tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.watcher.watcher import FileWatcher


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
def file_watcher(store):
    return FileWatcher(data_root=store.data_root, store=store, bus=EventBus())
