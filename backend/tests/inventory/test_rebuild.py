import pytest

from grimoire.inventory.models import HolderKind, InventoryEntry
from grimoire.inventory.persistence import InventoryPersistence

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_rebuild_repopulates_holdings_from_files(store, file_watcher):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    p = InventoryPersistence(store)
    await p.write_holder_inventory(
        campaign_id="c1",
        holder_kind=HolderKind.CHARACTER,
        holder_id="joe",
        entries=[InventoryEntry(item_ref="ring", item_name="Ring", quantity=3)],
        source="inventory",
        turn_id=None,
    )
    # Simulate DB loss of the derived rows.
    await store.db.execute("DELETE FROM inventory_holdings")
    assert await store.list_inventory_holdings("c1") == []

    # Rebuild from files.
    await file_watcher.scan_now(scope="campaigns")

    rows = await store.list_inventory_holdings("c1", item_ref="ring")
    assert len(rows) == 1 and rows[0]["quantity"] == 3
