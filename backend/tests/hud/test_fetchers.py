"""HUD fetcher helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hud.fetchers import _resolve_character_name
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


async def test_resolve_character_name_resolves_emergent_character(store: StateStore) -> None:
    """An ``emergent/<id>`` cast ref must resolve to the emergent character's
    display name.

    Regression: ``_resolve_character_name`` resolved campaign-scoped refs only
    via ``list_for_composition`` (world/library characters), so emergent PCs and
    NPCs showed their raw asset_id in the HUD cast instead of their name.
    """
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="shia-zecil",
        frontmatter={"id": "shia-zecil", "name": "Shia Zecil", "role": "pc"},
        body="An emergent player character.",
        source="test",
    )
    library = LibraryService(store)

    name = await _resolve_character_name(library, None, "shia-zecil", "c1")
    assert name == "Shia Zecil"


async def test_resolve_character_name_falls_back_to_asset_id(store: StateStore) -> None:
    """Unknown refs still degrade gracefully to the asset_id."""
    library = LibraryService(store)
    name = await _resolve_character_name(library, None, "nobody", "c1")
    assert name == "nobody"
