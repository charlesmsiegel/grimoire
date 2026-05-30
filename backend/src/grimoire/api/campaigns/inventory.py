"""Inventory REST API (#444). Mounted under /api/campaigns/{campaign_id}/inventory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import InventoryDep, StateStoreDep
from grimoire.inventory.config import InventoryConfig
from grimoire.inventory.models import InventoryOperation

router = APIRouter()


async def _is_enabled(store: Any, campaign_id: str) -> bool:
    cfg = InventoryConfig.from_campaign_config(await store.get_campaign_config(campaign_id))
    return cfg.enabled


async def _require_enabled(store: Any, campaign_id: str) -> InventoryConfig:
    cfg = InventoryConfig.from_campaign_config(await store.get_campaign_config(campaign_id))
    if not cfg.enabled:
        raise HTTPException(status_code=409, detail="feature_disabled")
    return cfg


# Reads return an empty result (not 409) when inventory is disabled: "what
# holdings/flags exist" has a sensible answer — none — when the feature is off,
# and a 409 on a safe GET is both misleading in logs and noisy for HUD widgets
# that poll regardless of campaign config. Mutations below still 409.
@router.get("/{campaign_id}/inventory")
async def get_inventory(campaign_id: str, store: StateStoreDep, item_ref: str | None = None) -> Any:
    if not await _is_enabled(store, campaign_id):
        return {"holders": []}
    rows = await store.list_inventory_holdings(campaign_id, item_ref=item_ref)
    holders: dict[str, list] = {}
    for r in rows:
        holders.setdefault(f"{r['holder_kind']}:{r['holder_id']}", []).append(r)
    return {"holders": [{"holder": k, "entries": v} for k, v in holders.items()]}


@router.get("/{campaign_id}/inventory/holders/{kind}/{holder_id}")
async def get_holder(campaign_id: str, kind: str, holder_id: str, store: StateStoreDep) -> Any:
    if not await _is_enabled(store, campaign_id):
        return {"holder": f"{kind}:{holder_id}", "entries": []}
    rows = await store.list_inventory_holdings(campaign_id, holder_kind=kind, holder_id=holder_id)
    return {"holder": f"{kind}:{holder_id}", "entries": rows}


@router.post("/{campaign_id}/inventory/operations")
async def submit_operation(
    campaign_id: str, op: InventoryOperation, inventory: InventoryDep
) -> Any:
    op = op.model_copy(update={"source": "user", "confidence": 1.0})
    result = await inventory.apply(campaign_id=campaign_id, turn_id=None, operations=[op])
    if result is None:
        raise HTTPException(status_code=409, detail="feature_disabled")
    return result


@router.get("/{campaign_id}/inventory/flags")
async def list_flags(campaign_id: str, store: StateStoreDep, resolved: bool = False) -> Any:
    if not await _is_enabled(store, campaign_id):
        return {"flags": []}
    return {"flags": await store.list_inventory_flags(campaign_id, resolved=resolved)}


@router.post("/{campaign_id}/inventory/flags/{flag_id}/resolve")
async def resolve_flag(campaign_id: str, flag_id: str, store: StateStoreDep) -> Any:
    await _require_enabled(store, campaign_id)
    await store.resolve_inventory_flag(campaign_id, flag_id)
    return {"ok": True}
