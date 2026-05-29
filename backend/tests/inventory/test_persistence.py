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
