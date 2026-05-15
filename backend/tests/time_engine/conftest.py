"""Shared fixtures for Time Engine tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.characters import CharactersService
from grimoire.continuity import ContinuityService
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.time_engine import TimeEngineService
from grimoire.world import WorldService


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
async def world(library: LibraryService) -> WorldService:
    return WorldService(library)


@pytest.fixture
async def mechanics(store: StateStore, tmp_path: Path) -> MechanicsService:
    mech_root = tmp_path / "mechanics"
    mech_root.mkdir()
    config = MechanicsConfig(root=mech_root)
    return MechanicsService(config=config, state_store=store)


@pytest.fixture
async def characters(library: LibraryService, mechanics: MechanicsService) -> CharactersService:
    return CharactersService(library, mechanics)


@pytest.fixture
def continuity() -> ContinuityService:
    return ContinuityService()


@pytest.fixture
async def time_engine(
    store: StateStore,
    world: WorldService,
    characters: CharactersService,
    mechanics: MechanicsService,
    continuity: ContinuityService,
) -> TimeEngineService:
    return TimeEngineService(
        store=store,
        world=world,
        characters=characters,
        mechanics=mechanics,
        continuity=continuity,
    )
