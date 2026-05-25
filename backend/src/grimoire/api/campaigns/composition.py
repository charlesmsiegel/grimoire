"""Campaign composition routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import LibraryDep, StateStoreDep
from grimoire.api.util import map_lookup_errors, to_payload

from .schemas import CompositionPayload, WorldRefPayload

router = APIRouter()


@router.get("/{campaign_id}/composition")
async def get_composition(campaign_id: str, library: LibraryDep) -> Any:
    try:
        return to_payload(await library.get_composition(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/composition")
async def set_composition(
    campaign_id: str,
    payload: CompositionPayload,
    library: LibraryDep,
) -> Any:
    from grimoire.types.composition import Composition, WorldRef

    try:
        comp = Composition(
            worlds=[WorldRef(**ref.model_dump()) for ref in payload.worlds],
            mechanics=payload.mechanics,
            style_guide_id=payload.style_guide_id,
            image_preset_id=payload.image_preset_id,
            inline_style_guide=payload.inline_style_guide,
            content_boundaries=payload.content_boundaries,
            calendar_ids=list(payload.calendar_ids),
            holiday_set_ids=list(payload.holiday_set_ids),
            display_calendar_id=payload.display_calendar_id,
        )
        await library.set_composition(campaign_id, comp)
        return to_payload(await library.get_composition(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/composition/refs", status_code=201)
async def add_world_ref(
    campaign_id: str,
    ref: WorldRefPayload,
    state_store: StateStoreDep,
) -> Any:
    try:
        await state_store.upsert_world_ref(
            campaign_id=campaign_id,
            world_id=ref.world_id,
            priority=ref.priority,
            include=list(ref.include) if ref.include is not None else None,
            track_latest=ref.track_latest,
            bound_at_version=ref.bound_at_version,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.delete("/{campaign_id}/composition/refs/{world_id}", status_code=204)
async def remove_world_ref(
    campaign_id: str,
    world_id: str,
    state_store: StateStoreDep,
) -> None:
    await state_store.db.execute(
        "DELETE FROM campaign_world_refs WHERE campaign_id = ? AND world_id = ?",
        (campaign_id, world_id),
    )


@router.post("/{campaign_id}/composition/refs/{world_id}/upgrade")
async def upgrade_world_ref(
    campaign_id: str,
    world_id: str,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(await library.upgrade_world_ref(campaign_id, world_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
