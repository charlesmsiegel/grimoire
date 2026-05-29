"""Pure, deterministic inventory operation resolution. No I/O.

The state machine mutates an in-memory holdings map:
    holdings[(HolderKind, holder_id)][item_ref] = InventoryEntry

Each ``apply_op`` returns a ``StepResult`` carrying an optional reconciliation
flag. Conflicts never raise — the prose is canon, so we reconcile state to
match the narrative and flag the discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import FlagReason, HolderKind, InventoryAction, InventoryEntry

Holdings = dict[tuple[HolderKind, str], dict[str, InventoryEntry]]


@dataclass(frozen=True)
class ResolvedOp:
    action: InventoryAction
    item_ref: str
    item_name: str
    fungible: bool
    holder_kind: HolderKind
    holder_id: str
    to_kind: HolderKind | None = None
    to_id: str | None = None
    quantity: int | None = None
    equipped: bool | None = None
    provenance: str | None = None
    acquired_in_post: str | None = None


@dataclass(frozen=True)
class StepResult:
    flag: FlagReason | None = None


def _bucket(holdings: Holdings, kind: HolderKind, hid: str) -> dict[str, InventoryEntry]:
    return holdings.setdefault((kind, hid), {})


def _grant(bucket: dict[str, InventoryEntry], op: ResolvedOp, qty: int) -> None:
    existing = bucket.get(op.item_ref)
    if existing is not None:
        existing.quantity += qty
        return
    bucket[op.item_ref] = InventoryEntry(
        item_ref=op.item_ref,
        item_name=op.item_name,
        quantity=qty,
        fungible=op.fungible,
        provenance=op.provenance,
        acquired_in_post=op.acquired_in_post,
    )


def _remove(bucket: dict[str, InventoryEntry], item_ref: str, qty: int) -> bool:
    """Remove qty; delete entry at <=0. Returns True if a shortfall was clamped."""
    entry = bucket.get(item_ref)
    if entry is None:
        return True  # nothing to remove — shortfall
    shortfall = qty > entry.quantity
    entry.quantity -= qty
    if entry.quantity <= 0:
        del bucket[item_ref]
    return shortfall


def apply_op(holdings: Holdings, op: ResolvedOp) -> StepResult:
    qty = op.quantity if op.quantity is not None else 1
    src = _bucket(holdings, op.holder_kind, op.holder_id)

    if op.action is InventoryAction.ACQUIRE:
        _grant(src, op, qty)
        return StepResult()

    if op.action is InventoryAction.DROP:
        missing = op.item_ref not in src
        _remove(src, op.item_ref, qty)
        return StepResult(FlagReason.RECONCILED_MISSING_ITEM if missing else None)

    if op.action is InventoryAction.TRANSFER:
        if op.to_kind is None or op.to_id is None:
            # Treat as a drop if no destination resolved.
            _remove(src, op.item_ref, qty)
            return StepResult(FlagReason.RECONCILED_HOLDER)
        missing = op.item_ref not in src
        _remove(src, op.item_ref, qty)
        dst = _bucket(holdings, op.to_kind, op.to_id)
        _grant(dst, op, qty)
        return StepResult(FlagReason.RECONCILED_MISSING_ITEM if missing else None)

    if op.action is InventoryAction.CONSUME:
        entry = src.get(op.item_ref)
        take = qty if op.quantity is not None else (entry.quantity if entry else 1)
        shortfall = _remove(src, op.item_ref, take)
        return StepResult(FlagReason.RECONCILED_QUANTITY if shortfall else None)

    if op.action is InventoryAction.ADJUST:
        entry = src.get(op.item_ref)
        if entry is None:
            if qty <= 0:
                return StepResult(FlagReason.RECONCILED_QUANTITY)
            _grant(src, op, qty)
            return StepResult()
        new_q = entry.quantity + qty
        if new_q <= 0:
            del src[op.item_ref]
            return StepResult(FlagReason.RECONCILED_QUANTITY if new_q < 0 else None)
        entry.quantity = new_q
        return StepResult()

    if op.action in (InventoryAction.EQUIP, InventoryAction.UNEQUIP):
        want = op.action is InventoryAction.EQUIP
        entry = src.get(op.item_ref)
        flag = None
        if entry is None:
            _grant(src, op, 1)
            entry = src[op.item_ref]
            flag = FlagReason.RECONCILED_MISSING_ITEM
        entry.equipped = want
        return StepResult(flag)

    return StepResult()
