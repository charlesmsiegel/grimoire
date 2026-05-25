"""Resolved per-campaign entity views, promotion, and override routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import CharactersDep, MechanicsDep, WorldDep
from grimoire.api.util import map_lookup_errors, to_payload

from .helpers import _list_kind
from .schemas import (
    CharacterCreationSubmitPayload,
    CharacterOverridePayload,
    MechanicsSwitchPayload,
    PromotePayload,
)

router = APIRouter()


@router.get("/{campaign_id}/characters")
async def list_characters(campaign_id: str, characters: CharactersDep) -> Any:
    try:
        return to_payload(await characters.list_for_campaign(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/items")
async def list_items(campaign_id: str, world: WorldDep) -> Any:
    try:
        return await _list_kind(campaign_id, "item", world)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/locations")
async def list_locations(campaign_id: str, world: WorldDep) -> Any:
    try:
        return await _list_kind(campaign_id, "location", world)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/lore")
async def list_lore(campaign_id: str, world: WorldDep) -> Any:
    try:
        return await _list_kind(campaign_id, "lore", world)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/factions")
async def list_factions(campaign_id: str, world: WorldDep) -> Any:
    try:
        return await _list_kind(campaign_id, "faction", world)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/monsters")
async def list_monsters(campaign_id: str, world: WorldDep) -> Any:
    try:
        return await _list_kind(campaign_id, "monster", world)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/characters/{entity_id}/promote-to-library")
async def promote_character(
    campaign_id: str,
    entity_id: str,
    payload: PromotePayload,
    characters: CharactersDep,
) -> Any:
    try:
        return to_payload(
            await characters.promote_to_library(
                campaign_id,
                entity_id,
                payload.target_world_id,
                source=payload.source,
                confirm=payload.confirm,
            )
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/{campaign_id}/characters/{entity_id}/override")
async def patch_character_override(
    campaign_id: str,
    entity_id: str,
    payload: CharacterOverridePayload,
    characters: CharactersDep,
) -> Any:
    world_id = payload.world_id
    if not world_id:
        try:
            rows = await characters.list_for_campaign(campaign_id)
        except Exception as exc:
            raise map_lookup_errors(exc) from exc
        for row in rows:
            char = row.character
            if char.id == entity_id and char.world_id:
                world_id = char.world_id
                break
    if not world_id:
        raise HTTPException(
            status_code=404,
            detail=(
                f"could not resolve owning world for character {entity_id!r} in "
                f"campaign {campaign_id!r}; pass world_id explicitly for emergent-only campaigns"
            ),
        )
    ref = f"library:worlds/{world_id}/characters/{entity_id}"
    try:
        await characters.upsert_override(
            campaign_id,
            ref,
            payload.override,
            source=payload.source,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True, "world_id": world_id, "ref": ref}


@router.post("/{campaign_id}/{kind}/{entity_id}/promote-to-library")
async def promote_entity(
    campaign_id: str,
    kind: str,
    entity_id: str,
    payload: PromotePayload,
    world: WorldDep,
) -> Any:
    if kind == "characters":
        raise HTTPException(status_code=404, detail="use /characters/{id}/promote-to-library")
    try:
        return to_payload(
            await world.promote_to_library(
                campaign_id,
                kind.removesuffix("s"),
                entity_id,
                payload.target_world_id,
                source=payload.source,
            )
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/characters/{character_id}/creation")
async def get_character_creation_steps(
    campaign_id: str,
    character_id: str,
    mechanics: MechanicsDep,
) -> Any:
    _ = character_id
    try:
        return to_payload(await mechanics.character_creation_steps(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/characters/{character_id}/creation/submit")
async def submit_character_creation(
    campaign_id: str,
    character_id: str,
    payload: CharacterCreationSubmitPayload,
    mechanics: MechanicsDep,
) -> Any:
    try:
        return await mechanics.finalize_character_creation(
            campaign_id, character_id, payload.step_outputs, source=payload.source
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/mechanics/switch")
async def switch_mechanics(
    campaign_id: str,
    payload: MechanicsSwitchPayload,
    mechanics: MechanicsDep,
) -> Any:
    try:
        result = await mechanics.switch_module(
            campaign_id, payload.mechanics, source=payload.source
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)
