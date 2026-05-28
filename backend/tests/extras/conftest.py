"""Fixtures for ExtrasService tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.extras import ExtrasMirror, ExtrasService
from grimoire.library import LibraryService
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
async def extras(library: LibraryService, store: StateStore) -> ExtrasService:
    return ExtrasService(library=library, store=store, mirror=ExtrasMirror(store.db))


async def _seed_world(store: StateStore, world_id: str) -> None:
    await store.write_library_file(
        library_id=f"worlds/{world_id}/world",
        frontmatter={"id": world_id, "name": world_id, "version": 1},
        body="",
        source="user",
    )


async def _seed_character(
    store: StateStore,
    world_id: str,
    asset_id: str,
    *,
    extras: dict | None = None,
) -> None:
    fm = {"id": asset_id, "name": asset_id.replace("-", " ").title(), "tags": []}
    if extras is not None:
        fm["extras"] = extras
    await store.write_library_file(
        library_id=f"worlds/{world_id}/characters/{asset_id}",
        frontmatter=fm,
        body=f"# {asset_id}",
        source="user",
    )


@pytest.fixture
def seed_world():
    return _seed_world


@pytest.fixture
def seed_character():
    return _seed_character
