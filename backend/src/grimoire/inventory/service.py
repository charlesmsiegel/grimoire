"""InventoryService: deterministic application of inventory operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.types.state import DeltaKind, StateDelta

from . import events as inv_events
from .config import InventoryConfig
from .models import (
    FlaggedOp,
    FlagReason,
    HolderKind,
    InventoryAction,
    InventoryOperation,
)
from .persistence import InventoryPersistence
from .resolver import ItemResolver
from .state_machine import Holdings, ResolvedOp, apply_op


def deltas_to_operations(deltas: list[StateDelta]) -> list[InventoryOperation]:
    """Map INVENTORY_CHANGE deltas to typed operations (extraction order)."""
    ops: list[InventoryOperation] = []
    for d in deltas:
        if d.kind is not DeltaKind.INVENTORY_CHANGE:
            continue
        a = d.after or {}
        try:
            ops.append(
                InventoryOperation(
                    action=InventoryAction(a.get("action", "acquire")),
                    item=str(a.get("item", "")),
                    holder=str(a.get("holder", "")),
                    to=a.get("to"),
                    quantity=a.get("quantity"),
                    equipped=a.get("equipped"),
                    provenance=a.get("provenance"),
                    confidence=float(d.confidence),
                    source=d.source or "extractor",
                    evidence=d.evidence or "",
                )
            )
        except (ValueError, TypeError):
            continue
    return ops


class InventoryService:
    def __init__(
        self,
        *,
        store: Any,
        event_bus: EventBus,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._bus = event_bus
        self._clock = clock
        self._persist = InventoryPersistence(store)

    async def _config(self, campaign_id: str) -> InventoryConfig:
        return InventoryConfig.from_campaign_config(
            await self._store.get_campaign_config(campaign_id)
        )

    async def apply_from_deltas(
        self, *, campaign_id: str, turn_id: str | None, deltas: list[StateDelta]
    ) -> dict | None:
        """Map extracted ``INVENTORY_CHANGE`` deltas to operations and apply.

        Lets callers (the orchestrator) hand off raw deltas without importing
        the inventory package — they hold this service via an injected
        reference and never reach into ``grimoire.inventory``.
        """
        ops = deltas_to_operations(deltas)
        if not ops:
            return None
        return await self.apply(campaign_id=campaign_id, turn_id=turn_id, operations=ops)

    async def apply(
        self, *, campaign_id: str, turn_id: str | None, operations: list[InventoryOperation]
    ) -> dict | None:
        config = await self._config(campaign_id)
        if not config.enabled or not operations:
            return None

        resolver = ItemResolver(self._store, config)
        holdings: Holdings = {}
        touched: set[tuple[HolderKind, str]] = set()
        flags: list[FlaggedOp] = []

        for op in operations:
            holder_kind, holder_id = self._holder(op.holder)
            if holder_id is None:
                flags.append(FlaggedOp(op=op, reason=FlagReason.UNRESOLVED_HOLDER))
                continue
            item_ref, item_name, fungible = await resolver.resolve(
                campaign_id, op.item, turn_id=turn_id
            )
            to_kind, to_id = self._holder(op.to) if op.to else (None, None)

            await self._ensure_loaded(campaign_id, holdings, holder_kind, holder_id)
            if to_id is not None and to_kind is not None:
                await self._ensure_loaded(campaign_id, holdings, to_kind, to_id)

            resolved = ResolvedOp(
                action=op.action,
                item_ref=item_ref,
                item_name=item_name,
                fungible=fungible,
                holder_kind=holder_kind,
                holder_id=holder_id,
                to_kind=to_kind,
                to_id=to_id,
                quantity=op.quantity,
                equipped=op.equipped,
                provenance=op.provenance,
                acquired_in_post=turn_id,
            )
            step = apply_op(holdings, resolved)
            touched.add((holder_kind, holder_id))
            if to_id is not None and to_kind is not None:
                touched.add((to_kind, to_id))

            if step.flag is not None:
                flags.append(FlaggedOp(op=op, reason=step.flag))
            elif op.confidence < config.flag_threshold:
                flags.append(FlaggedOp(op=op, reason=FlagReason.LOW_CONFIDENCE))

        # Persist every touched holder (file SSOT + derived rows).
        for hk, hid in touched:
            entries = list(holdings.get((hk, hid), {}).values())
            await self._persist.write_holder_inventory(
                campaign_id=campaign_id,
                holder_kind=hk,
                holder_id=hid,
                entries=entries,
                source="inventory",
                turn_id=turn_id,
            )

        await self._record_flags(campaign_id, turn_id, flags)

        await self._bus.emit(
            Event(
                type=inv_events.INVENTORY_CHANGED,
                payload={
                    "campaign_id": campaign_id,
                    "turn_id": turn_id,
                    "holders": [{"kind": k.value, "id": i} for (k, i) in touched],
                },
            )
        )
        if flags:
            await self._bus.emit(
                Event(
                    type=inv_events.INVENTORY_FLAGGED,
                    payload={"campaign_id": campaign_id, "turn_id": turn_id, "count": len(flags)},
                )
            )
        return {"touched": len(touched), "flags": len(flags)}

    async def _ensure_loaded(
        self, campaign_id: str, holdings: Holdings, kind: HolderKind, hid: str
    ) -> None:
        if (kind, hid) in holdings:
            return
        entries = await self._persist.read_holder_inventory(campaign_id, kind, hid)
        holdings[(kind, hid)] = {e.item_ref: e for e in entries}

    async def _record_flags(
        self, campaign_id: str, turn_id: str | None, flags: list[FlaggedOp]
    ) -> None:
        now = self._clock().isoformat()
        for f in flags:
            await self._store.record_inventory_flag(
                campaign_id=campaign_id,
                turn_id=turn_id,
                op_json=f.op.model_dump_json(),
                flag_reason=f.reason.value,
                created_at=now,
            )

    @staticmethod
    def _holder(ref: str | None) -> tuple[HolderKind | None, str | None]:
        """Resolve a holder ref to (kind, id). Locations are detected by a
        'location:' prefix or a '/locations/' segment; otherwise character.
        Refs may be ids or composite 'library:.../characters/<id>' refs."""
        if not ref:
            return None, None
        raw = ref.strip()
        kind = HolderKind.CHARACTER
        if raw.startswith("location:") or "/locations/" in raw:
            kind = HolderKind.LOCATION
        hid = raw.split("/")[-1].split(":")[-1]
        return kind, (hid or None)
