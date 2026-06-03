"""Campaign PC (player character) routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body

from grimoire.api.deps import CharactersDep, ScenesDep
from grimoire.api.util import map_lookup_errors, to_payload

from .schemas import AddPCPayload, PCProfilePayload

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{campaign_id}/pcs")
async def list_pcs(campaign_id: str, characters: CharactersDep) -> Any:
    try:
        return to_payload(await characters.list_pcs(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/pcs", status_code=201)
async def add_pc(
    campaign_id: str,
    payload: AddPCPayload,
    characters: CharactersDep,
    scenes: ScenesDep,
) -> Any:
    try:
        result = await characters.add_pc(
            campaign_id,
            payload.character_ref,
            payload.name,
            payload.owner,
            role_tags=payload.role_tags,
        )
        try:
            active = await scenes.active_scene_for_campaign(campaign_id)
            if active is not None:
                await scenes.add_present_pc(active.id, payload.character_ref)
        except Exception:
            logger.warning(
                "could not attach new PC %s to active scene in campaign %s",
                payload.character_ref,
                campaign_id,
                exc_info=True,
            )
        return to_payload(result)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/{campaign_id}/pcs/{character_ref:path}", status_code=204)
async def remove_pc(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
    scenes: ScenesDep,
) -> None:
    try:
        await characters.remove_pc(campaign_id, character_ref)
        # Mirror add_pc: a removed PC must also leave the active scene, or it
        # keeps counting toward present_pc_refs and gates multi-PC advance until
        # a separate cast-change removal happens (#517).
        try:
            active = await scenes.active_scene_for_campaign(campaign_id)
            if active is not None:
                await scenes.remove_present_character(active.id, character_ref)
        except Exception:
            logger.warning(
                "could not detach removed PC %s from active scene in campaign %s",
                character_ref,
                campaign_id,
                exc_info=True,
            )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/pcs/{character_ref:path}/set-active")
async def set_active_pc(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
) -> Any:
    try:
        await characters.set_active_pc(campaign_id, character_ref)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.post("/{campaign_id}/pcs/{character_ref:path}/set-current-scene")
async def set_current_scene_for_pc(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
    scene_id: Annotated[str, Body(embed=True)],
) -> Any:
    try:
        await characters.set_current_scene_for_pc(campaign_id, character_ref, scene_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/pcs/{character_ref:path}/profile")
async def get_pc_profile(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
) -> Any:
    try:
        profile = await characters.get_pc_profile(campaign_id, character_ref)
        if profile is None:
            return {
                "description": "",
                "goals": [],
                "player_notes": "",
                "character_ref": character_ref,
            }
        return to_payload(profile)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/pcs/{character_ref:path}/profile")
async def save_pc_profile(
    campaign_id: str,
    character_ref: str,
    payload: PCProfilePayload,
    characters: CharactersDep,
) -> Any:
    from grimoire.characters.pc_profile import PCProfile

    try:
        profile = PCProfile(
            character_ref=character_ref,
            goals=payload.goals,
            player_notes=payload.player_notes,
            description=payload.description,
        )
        await characters.save_pc_profile(campaign_id, character_ref, profile)
        return to_payload(profile)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/pcs/{character_ref:path}/profile/revisions")
async def list_pc_profile_revisions(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
) -> Any:
    try:
        revisions = await characters.list_pc_profile_revisions(campaign_id, character_ref)
        return to_payload(revisions)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/pcs/{character_ref:path}/profile/revisions/{timestamp}")
async def get_pc_profile_revision(
    campaign_id: str,
    character_ref: str,
    timestamp: str,
    characters: CharactersDep,
) -> Any:
    from fastapi import HTTPException

    try:
        revision = await characters.get_pc_profile_revision(campaign_id, character_ref, timestamp)
        if revision is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        return to_payload(revision)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
