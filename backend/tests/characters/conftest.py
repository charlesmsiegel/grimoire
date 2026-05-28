"""Fixtures for Characters service tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.characters import CharactersService
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


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
async def library(store: StateStore) -> LibraryService:
    return LibraryService(store)


@pytest.fixture
async def mechanics(store: StateStore, tmp_path: Path) -> MechanicsService:
    mech_root = tmp_path / "mechanics"
    mech_root.mkdir()
    config = MechanicsConfig(root=mech_root)
    return MechanicsService(config=config, state_store=store)


@pytest.fixture
async def characters(library: LibraryService, mechanics: MechanicsService) -> CharactersService:
    return CharactersService(library, mechanics)
