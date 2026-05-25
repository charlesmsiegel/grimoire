"""API routes for the new-scene workflow and Scene Ledger."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from grimoire.api.deps import SceneLedgerDep

router = APIRouter()


class LedgerStatusUpdate(BaseModel):
    status: str


@router.get("/{campaign_id}/scene-ledger")
async def list_ledger(
    campaign_id: str,
    ledger: SceneLedgerDep,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status == "active":
        return await ledger.list_active(campaign_id)
    return await ledger.list_all(campaign_id)


@router.patch("/{campaign_id}/scene-ledger/{item_id}")
async def update_ledger_item(
    campaign_id: str,
    item_id: str,
    body: LedgerStatusUpdate,
    ledger: SceneLedgerDep,
) -> dict[str, str]:
    await ledger.set_status(item_id, body.status)
    return {"id": item_id, "status": body.status}
