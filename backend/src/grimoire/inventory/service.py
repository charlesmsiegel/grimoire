"""InventoryService: deterministic application of inventory operations."""

from __future__ import annotations

import logging
from collections import OrderedDict
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
    InventoryEntry,
    InventoryOperation,
)
from .persistence import InventoryPersistence
from .resolver import ItemResolver
from .state_machine import Holdings, ResolvedOp, apply_op

logger = logging.getLogger(__name__)


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


OpSignature = tuple[str, str, str, str | None, str | None, str | None, int]


def _op_signature(
    *,
    action: InventoryAction,
    item_ref: str,
    holder_kind: HolderKind,
    holder_id: str,
    to_kind: HolderKind | None,
    to_id: str | None,
    quantity: int | None,
) -> OpSignature:
    """Identity of an applied op for turn-scoped dedup (#622).

    Mirrors the ``(action, item, holder, to, quantity)`` tuple the issue calls
    out, but over *resolved* item/holder ids so two restatements that name the
    item differently ("the ring" / "ring") collapse. Quantity is normalised the
    same way ``apply_op`` does (magnitude for every action but ADJUST) so a
    signed/unsigned restatement of one removal still matches.
    """
    qty = quantity if quantity is not None else 1
    if action is not InventoryAction.ADJUST and qty < 0:
        qty = -qty
    return (
        action.value,
        item_ref,
        holder_kind.value,
        holder_id,
        to_kind.value if to_kind is not None else None,
        to_id,
        qty,
    )


