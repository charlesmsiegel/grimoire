from grimoire.inventory.models import (
    FlagReason,
    FlaggedOp,
    InventoryAction,
    InventoryEntry,
    InventoryOperation,
)


def test_entry_defaults():
    e = InventoryEntry(item_ref="the-key", item_name="The Key")
    assert e.quantity == 1
    assert e.fungible is False
    assert e.equipped is False
    assert e.provenance is None


def test_operation_roundtrip_json():
    op = InventoryOperation(
        action=InventoryAction.TRANSFER,
        item="silver ring",
        holder="winifred",
        to="julian",
        quantity=1,
        confidence=0.9,
    )
    blob = op.model_dump_json()
    back = InventoryOperation.model_validate_json(blob)
    assert back.action is InventoryAction.TRANSFER
    assert back.to == "julian"


def test_flagged_op_reason_enum():
    op = InventoryOperation(action=InventoryAction.DROP, item="x", holder="h", confidence=0.1)
    flag = FlaggedOp(op=op, reason=FlagReason.LOW_CONFIDENCE)
    assert flag.reason is FlagReason.LOW_CONFIDENCE
