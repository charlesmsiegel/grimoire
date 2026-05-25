"""Campaign sheet and mechanics content routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from grimoire.api.deps import CharactersDep, MechanicsDep, StateStoreDep, WorldDep
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter()


@router.post("/{campaign_id}/sheets/bulk-create-missing")
async def bulk_create_missing_sheets(
    campaign_id: str,
    state_store: StateStoreDep,
    mechanics: MechanicsDep,
    characters: CharactersDep,
    world: WorldDep,
) -> Any:
    row = await state_store.db.fetchone(
        "SELECT mechanics_module FROM campaigns WHERE id = ?", (campaign_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    module_id = row["mechanics_module"]
    if not module_id or module_id == "null":
        raise HTTPException(
            status_code=409,
            detail=f"campaign {campaign_id!r} has no mechanics module bound",
        )
    module = mechanics.get_module(module_id)
    if module is None:
        raise HTTPException(
            status_code=404,
            detail=f"mechanics module {module_id!r} is not loaded",
        )
    sheet_kinds: list[str] = list(getattr(module, "sheet_kinds", None) or [])
    if not sheet_kinds:
        manifest = await mechanics.module_info(module_id)
        sheet_kinds = list(manifest.sheet_kinds) if manifest else []
    if not sheet_kinds:
        sheet_kinds = ["character"]

    inventory: dict[str, list[str]] = {}
    for kind in sheet_kinds:
        ids: list[str] = []
        if kind == "character":
            try:
                rows = await characters.list_for_campaign(campaign_id)
                ids = [r.character.id for r in rows]
            except Exception:
                ids = []
        else:
            try:
                entries = await world.list_for_campaign(campaign_id, kind)
                ids = [getattr(e, "asset_id", None) or getattr(e, "id", None) for e in entries]
                ids = [i for i in ids if i]
            except Exception:
                ids = []
        inventory[kind] = ids

    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for kind, entity_ids in inventory.items():
        existing = await state_store.list_sheet_entity_ids(
            campaign_id=campaign_id,
            kind=kind,
            mechanics_id=module_id,
        )
        for entity_id in entity_ids:
            if entity_id in existing:
                skipped.append({"kind": kind, "entity_id": entity_id})
                continue
            initial = module.initialize_sheet(kind, entity_id)
            await state_store.write_sheet(
                campaign_id=campaign_id,
                kind=kind,
                entity_id=entity_id,
                mechanics_id=module_id,
                sheet=initial,
                source="api:bulk-create-missing",
            )
            created.append({"kind": kind, "entity_id": entity_id})

    return {"created": created, "skipped": skipped}


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
    row = await state_store.db.fetchone(
        "SELECT mechanics_module FROM campaigns WHERE id = ?", (campaign_id,)
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
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
