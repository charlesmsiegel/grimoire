from grimoire.inventory.models import (
    FlagReason,
    HolderKind,
    InventoryAction,
    InventoryEntry,
)
from grimoire.inventory.state_machine import ResolvedOp, apply_op


def _op(action, *, item_ref="ring", fungible=False, holder=("character", "flo"),
        to=None, quantity=None, equipped=None, item_name="Ring"):
    return ResolvedOp(
        action=action,
        item_ref=item_ref,
        item_name=item_name,
        fungible=fungible,
        holder_kind=HolderKind(holder[0]),
        holder_id=holder[1],
        to_kind=HolderKind(to[0]) if to else None,
        to_id=to[1] if to else None,
        quantity=quantity,
        equipped=equipped,
    )


def _holdings(*entries):
    # mapping: (holder_kind, holder_id) -> {item_ref: InventoryEntry}
    h = {}
    for (hk, hid), entry in entries:
        h.setdefault((HolderKind(hk), hid), {})[entry.item_ref] = entry
    return h


def test_acquire_adds_entry():
    h = {}
    res = apply_op(h, _op(InventoryAction.ACQUIRE))
    entry = h[(HolderKind.CHARACTER, "flo")]["ring"]
    assert entry.quantity == 1
    assert res.flag is None


def test_acquire_stacks_fungible():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=100, fungible=True)),
    )
    apply_op(h, _op(InventoryAction.ACQUIRE, item_ref="gold", fungible=True, quantity=20))
    assert h[(HolderKind.CHARACTER, "flo")]["gold"].quantity == 120


def test_transfer_moves_between_holders():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="ring", item_name="Ring", quantity=1)),
    )
    res = apply_op(
        h, _op(InventoryAction.TRANSFER, to=("character", "julian"), quantity=1)
    )
    assert "ring" not in h[(HolderKind.CHARACTER, "flo")]
    assert h[(HolderKind.CHARACTER, "julian")]["ring"].quantity == 1
    assert res.flag is None


def test_consume_default_one_removes_at_zero():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="potion", item_name="Potion", quantity=1, fungible=True)),
    )
    apply_op(h, _op(InventoryAction.CONSUME, item_ref="potion", fungible=True))
    assert "potion" not in h[(HolderKind.CHARACTER, "flo")]


def test_adjust_applies_signed_delta():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=100, fungible=True)),
    )
    apply_op(h, _op(InventoryAction.ADJUST, item_ref="gold", fungible=True, quantity=-30))
    assert h[(HolderKind.CHARACTER, "flo")]["gold"].quantity == 70


def test_equip_sets_flag():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="sword", item_name="Sword", quantity=1)),
    )
    apply_op(h, _op(InventoryAction.EQUIP, item_ref="sword"))
    assert h[(HolderKind.CHARACTER, "flo")]["sword"].equipped is True


def test_drop_missing_item_reconciles_and_flags():
    h = {}
    res = apply_op(h, _op(InventoryAction.DROP))
    # Reconciled: item granted then dropped -> holder ends with no entry.
    assert "ring" not in h.get((HolderKind.CHARACTER, "flo"), {})
    assert res.flag is FlagReason.RECONCILED_MISSING_ITEM


def test_transfer_missing_source_reconciles_and_flags():
    h = {}
    res = apply_op(h, _op(InventoryAction.TRANSFER, to=("character", "julian"), quantity=1))
    assert h[(HolderKind.CHARACTER, "julian")]["ring"].quantity == 1
    assert res.flag is FlagReason.RECONCILED_MISSING_ITEM


def test_over_consume_clamps_and_flags():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=10, fungible=True)),
    )
    res = apply_op(h, _op(InventoryAction.CONSUME, item_ref="gold", fungible=True, quantity=50))
    assert "gold" not in h[(HolderKind.CHARACTER, "flo")]
    assert res.flag is FlagReason.RECONCILED_QUANTITY


def test_adjust_below_zero_clamps_and_flags():
    h = _holdings(
        (("character", "flo"), InventoryEntry(item_ref="gold", item_name="Gold", quantity=10, fungible=True)),
    )
    res = apply_op(h, _op(InventoryAction.ADJUST, item_ref="gold", fungible=True, quantity=-50))
    assert "gold" not in h[(HolderKind.CHARACTER, "flo")]
    assert res.flag is FlagReason.RECONCILED_QUANTITY


def test_equip_missing_item_reconciles_and_flags():
    h = {}
    res = apply_op(h, _op(InventoryAction.EQUIP, item_ref="sword", item_name="Sword"))
    assert h[(HolderKind.CHARACTER, "flo")]["sword"].equipped is True
    assert res.flag is FlagReason.RECONCILED_MISSING_ITEM
