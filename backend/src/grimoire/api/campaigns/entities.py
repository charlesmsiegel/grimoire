"""Resolved per-campaign entity views, promotion, and override routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import CharactersDep, MechanicsDep, ScenesDep, WorldDep
from grimoire.api.util import map_lookup_errors, to_payload
from grimoire.util import canonicalize_character_ref

from .helpers import _list_kind
from .schemas import (
    CharacterCreationSubmitPayload,
    EntityOverridePayload,
    MechanicsSwitchPayload,
    PromotePayload,
)

router = APIRouter()

# Plural URL segments for the world-owned kinds whose campaign-side overrides
# route through ``WorldService`` (characters have their own route below).
_WORLD_KIND_SEGMENTS: frozenset[str] = frozenset(
    {"items", "locations", "lore", "factions", "greetings", "monsters"}
)


@router.get("/{campaign_id}/characters")
async def list_characters(campaign_id: str, characters: CharactersDep) -> Any:
    try:
        return to_payload(await characters.list_for_campaign(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/cast")
async def list_cast(campaign_id: str, characters: CharactersDep, scenes: ScenesDep) -> Any:
    """Dramatis personae: the resolved characters that are part of play.

    A character is in the cast when it is a PC, is emergent (campaign-local
    characters only exist because play created them), or has appeared in at
    least one scene's declared or present cast. Library characters no scene
    has touched belong to the World → Characters view instead.
    """
    try:
        rows = await characters.list_for_campaign(campaign_id)
        pcs = await characters.list_pcs(campaign_id)
        scene_rows = await scenes.list_scenes(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    in_play = _in_play_refs(pcs, scene_rows)
    # A character that entered and later left is gone from the sidecar's
    # membership fields (remove_present_character strips declared refs too),
    # but appearance is historical — the confirmed cast-change log is the
    # durable record, so union it in.
    for scene in scene_rows:
        try:
            confirmed = await scenes.list_confirmed_cast_changes(scene.id)
        except Exception as exc:
            raise map_lookup_errors(exc) from exc
        for change in confirmed:
            in_play.add(canonicalize_character_ref(change.character_ref))
    return to_payload([row for row in rows if _in_cast(row, in_play)])


def _in_play_refs(pcs: Any, scene_rows: Any) -> set[str]:
    """Canonical refs that count as "in play": every PC plus every ref any
    scene declared or marked present. Spellings are normalized so membership
    checks line up regardless of how the ref was stored (#464, #517).
    """
    refs: set[str] = set()
    for entry in pcs:
        # The service returns ``PCEntry`` models; the raw store (and test
        # fakes) return dict rows.
        ref = entry["character_ref"] if isinstance(entry, dict) else entry.character_ref
        refs.add(canonicalize_character_ref(ref))
    for scene in scene_rows:
        declared = scene.declared_character_refs or []
        for ref in [*scene.present_character_refs, *scene.present_pc_refs, *declared]:
            refs.add(canonicalize_character_ref(ref))
    return refs


def _in_cast(row: Any, in_play: set[str]) -> bool:
    char = row.character
    if not char.world_id:
        return True
    return f"library:worlds/{char.world_id}/characters/{char.id}" in in_play


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


@router.get("/{campaign_id}/greetings")
async def list_greetings(campaign_id: str, world: WorldDep) -> Any:
    """Cascade-resolved greetings for the campaign (#600).

    Replaces the frontend's per-world library fan-out: rows honour the
    composition's per-ref ``include`` filters and carry real source chains,
    so emergent greetings and overrides surface like every other kind.
    """
    try:
        return await _list_kind(campaign_id, "greeting", world)
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
    payload: EntityOverridePayload,
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


@router.patch("/{campaign_id}/{kind}/{entity_id}/override")
async def patch_entity_override(
    campaign_id: str,
    kind: str,
    entity_id: str,
    payload: EntityOverridePayload,
    world: WorldDep,
) -> Any:
    """Campaign-local override for the non-character kinds (#600).

    Mirrors ``patch_character_override``. World owns the whole lookup:
    it resolves the owning world from the campaign's composition unless the
    payload names one, rejects emergent-only / emergent-shadowed targets
    (409 — they are campaign-local source of truth, so an override could
    never surface), and 404s targets that don't exist in the named world.
    """
    if kind == "characters":
        raise HTTPException(status_code=404, detail="use /characters/{id}/override")
    if kind not in _WORLD_KIND_SEGMENTS:
        raise HTTPException(status_code=404, detail=f"unknown entity kind {kind!r}")
    try:
        world_id = await world.upsert_override(
            campaign_id,
            kind,
            entity_id,
            payload.override,
            world_id=payload.world_id,
            source=payload.source,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "ok": True,
        "world_id": world_id,
        "ref": f"library:worlds/{world_id}/{kind}/{entity_id}",
    }


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
