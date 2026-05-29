"""Scene Manager queue/confirm/dismiss cast-change methods (#464)."""

from __future__ import annotations

import pytest

from grimoire.scenes.cast_changes import CastChangeStore
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneInit
from grimoire.storage import apply_migrations
from grimoire.storage.db import Database
from grimoire.types.scene import CastChange


@pytest.fixture
async def manager(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    await db.connect()
    await apply_migrations(db)
    mgr = SceneManager(tmp_path, cast_change_store=CastChangeStore(db))
    yield mgr
    await db.close()


async def test_queue_and_confirm_npc_enter(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id,
        character_ref="library:worlds/w/characters/reyes",
        change=CastChange.ENTER,
        is_pc=False,
        evidence="strides in",
        confidence=0.8,
        turn_id="t1",
    )
    assert len(await manager.list_pending_cast_changes(scene.id)) == 1

    await manager.confirm_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "library:worlds/w/characters/reyes" in updated.present_character_refs
    assert await manager.list_pending_cast_changes(scene.id) == []


async def test_dismiss_does_not_touch_cast(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id, character_ref="x", change=CastChange.ENTER, is_pc=False
    )
    await manager.dismiss_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "x" not in updated.present_character_refs
    assert await manager.list_pending_cast_changes(scene.id) == []


async def test_confirm_pc_enter_uses_add_present_pc(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id,
        character_ref="campaign:emergent/character/hero",
        change=CastChange.ENTER,
        is_pc=True,
    )
    await manager.confirm_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "campaign:emergent/character/hero" in updated.present_pc_refs


async def test_confirm_leave_removes_character(manager):
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="t", present_character_refs=["npc:guard"])
    )
    cid = await manager.queue_cast_change(
        scene.id, character_ref="npc:guard", change=CastChange.LEAVE, is_pc=False
    )
    await manager.confirm_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "npc:guard" not in updated.present_character_refs


async def test_confirm_twice_raises(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id, character_ref="x", change=CastChange.ENTER, is_pc=False
    )
    await manager.confirm_cast_change(scene.id, cid)
    with pytest.raises(ValueError):
        await manager.confirm_cast_change(scene.id, cid)


async def test_confirm_unknown_id_raises(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    with pytest.raises(KeyError):
        await manager.confirm_cast_change(scene.id, "cc-nope")


async def test_dismiss_after_confirm_raises(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id, character_ref="x", change=CastChange.ENTER, is_pc=False
    )
    await manager.confirm_cast_change(scene.id, cid)
    # Dismissing an already-confirmed change must not silently overwrite status.
    with pytest.raises(ValueError):
        await manager.dismiss_cast_change(scene.id, cid)


async def test_queue_dedupes_identical_pending(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    first = await manager.queue_cast_change(
        scene.id, character_ref="npc:guard", change=CastChange.ENTER, is_pc=False
    )
    second = await manager.queue_cast_change(
        scene.id, character_ref="npc:guard", change=CastChange.ENTER, is_pc=False
    )
    assert first == second
    assert len(await manager.list_pending_cast_changes(scene.id)) == 1

    # A different change (leave) for the same character is a distinct row.
    third = await manager.queue_cast_change(
        scene.id, character_ref="npc:guard", change=CastChange.LEAVE, is_pc=False
    )
    assert third != first
    assert len(await manager.list_pending_cast_changes(scene.id)) == 2
