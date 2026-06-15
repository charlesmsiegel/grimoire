"""Campaign sheet and mechanics content routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from grimoire.api.deps import CharactersDep, MechanicsDep, StateStoreDep, WorldDep
from grimoire.api.util import map_lookup_errors, to_payload

from .helpers import _require_campaign_row

router = APIRouter()


@router.post("/{campaign_id}/sheets/bulk-create-missing")
async def bulk_create_missing_sheets(
    campaign_id: str,
    mechanics: MechanicsDep,
    characters: CharactersDep,
    world: WorldDep,
) -> Any:
    try:
        result = await mechanics.bulk_create_missing_sheets(
            campaign_id, characters=characters, world=world
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/{campaign_id}/sheets/{kind}/{entity_id}")
async def get_sheet(
    campaign_id: str,
    kind: str,
    entity_id: str,
    mechanics: MechanicsDep,
) -> Any:
    try:
        sheet = await mechanics.get_sheet(campaign_id, entity_id, entity_kind=kind)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    if sheet is None:
        raise HTTPException(status_code=404, detail="sheet not found")
    return sheet


@router.put("/{campaign_id}/sheets/{kind}/{entity_id}")
async def put_sheet(
    campaign_id: str,
    kind: str,
    entity_id: str,
    state_store: StateStoreDep,
    payload: Annotated[dict[str, Any], Body()],
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    module_id = row["mechanics_module"] or "null"
    try:
        await state_store.write_sheet(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            mechanics_id=module_id,
            sheet=payload,
            source="api",
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/content/{kind}")
async def list_mechanics_content(
    campaign_id: str,
    kind: str,
    mechanics: MechanicsDep,
) -> Any:
    try:
        return to_payload(await mechanics.list_content(campaign_id, kind))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/content/{kind}/{content_id}")
async def get_mechanics_content(
    campaign_id: str,
    kind: str,
    content_id: str,
    mechanics: MechanicsDep,
) -> Any:
    try:
        payload = await mechanics.get_content(campaign_id, kind, content_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="content not found")
    return payload


@router.put("/{campaign_id}/content/{kind}/{content_id}")
async def put_mechanics_content(
    campaign_id: str,
    kind: str,
    content_id: str,
    mechanics: MechanicsDep,
    payload: Annotated[dict[str, Any], Body()],
) -> Any:
    try:
        return await mechanics.put_content(campaign_id, kind, content_id, payload)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
