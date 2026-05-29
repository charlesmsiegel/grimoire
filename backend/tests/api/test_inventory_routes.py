"""REST routes for the inventory subsystem (#444)."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.event_bus import EventBus
from grimoire.inventory import InventoryService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


async def _build(container: ServiceContainer, tmp_path: Path, *, enabled: bool):
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    db = Database(tmp_path / "inv.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    store = StateStore(db, data_root)
    await store.upsert_campaign(campaign_id="c_test", name="t")
    if enabled:
        await store.set_campaign_config("c_test", {"inventory": {"enabled": True}})
        await store.write_emergent(
            campaign_id="c_test",
            kind="character",
            entity_id="joe",
            frontmatter={"id": "joe", "name": "Joe"},
            body="",
            source="test",
        )
    container.state_store = store
    container.inventory = InventoryService(store=store, event_bus=EventBus())
    return db


@pytest.fixture
async def disabled_container(container: ServiceContainer, tmp_path: Path):
    db = await _build(container, tmp_path, enabled=False)
    try:
        yield container
    finally:
        with suppress(Exception):
            await db.close()


@pytest.fixture
async def enabled_container(container: ServiceContainer, tmp_path: Path):
    db = await _build(container, tmp_path, enabled=True)
    try:
        yield container
    finally:
        with suppress(Exception):
            await db.close()


def test_get_inventory_disabled_returns_409(client: TestClient, disabled_container):
    r = client.get("/api/campaigns/c_test/inventory")
    assert r.status_code == 409


def test_enabled_operation_and_listing(client: TestClient, enabled_container):
    r = client.post(
        "/api/campaigns/c_test/inventory/operations",
        json={"action": "acquire", "item": "120 gold", "holder": "joe", "confidence": 1.0},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/campaigns/c_test/inventory")
    assert r.status_code == 200
    body = r.json()
    assert "holders" in body
    refs = [e["item_ref"] for h in body["holders"] for e in h["entries"]]
    assert "resource:gold" in refs