class InventoryService:
    # How many recent turns' applied-op signatures to retain for cross-round
    # dedup. Rounds of one turn run back-to-back, so only the current turn is
    # ever consulted; the bound just stops the map growing without limit across
    # a long session (oldest turn evicted first).
    _MAX_TURN_SIGNATURES = 64

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
        # Per-turn set of op signatures already applied in an *earlier* round of
        # that turn (#622). Keyed by (campaign_id, turn_id); bounded LRU.
        self._applied_signatures: OrderedDict[tuple[str, str], set[OpSignature]] = OrderedDict()

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
        # Pre-image of every loaded holder (deep copies — apply_op mutates
        # entries in place), used to restore already-persisted holders when a
        # later holder's write fails: the persist loop commits all holders or
        # none (#584). Dict-as-ordered-set so persistence (and its unwind)
        # walks holders in first-touch order deterministically.
        pristine: dict[tuple[HolderKind, str], list[InventoryEntry]] = {}
        touched: dict[tuple[HolderKind, str], None] = {}
        flags: list[FlaggedOp] = []

        # Turn-scoped cross-round dedup (#622): in per_character_multi_call mode
        # every speaker round re-extracts and applies under the *same* turn_id,
        # so two NPC responses that restate one event ("Alice takes the ring" /
        # "…watches Alice pocket the ring") would apply the additive op twice.
        # We skip an op whose signature already applied in an *earlier* round of
        # this turn. `prior` is a snapshot taken before this round, so genuinely
        # repeated ops *within one response* (one apply() call) both apply — the
        # policy is "within-round repeats are real, cross-round repeats are
        # restatements." turn_id=None (manual API ops) opts out entirely.
        prior = self._applied_signatures.get((campaign_id, turn_id or ""), set())
        round_signatures: set[OpSignature] = set()
        deduped = 0

        for op in operations:
            holder_kind, holder_id = self._holder(op.holder)
            if holder_id is None:
                flags.append(FlaggedOp(op=op, reason=FlagReason.UNRESOLVED_HOLDER))
                continue
            item_ref, item_name, fungible = await resolver.resolve(
                campaign_id, op.item, turn_id=turn_id
            )
            to_kind, to_id = self._holder(op.to) if op.to else (None, None)

            signature = _op_signature(
                action=op.action,
                item_ref=item_ref,
                holder_kind=holder_kind,
                holder_id=holder_id,
                to_kind=to_kind,
                to_id=to_id,
                quantity=op.quantity,
            )
            if turn_id is not None and signature in prior:
                deduped += 1
                logger.debug(
                    "inventory op deduped within turn %s (campaign %s): %s",
                    turn_id,
                    campaign_id,
                    signature,
                )
                continue
            round_signatures.add(signature)

            await self._ensure_loaded(campaign_id, holdings, pristine, holder_kind, holder_id)
            if to_id is not None and to_kind is not None:
                await self._ensure_loaded(campaign_id, holdings, pristine, to_kind, to_id)

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
            touched[(holder_kind, holder_id)] = None
            if to_id is not None and to_kind is not None:
                touched[(to_kind, to_id)] = None

            if step.flag is not None:
                flags.append(FlaggedOp(op=op, reason=step.flag))
            elif op.confidence < config.flag_threshold:
                flags.append(FlaggedOp(op=op, reason=FlagReason.LOW_CONFIDENCE))

        # Persist every touched holder (file SSOT + derived rows). A failure
        # mid-loop restores every holder reached so far — including the one
        # whose write failed partway (file written, derived rows not) — before
        # re-raising: the batch commits all holders or none (#584).
        # BaseException so a task cancellation mid-persist restores too.
        written: list[tuple[HolderKind, str]] = []
        try:
            for hk, hid in touched:
                written.append((hk, hid))
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
        except BaseException:
            await self._rewrite_holders(
                campaign_id,
                turn_id,
                [(hk, hid, pristine.get((hk, hid), [])) for hk, hid in reversed(written)],
            )
            raise

        # Record this round's signatures only after the batch committed, so a
        # rolled-back round leaves no dedup ghost that would skip a legit retry
        # (#622). A turn fails terminally on apply failure, but recording on
        # success keeps the invariant local and obvious.
        self._remember_signatures(campaign_id, turn_id, round_signatures)

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
        return {
            "touched": len(touched),
            "flags": len(flags),
            # Ops skipped as cross-round restatements of an earlier round in the
            # same turn (#622); 0 outside multi-call mode.
            "deduped": deduped,
            # Pre-images of every holder this apply committed, handed back via
            # restore_holders when a turn stage *after* the apply fails (#584).
            "rollback": [(hk, hid, pristine.get((hk, hid), [])) for hk, hid in touched],
        }

    def _remember_signatures(
        self, campaign_id: str, turn_id: str | None, signatures: set[OpSignature]
    ) -> None:
        """Fold a committed round's op signatures into the turn's applied set,
        evicting the oldest turn when the bounded map overflows (#622)."""
        if turn_id is None or not signatures:
            return
        key = (campaign_id, turn_id)
        existing = self._applied_signatures.get(key)
        if existing is None:
            self._applied_signatures[key] = set(signatures)
            while len(self._applied_signatures) > self._MAX_TURN_SIGNATURES:
                self._applied_signatures.popitem(last=False)
        else:
            existing |= signatures
            self._applied_signatures.move_to_end(key)

    async def restore_holders(
        self,
        *,
        campaign_id: str,
        turn_id: str | None,
        rollback: list[tuple[HolderKind, str, list[InventoryEntry]]],
    ) -> None:
        """Rewrite holders to the pre-images a successful :meth:`apply` returned.

        Called by the orchestrator's cross-stage unwind when a turn stage
        *after* the inventory apply fails (#584), so committed holder writes
        unwind with the rest of the turn. Restores newest-first and emits
        ``INVENTORY_CHANGED`` so consumers (HUD) refresh.
        """
        if not rollback:
            return
        await self._rewrite_holders(campaign_id, turn_id, list(reversed(rollback)))
        await self._bus.emit(
            Event(
                type=inv_events.INVENTORY_CHANGED,
                payload={
                    "campaign_id": campaign_id,
                    "turn_id": turn_id,
                    "holders": [{"kind": k.value, "id": i} for (k, i, _) in rollback],
                },
            )
        )

    async def _rewrite_holders(
        self,
        campaign_id: str,
        turn_id: str | None,
        holders: list[tuple[HolderKind, str, list[InventoryEntry]]],
    ) -> None:
        """Best-effort restore of holder pre-images; failures are logged so a
        partial rollback is visible, and the remaining holders still restore."""
        for hk, hid, entries in holders:
            try:
                await self._persist.write_holder_inventory(
                    campaign_id=campaign_id,
                    holder_kind=hk,
                    holder_id=hid,
                    entries=entries,
                    source="inventory_rollback",
                    turn_id=turn_id,
                )
            except Exception:
                logger.warning(
                    "inventory rollback failed for holder %s/%s (campaign %s)",
                    getattr(hk, "value", hk),
                    hid,
                    campaign_id,
                    exc_info=True,
                )

    async def _ensure_loaded(
        self,
        campaign_id: str,
        holdings: Holdings,
        pristine: dict[tuple[HolderKind, str], list[InventoryEntry]],
        kind: HolderKind,
        hid: str,
    ) -> None:
        if (kind, hid) in holdings:
            return
        entries = await self._persist.read_holder_inventory(campaign_id, kind, hid)
        holdings[(kind, hid)] = {e.item_ref: e for e in entries}
        # apply_op mutates entries in place, so keep untouched copies for the
        # persist-failure rollback.
        pristine[(kind, hid)] = [e.model_copy(deep=True) for e in entries]

    async def _record_flags(
        self, campaign_id: str, turn_id: str | None, flags: list[FlaggedOp]
    ) -> None:
        """Insert review flags all-or-nothing: a failure mid-way deletes the
        flags already inserted before re-raising, so a rolled-back apply never
        leaves review flags behind (#584)."""
        now = self._clock().isoformat()
        recorded: list[str] = []
        try:
            for f in flags:
                fid = await self._store.record_inventory_flag(
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    op_json=f.op.model_dump_json(),
                    flag_reason=f.reason.value,
                    created_at=now,
                )
                recorded.append(fid)
        except BaseException:
            for fid in reversed(recorded):
                try:
                    await self._store.delete_inventory_flag(campaign_id, fid)
                except Exception:
                    logger.warning(
                        "inventory flag rollback failed for %s (campaign %s)",
                        fid,
                        campaign_id,
                        exc_info=True,
                    )
            raise

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
