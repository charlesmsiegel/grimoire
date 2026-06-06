import logging

import pytest

from grimoire.inventory.models import HolderKind, InventoryEntry
from grimoire.inventory.persistence import InventoryPersistence
from grimoire.state_store.paths import emergent_path

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


async def test_rebuild_clears_rows_when_section_removed(store, file_watcher):
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
        entries=[InventoryEntry(item_ref="ring", item_name="Ring", quantity=1)],
        source="inventory",
        turn_id=None,
    )
    await file_watcher.scan_now(scope="campaigns")
    assert await store.list_inventory_holdings("c1", holder_id="joe", holder_kind="character")

    # Remove the inventory section from the file (rewrite frontmatter without it).
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="joe",
        frontmatter={"id": "joe", "name": "Joe"},
        body="",
        source="test",
    )
    # A full rescan must drop the now-orphaned holding rows.
    await file_watcher.scan_now(scope="campaigns")
    assert await store.list_inventory_holdings("c1", holder_id="joe", holder_kind="character") == []


async def test_rebuild_logs_and_skips_malformed_holder_file(store, caplog):
    """A holder file that fails to parse is logged and skipped without aborting
    the rebuild, and the skip is observable via the returned count (#553)."""
    await store.upsert_campaign(campaign_id="c1", name="C")
    # A valid holder with inventory.
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
    # A second holder file with malformed YAML frontmatter (unterminated quote).
    bad = emergent_path(store.data_root, "c1", "character", "broken")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('---\nid: broken\nname: "unterminated\n---\nbody\n', encoding="utf-8")

    await store.db.execute("DELETE FROM inventory_holdings")
    with caplog.at_level(logging.WARNING, logger="grimoire.state_store.store"):
        skipped = await store.rebuild_inventory_holdings_from_files()

    # The valid holder is still indexed despite the malformed neighbour.
    rows = await store.list_inventory_holdings("c1", item_ref="ring")
    assert len(rows) == 1 and rows[0]["quantity"] == 3
    # The skip is observable: counted and logged with the file path + exception.
    assert skipped == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("broken" in r.getMessage() and r.exc_info for r in warnings)
