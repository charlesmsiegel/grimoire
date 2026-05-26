"""Campaign scene routes."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import CharactersDep, ContainerDep, LibraryDep, ScenesDep, StateStoreDep
from grimoire.api.util import map_lookup_errors, to_payload

from .helpers import _require_scene_owned, _seed_greeting_first_post
from .schemas import SceneSummary, SceneUpdatePayload

logger = logging.getLogger(__name__)

router = APIRouter()

_reconciled_campaigns: set[str] = set()


async def _reconcile_emergent_pcs(
    campaign_id: str, scene: Any, characters: Any, store: Any
) -> None:
    """Auto-create stub emergent characters and PC registrations for
    ``present_pc_refs`` entries with the ``emergent/`` prefix that don't
    exist yet. Runs at most once per campaign per process lifetime."""
    if campaign_id in _reconciled_campaigns:
        return
    _reconciled_campaigns.add(campaign_id)
    from grimoire.types.characters import CharacterData, CharacterRole

    existing_pcs = {p["character_ref"] for p in await store.list_pcs(campaign_id)}
    for ref in scene.present_pc_refs or []:
        if not ref.startswith("emergent/"):
            continue
        asset_id = ref.split("/", 1)[1]
        emergent = await store.get_emergent(campaign_id, "character", asset_id)
        if emergent is None:
            display = asset_id.replace("-", " ").title()
            data = CharacterData(
                id=asset_id,
                name=display,
                role=CharacterRole.PC,
                description=f"Auto-created from scene reference '{ref}'.",
            )
            try:
                await characters.create_emergent(campaign_id, data)
                logger.info(
                    "auto-created emergent character %r for campaign %s",
                    asset_id,
                    campaign_id,
                )
            except Exception:
                logger.warning(
                    "failed to auto-create emergent character %r for campaign %s",
                    asset_id,
                    campaign_id,
                    exc_info=True,
                )
                continue
        if ref not in existing_pcs:
            display = asset_id.replace("-", " ").title()
            try:
                await characters.add_pc(campaign_id, ref, display)
                logger.info("auto-registered PC %r for campaign %s", ref, campaign_id)
            except Exception:
                logger.warning(
                    "failed to auto-register PC %r for campaign %s",
                    ref,
                    campaign_id,
                    exc_info=True,
                )


@router.get("/{campaign_id}/scenes", response_model=list[SceneSummary])
async def list_scenes(campaign_id: str, scenes: ScenesDep) -> Any:
    try:
        return to_payload(await scenes.list_scenes(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/scenes/{scene_id}")
async def get_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
    characters: CharactersDep,
    state_store: StateStoreDep,
) -> Any:
    try:
        scene = await _require_scene_owned(scenes, campaign_id, scene_id)
        await _reconcile_emergent_pcs(campaign_id, scene, characters, state_store)
        body = await scenes.load_scene_body(scene_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "scene": to_payload(scene),
        "body": body,
        "posts": [],
    }


@router.get("/{campaign_id}/scenes/{scene_id}/posts")
async def get_scene_posts(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
    container: ContainerDep,
    limit: int = 50,
    before: int | None = None,
) -> Any:
    try:
        await _require_scene_owned(scenes, campaign_id, scene_id)
        clamped = max(1, min(limit, 200))
        posts = await scenes.get_posts_paginated(
            scene_id,
            limit=clamped,
            before=before,
            db=container.db,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "posts": to_payload(posts),
        "has_more": len(posts) == clamped,
    }


@router.patch("/{campaign_id}/scenes/{scene_id}")
async def update_scene(
    campaign_id: str,
    scene_id: str,
    payload: SceneUpdatePayload,
    scenes: ScenesDep,
    state_store: StateStoreDep,
) -> Any:
    from grimoire.scenes.narrator_mode import (
        RESPONSE_MODES,
        effective_response_mode,
        normalize_response_mode,
    )

    try:
        scene = await _require_scene_owned(scenes, campaign_id, scene_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc

    touched_narrator = False
    new_mode: str | None = scene.narrator_response_mode

    if payload.clear_narrator_response_mode:
        new_mode = None
        touched_narrator = True
    elif payload.narrator_response_mode is not None:
        normalized = normalize_response_mode(payload.narrator_response_mode)
        if normalized is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"narrator_response_mode must be one of {list(RESPONSE_MODES)} "
                    f"or null, got {payload.narrator_response_mode!r}"
                ),
            )
        new_mode = normalized
        touched_narrator = True

    if touched_narrator:
        try:
            scene = await scenes.set_narrator_response_mode(scene_id, new_mode)
        except Exception as exc:
            raise map_lookup_errors(exc) from exc

    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    effective = effective_response_mode(
        scene_override=scene.narrator_response_mode,
        campaign_row=dict(row) if row else None,
    )
    return {
        "scene": to_payload(scene),
        "narrator_response_mode": {
            "scene_override": scene.narrator_response_mode,
            "effective": effective,
        },
    }


@router.post("/{campaign_id}/scenes/{scene_id}/end")
async def end_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
) -> Any:
    try:
        await _require_scene_owned(scenes, campaign_id, scene_id)
        return to_payload(await scenes.close_scene(scene_id, closed_at_turn="manual"))
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/scenes/seed", status_code=201)
async def seed_first_scene(
    campaign_id: str,
    state_store: StateStoreDep,
    library: LibraryDep,
    scenes: ScenesDep,
    container: ContainerDep,
) -> Any:
    from grimoire.scenes.types import SceneInit

    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    camp = dict(row)

    existing = await scenes.list_scenes(campaign_id)
    if existing:
        return {"scene": to_payload(existing[0]), "created": False}

    greeting_id = camp.get("greeting_id")
    if not greeting_id:
        raise HTTPException(
            status_code=409,
            detail=f"campaign {campaign_id!r} has no greeting_id; cannot seed an opening scene",
        )

    composition = await library.get_composition(campaign_id)
    world_refs = getattr(composition, "worlds", []) or []

    greeting = None
    world_id = None
    for ref in world_refs:
        world_id = getattr(ref, "world_id", None) or ref.get("world_id")  # type: ignore[union-attr]
        if not world_id:
            continue
        try:
            greeting = await library.get_greeting(world_id, greeting_id)
            break
        except Exception:
            continue
    if greeting is None:
        raise HTTPException(
            status_code=404,
            detail=f"greeting {greeting_id!r} not found in any world on campaign {campaign_id!r}",
        )

    pc_rows = await state_store.list_pcs(campaign_id)
    pc_refs = [r["character_ref"] for r in pc_rows if r.get("character_ref")]

    in_game_start: datetime | None = None
    starting_time = getattr(greeting, "starting_time", None)
    if isinstance(starting_time, str) and starting_time:
        try:
            in_game_start = datetime.fromisoformat(starting_time)
        except ValueError:
            in_game_start = None

    init = SceneInit(
        campaign_id=campaign_id,
        branch_id="main",
        title=greeting.name or "Opening",
        location_ref=greeting.starting_location,
        in_game_start=in_game_start,
        present_character_refs=list(greeting.present_characters or []),
        present_pc_refs=pc_refs,
        pov_character_ref=greeting.pov_character,
        greeting_id=greeting.id,
        mood=greeting.mood,
        tags=list(greeting.tags or []),
    )
    try:
        scene = await scenes.start_scene(init)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    try:
        await _seed_greeting_first_post(
            scenes=scenes,
            scene=scene,
            greeting=greeting,
            state_store=state_store,
            library=library,
            world_id=world_id,
        )
    except Exception:
        logger.warning(
            "greeting first-post append failed; scene 1 exists with empty body",
            exc_info=True,
        )
    # Populate the scene ledger with all greetings from the campaign's worlds.
    ledger = getattr(container, "scene_ledger", None)
    if ledger is not None:
        used_greeting_id = greeting.id if greeting else None
        for ref in world_refs:
            wid = getattr(ref, "world_id", None) or (
                ref.get("world_id") if isinstance(ref, dict) else None
            )
            if not wid:
                continue
            try:
                all_greetings = await library.list_greetings(wid)
            except Exception:
                continue
            for g in all_greetings:
                item_id = await ledger.add(
                    campaign_id=campaign_id,
                    summary=g.name or g.body[:80],
                    source="greeting",
                    greeting_id=g.id,
                    proposed_location=g.starting_location,
                )
                if g.id == used_greeting_id:
                    await ledger.mark_used(campaign_id, item_id, scene_id=scene.id)

    return {"scene": to_payload(scene), "created": True}
