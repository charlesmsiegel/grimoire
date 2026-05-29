import pytest

from grimoire.inventory.models import HolderKind, InventoryEntry
from grimoire.inventory.persistence import InventoryPersistence

pytestmark = pytest.mark.asyncio


async def test_emergent_holder_roundtrip(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    # Seed an emergent character holder.
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"}, body="barkeep", source="test",
    )
    p = InventoryPersistence(store)
    entries = [InventoryEntry(item_ref="ring", item_name="Ring", quantity=1)]
    await p.write_holder_inventory(
        campaign_id="c1", holder_kind=HolderKind.CHARACTER, holder_id="joe",
        entries=entries, source="inventory", turn_id="t1",
    )

    # File SSOT updated.
    doc = await store.get_emergent("c1", "character", "joe")
    assert doc["frontmatter"]["inventory"]["entries"][0]["item_ref"] == "ring"

    # Derived table synced.
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="joe")
    assert len(rows) == 1 and rows[0]["item_ref"] == "ring"

    # Round-trip read.
    read = await p.read_holder_inventory("c1", HolderKind.CHARACTER, "joe")
    assert read[0].item_ref == "ring"
