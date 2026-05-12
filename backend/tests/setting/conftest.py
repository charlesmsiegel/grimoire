"""Fixtures for Setting service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.library import LibraryService
from grimoire.setting import SettingService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
async def library(store: StateStore) -> LibraryService:
    return LibraryService(store)


@pytest.fixture
async def setting(library: LibraryService) -> SettingService:
    return SettingService(library)
