import pytest

from grimoire.event_bus import EventBus
from grimoire.inventory.models import InventoryAction, InventoryOperation
from grimoire.inventory.service import InventoryService, deltas_to_operations
from grimoire.types.common import Scope
from grimoire.types.state import DeltaKind, StateDelta

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_deltas_to_operations_filters_and_maps():
    deltas = [
        StateDelta(kind=DeltaKind.FACT_ADD, target_scope=Scope.CAMPAIGN_SQLITE, target_id="x"),
        StateDelta(
            kind=DeltaKind.INVENTORY_CHANGE, target_scope=Scope.CAMPAIGN_SQLITE, target_id="y",
            after={"action": "transfer", "item": "ring", "holder": "flo", "to": "julian", "quantity": 1},
            confidence=0.9,
        ),
    ]
    ops = deltas_to_operations(deltas)
    assert len(ops) == 1
    assert ops[0].action is InventoryAction.TRANSFER
    assert ops[0].to == "julian"


async def test_pipeline_applies_inventory(store):
    await store.upsert_campaign(campaign_id="c1", name="C")
    await store.set_campaign_config("c1", {"inventory": {"enabled": True}})
    await store.write_emergent(
        campaign_id="c1", kind="character", entity_id="flo",
        frontmatter={"id": "flo", "name": "Flo"}, body="", source="test",
    )
    svc = InventoryService(store=store, event_bus=EventBus())
    op = InventoryOperation(action=InventoryAction.ACQUIRE, item="ring", holder="flo", confidence=1.0)
    await svc.apply(campaign_id="c1", turn_id="t1", operations=[op])
    rows = await store.list_inventory_holdings("c1", item_ref="ring")
    assert len(rows) == 1
