"""Tests for the SceneLedger service."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.scenes.ledger import SceneLedger
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def ledger(db):
    return SceneLedger(db)


async def test_add_and_list(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="The party investigates the ruins.",
        source="llm",
    )
    items = await ledger.list_active("c1")
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["summary"] == "The party investigates the ruins."
    assert items[0]["source"] == "llm"
    assert items[0]["status"] == "active"


async def test_add_greeting_item(ledger: SceneLedger) -> None:
    await ledger.add(
        campaign_id="c1",
        summary="A quiet morning at the harbor.",
        source="greeting",
        greeting_id="gr-harbor",
    )
    items = await ledger.list_active("c1")
    assert items[0]["greeting_id"] == "gr-harbor"


async def test_dismiss_and_restore(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="Encounter in the forest.",
        source="llm",
    )
    await ledger.set_status(item_id, "dismissed")
    assert len(await ledger.list_active("c1")) == 0

    await ledger.set_status(item_id, "active")
    assert len(await ledger.list_active("c1")) == 1


async def test_mark_used(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="The tavern scene.",
        source="greeting",
        greeting_id="gr-tavern",
    )
    await ledger.mark_used(item_id, scene_id="scene-001")
    items = await ledger.list_all("c1")
    used = [i for i in items if i["status"] == "used"]
    assert len(used) == 1
    assert used[0]["used_in_scene_id"] == "scene-001"


async def test_list_all_returns_every_status(ledger: SceneLedger) -> None:
    id1 = await ledger.add(campaign_id="c1", summary="A", source="llm")
    await ledger.add(campaign_id="c1", summary="B", source="llm")
    id3 = await ledger.add(campaign_id="c1", summary="C", source="llm")
    await ledger.set_status(id1, "dismissed")
    await ledger.mark_used(id3, scene_id="s1")
    items = await ledger.list_all("c1")
    assert len(items) == 3


async def test_populate_from_greetings(ledger: SceneLedger) -> None:
    greetings = [
        {"id": "gr-1", "name": "The Harbor"},
        {"id": "gr-2", "name": "The Camp"},
    ]
    for g in greetings:
        await ledger.add(
            campaign_id="c1",
            summary=g["name"],
            source="greeting",
            greeting_id=g["id"],
        )
    items = await ledger.list_active("c1")
    assert len(items) == 2
    assert all(i["source"] == "greeting" for i in items)


async def test_campaign_isolation(ledger: SceneLedger) -> None:
    await ledger.add(campaign_id="c1", summary="A", source="llm")
    await ledger.add(campaign_id="c2", summary="B", source="llm")
    assert len(await ledger.list_active("c1")) == 1
    assert len(await ledger.list_active("c2")) == 1


async def test_get_returns_item(ledger: SceneLedger) -> None:
    item_id = await ledger.add(campaign_id="c1", summary="Test", source="llm")
    item = await ledger.get(item_id)
    assert item is not None
    assert item["summary"] == "Test"


async def test_get_returns_none_for_missing(ledger: SceneLedger) -> None:
    assert await ledger.get("nonexistent") is None
