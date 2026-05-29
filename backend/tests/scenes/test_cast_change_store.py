"""Tests for the pending-cast-changes migration + CastChangeStore (#464)."""

from __future__ import annotations

import pytest

from grimoire.scenes.cast_changes import CastChangeStore
from grimoire.storage import apply_migrations
from grimoire.storage.db import Database
from grimoire.types.scene import CastChange


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.sqlite")
    await database.connect()
    await apply_migrations(database)
    yield database
    await database.close()


async def test_pending_cast_changes_table_exists(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_cast_changes'"
    )
    assert len(rows) == 1


async def test_store_add_list_get_set_status(db):
    store = CastChangeStore(db)
    cid = await store.add(
        campaign_id="c",
        scene_id="s",
        character_ref="library:worlds/w/characters/reyes",
        change=CastChange.ENTER,
        is_pc=False,
        evidence="strides in",
        confidence=0.8,
        turn_id="t1",
    )
    pending = await store.list_pending("s")
    assert len(pending) == 1
    assert pending[0].character_ref.endswith("reyes")
    assert pending[0].is_pc is False
    assert pending[0].change == "enter"

    rec = await store.get(cid)
    assert rec is not None and rec.change == "enter"

    await store.set_status(cid, "confirmed")
    assert await store.list_pending("s") == []


async def test_store_scopes_pending_by_scene(db):
    store = CastChangeStore(db)
    await store.add(campaign_id="c", scene_id="s1", character_ref="a", change=CastChange.ENTER, is_pc=False)
    await store.add(campaign_id="c", scene_id="s2", character_ref="b", change=CastChange.LEAVE, is_pc=True)
    assert len(await store.list_pending("s1")) == 1
    assert len(await store.list_pending("s2")) == 1
