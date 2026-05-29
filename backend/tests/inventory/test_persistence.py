import pytest

pytestmark = pytest.mark.asyncio


async def test_inventory_tables_exist(store):
    rows = await store.db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('inventory_holdings', 'inventory_flags')"
    )
    names = {r["name"] for r in rows}
    assert names == {"inventory_holdings", "inventory_flags"}


async def test_inventory_holdings_has_no_branch_id(store):
    cols = await store.db.fetchall("PRAGMA table_info(inventory_holdings)")
    names = {c["name"] for c in cols}
    assert "branch_id" not in names
    assert {"campaign_id", "holder_kind", "holder_id", "item_ref", "quantity"} <= names


async def test_upsert_and_list_holdings(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.upsert_inventory_holding(
        campaign_id="c1",
        holder_kind="character",
        holder_id="flo",
        item_ref="ring",
        item_name="Ring",
        quantity=2,
        fungible=False,
        equipped=False,
        provenance=None,
        notes=None,
    )
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="flo")
    assert len(rows) == 1
    assert rows[0]["quantity"] == 2

    await store.delete_inventory_holding("c1", "character", "flo", "ring")
    rows = await store.list_inventory_holdings("c1", holder_kind="character", holder_id="flo")
    assert rows == []


async def test_record_and_list_flags(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.record_inventory_flag(
        campaign_id="c1",
        turn_id="t1",
        op_json='{"action":"drop"}',
        flag_reason="low_confidence",
        created_at="2026-05-28T00:00:00Z",
    )
    flags = await store.list_inventory_flags("c1", resolved=False)
    assert len(flags) == 1
    fid = flags[0]["id"]
    await store.resolve_inventory_flag("c1", fid)
    assert await store.list_inventory_flags("c1", resolved=False) == []


async def test_find_and_create_emergent_item(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    assert await store.find_item_by_name("c1", "Rusty Key") is None
    ref = await store.create_emergent_item("c1", "Rusty Key", source="inventory")
    assert ref == "rusty-key"
    found = await store.find_item_by_name("c1", "rusty key")
    assert found is not None and found["item_ref"] == "rusty-key"


async def test_set_and_get_campaign_config(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.set_campaign_config("c1", {"inventory": {"enabled": True}})
    cfg = await store.get_campaign_config("c1")
    assert cfg["inventory"]["enabled"] is True
