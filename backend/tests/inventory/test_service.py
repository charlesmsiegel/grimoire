import pytest

from grimoire.event_bus import EventBus
from grimoire.inventory import events as inv_events
from grimoire.inventory.models import InventoryAction, InventoryOperation
from grimoire.inventory.service import InventoryService

pytestmark = pytest.mark.asyncio


async def _enable(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.set_campaign_config("c1", {"inventory": {"enabled": True, "flag_threshold": 0.6}})


async def test_disabled_campaign_is_noop(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE, item="ring", holder="joe", confidence=1.0
    )
    res = await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    assert res is None  # disabled -> no-op
    assert await store.list_inventory_holdings("c1") == []


async def test_acquire_persists_and_emits(store):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    bus = EventBus()
    seen = []
    bus.subscribe(inv_events.INVENTORY_CHANGED, lambda e: seen.append(e))
    svc = InventoryService(store=store, event_bus=bus)
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE,
        item="silver ring",
        holder="joe",
        quantity=1,
        confidence=0.95,
    )
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert rows and rows[0]["item_ref"] == "silver-ring"
    assert len(seen) == 1


async def test_low_confidence_records_flag(store):
    await _enable(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(
        action=InventoryAction.ACQUIRE,
        item="rusty key",
        holder="joe",
        confidence=0.2,
    )
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    flags = await store.list_inventory_flags("c1", resolved=False)
    assert len(flags) == 1
    assert flags[0]["flag_reason"] == "low_confidence"
