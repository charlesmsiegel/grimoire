"""Campaign REST routes.

Covers everything under ``/campaigns/{id}`` from spec 14 §Backend contract:
CRUD, composition, PCs, turns, scenes, resolved entity views, sheets, facts,
commitments, time advance, images, exports, and the review queue.

Each endpoint is a thin wrapper that dispatches to a domain service from the
container. Errors are translated by :func:`grimoire.api.util.map_lookup_errors`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from grimoire.api.deps import (
    CharactersDep,
    ContinuityDep,
    ExportDep,
    ImageGenDep,
    LibraryDep,
    MechanicsDep,
    OrchestratorDep,
    ScenesDep,
    StateStoreDep,
    TimeEngineDep,
    WorldDep,
)
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter(prefix="/campaigns")


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class WorldRefPayload(BaseModel):
    world_id: str
    priority: int = 1
    # None / missing means "include every kind"; an explicit list (even empty)
    # is treated literally — `[]` excludes everything from this world.
    include: list[str] | None = None
    track_latest: bool = False
    bound_at_version: int | None = None


class CompositionPayload(BaseModel):
    worlds: list[WorldRefPayload] = Field(default_factory=list)
    mechanics: str | None = None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    inline_style_guide: str | None = None
    content_boundaries: str | None = None


class CampaignCreatePayload(BaseModel):
    id: str
    name: str
    description: str | None = None
    composition: CompositionPayload | None = None
    greeting_id: str | None = None
    tags: list[str] | None = None


class CampaignUpdatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    greeting_id: str | None = None
    style_guide_id: str | None = None
    image_preset_id: str | None = None
    inline_style_guide: str | None = None
    content_boundaries: str | None = None
    mechanics: str | None = None


class AddPCPayload(BaseModel):
    character_ref: str
    name: str
    owner: str = "local"


class SubmitTurnPayload(BaseModel):
    pc_ref: str
    text: str
    metadata: dict[str, Any] | None = None


class AdvanceTurnPayload(BaseModel):
    scene_id: str


class RetconPayload(BaseModel):
    post_id: str
    new_text: str


class UndoPayload(BaseModel):
    count: int = 1


class ForkPayload(BaseModel):
    from_turn_id: str
    label: str


class CreateFactPayload(BaseModel):
    fact: dict[str, Any]
    source: str = "user"


class TimeAdvancePayload(BaseModel):
    duration: dict[str, Any] | None = None
    target: str | None = None
    reason: str = "narrative"
    scene_id: str | None = None
    branch_id: str | None = None


class ImageGenPayload(BaseModel):
    scene_id: str | None = None
    post_id: str | None = None
    request: dict[str, Any] | None = None
    priority: int = 5


class ExportPayload(BaseModel):
    adapter_id: str
    selection: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class ReviewUpdatePayload(BaseModel):
    notes: str = ""


class PromotePayload(BaseModel):
    target_world_id: str
    source: str = "user"
    confirm: bool = False


# --------------------------------------------------------------------------- #
# Campaign CRUD
# --------------------------------------------------------------------------- #


@router.get("")
async def list_campaigns(state_store: StateStoreDep) -> Any:
    rows = await state_store.db.fetchall(
        "SELECT id, name, description, mechanics_module, style_guide_id, image_preset_id, "
        "created_at, last_played_at FROM campaigns ORDER BY id"
    )
    return [dict(row) for row in rows]


@router.post("", status_code=201)
async def create_campaign(
    payload: CampaignCreatePayload,
    state_store: StateStoreDep,
    library: LibraryDep,
) -> Any:
    comp = payload.composition or CompositionPayload()
    try:
        await state_store.upsert_campaign(
            campaign_id=payload.id,
            name=payload.name,
            description=payload.description,
            mechanics_module=comp.mechanics,
            style_guide_id=comp.style_guide_id,
            image_preset_id=comp.image_preset_id,
            inline_style_guide=comp.inline_style_guide,
            content_boundaries=comp.content_boundaries,
            greeting_id=payload.greeting_id,
            tags=payload.tags,
        )
        for ref in comp.worlds:
            await state_store.upsert_world_ref(
                campaign_id=payload.id,
                world_id=ref.world_id,
                priority=ref.priority,
                include=list(ref.include) if ref.include is not None else None,
                track_latest=ref.track_latest,
                bound_at_version=ref.bound_at_version,
            )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return await get_campaign(payload.id, state_store=state_store, library=library)


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    state_store: StateStoreDep,
    library: LibraryDep,
) -> Any:
    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    data = dict(row)
    try:
        data["composition"] = to_payload(await library.get_composition(campaign_id))
    except Exception:
        data["composition"] = None
    return data


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    payload: CampaignUpdatePayload,
    state_store: StateStoreDep,
) -> Any:
    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    current = dict(row)
    try:
        await state_store.upsert_campaign(
            campaign_id=campaign_id,
            name=payload.name if payload.name is not None else current["name"],
            description=payload.description
            if payload.description is not None
            else current.get("description"),
            mechanics_module=payload.mechanics
            if payload.mechanics is not None
            else current.get("mechanics_module"),
            style_guide_id=payload.style_guide_id
            if payload.style_guide_id is not None
            else current.get("style_guide_id"),
            image_preset_id=payload.image_preset_id
            if payload.image_preset_id is not None
            else current.get("image_preset_id"),
            inline_style_guide=payload.inline_style_guide
            if payload.inline_style_guide is not None
            else current.get("inline_style_guide"),
            content_boundaries=payload.content_boundaries
            if payload.content_boundaries is not None
            else current.get("content_boundaries"),
            greeting_id=payload.greeting_id
            if payload.greeting_id is not None
            else current.get("greeting_id"),
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    return dict(row) if row else None


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str, state_store: StateStoreDep) -> None:
    await state_store.db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# PCs
# --------------------------------------------------------------------------- #


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
) -> Any:
    try:
        return to_payload(
            await characters.add_pc(campaign_id, payload.character_ref, payload.name, payload.owner)
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/{campaign_id}/pcs/{character_ref:path}", status_code=204)
async def remove_pc(
    campaign_id: str,
    character_ref: str,
    characters: CharactersDep,
) -> None:
    try:
        await characters.remove_pc(campaign_id, character_ref)
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


# --------------------------------------------------------------------------- #
# Turns
# --------------------------------------------------------------------------- #


@router.post("/{campaign_id}/turns")
async def submit_turn(
    campaign_id: str,
    payload: SubmitTurnPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.submit_post(
            campaign_id, payload.pc_ref, payload.text, payload.metadata
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/turns/advance")
async def advance_turn(
    campaign_id: str,
    payload: AdvanceTurnPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.advance(campaign_id, payload.scene_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/turns/regenerate")
async def regenerate_turn(
    campaign_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.regenerate_last(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/turns/undo")
async def undo_turn(
    campaign_id: str,
    orchestrator: OrchestratorDep,
    payload: UndoPayload | None = None,
) -> Any:
    count = payload.count if payload else 1
    try:
        result = await orchestrator.undo_turn(campaign_id, count)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/turns/{turn_id}/retcon")
async def retcon_post(
    campaign_id: str,
    turn_id: str,
    payload: RetconPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.retcon_post(payload.post_id, payload.new_text)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/forks", status_code=201)
async def fork_campaign(
    campaign_id: str,
    payload: ForkPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.fork(campaign_id, payload.from_turn_id, payload.label)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #


@router.get("/{campaign_id}/scenes")
async def list_scenes(campaign_id: str, scenes: ScenesDep) -> Any:
    try:
        return to_payload(await scenes.list_scenes(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


async def _require_scene_owned(scenes: Any, campaign_id: str, scene_id: str) -> Any:
    """Resolve ``scene_id`` and reject when it doesn't belong to ``campaign_id``.

    ``SceneManager.get_scene`` looks up scenes by id across every campaign on
    disk, so without this guard a caller could read/close another campaign's
    scenes by guessing the id. Returns the resolved Scene for the caller.
    """
    scene = await scenes.get_scene(scene_id)
    if getattr(scene, "campaign_id", None) != campaign_id:
        raise HTTPException(
            status_code=404,
            detail=f"scene {scene_id!r} not found in campaign {campaign_id!r}",
        )
    return scene


@router.get("/{campaign_id}/scenes/{scene_id}")
async def get_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
) -> Any:
    try:
        scene = await _require_scene_owned(scenes, campaign_id, scene_id)
        body = await scenes.load_scene_body(scene_id)
        posts = await scenes.get_posts(scene_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "scene": to_payload(scene),
        "body": body,
        "posts": to_payload(posts),
    }


@router.post("/{campaign_id}/scenes/{scene_id}/end")
async def end_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
) -> Any:
    try:
        await _require_scene_owned(scenes, campaign_id, scene_id)
        return to_payload(await scenes.close_scene(scene_id))
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
) -> Any:
    """Materialize the opening scene from the campaign's greeting.

    The campaign-create flow stores ``greeting_id`` but does not yet
    instantiate a scene; without one ``submit_post`` has nowhere to
    append. This endpoint is idempotent — if any scene already exists
    it returns the earliest one instead of creating a duplicate. The
    frontend wizard calls it as the final step of campaign creation,
    after PCs have been added.
    """
    from grimoire.types.scene import SceneInit

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

    # greeting.starting_time is an ISO string in the world's calendar;
    # SceneInit.in_game_start wants InGameTime (a wrapped datetime). The
    # mapping isn't always 1:1 (world calendars can be non-Gregorian),
    # so leave it None for the seed and let the orchestrator's time
    # engine attach a moment when the first turn runs.
    init = SceneInit(
        campaign_id=campaign_id,
        branch_id="main",
        title=greeting.name or "Opening",
        location_ref=greeting.starting_location,
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
    return {"scene": to_payload(scene), "created": True}


# --------------------------------------------------------------------------- #
# Resolved per-campaign entity views
# --------------------------------------------------------------------------- #


@router.get("/{campaign_id}/characters")
async def list_characters(campaign_id: str, characters: CharactersDep) -> Any:
    try:
        return to_payload(await characters.list_for_campaign(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


async def _list_kind(campaign_id: str, kind: str, world: Any) -> Any:
    return to_payload(await world.list_for_campaign(campaign_id, kind))


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
                kind.rstrip("s"),
                entity_id,
                payload.target_world_id,
                source=payload.source,
            )
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Sheets
# --------------------------------------------------------------------------- #


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
    # Use the campaign's bound mechanics_module so the file path matches what
    # MechanicsService.get_sheet will read back. The previous implementation
    # picked the first installed module instead, which silently desynced read
    # and write paths whenever more than one mechanics plugin was installed
    # or the campaign was null-mechanics.
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


# --------------------------------------------------------------------------- #
# Facts + commitments
# --------------------------------------------------------------------------- #


@router.get("/{campaign_id}/facts")
async def list_facts(
    campaign_id: str,
    continuity: ContinuityDep,
    limit: int = 50,
) -> Any:
    # ContinuityService is a single shared instance with no campaign scope of
    # its own, so the route applies the filter to keep campaigns isolated.
    # Fetch with a generous internal limit so the caller's `limit` still works
    # after we discard rows that belong to other campaigns.
    try:
        all_facts = await continuity.facts_about(limit=max(limit * 8, 200))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    scoped = [f for f in all_facts if getattr(f, "campaign_id", None) == campaign_id]
    return to_payload(scoped[:limit])


@router.post("/{campaign_id}/facts", status_code=201)
async def create_fact(
    campaign_id: str,
    payload: CreateFactPayload,
    continuity: ContinuityDep,
) -> Any:
    from grimoire.types.continuity import Fact

    try:
        # Stamp campaign_id from the path so a forged payload can't write into
        # a different campaign's continuity.
        fact_data = {**payload.fact, "campaign_id": campaign_id}
        fact = Fact.model_validate(fact_data)
        fact_id = await continuity.add_fact(fact, source=payload.source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"fact_id": fact_id}


@router.get("/{campaign_id}/commitments")
async def list_commitments(
    campaign_id: str,
    continuity: ContinuityDep,
    limit: int = 50,
) -> Any:
    try:
        all_commitments = await continuity.open_commitments(limit=max(limit * 8, 200))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    scoped = [c for c in all_commitments if getattr(c, "campaign_id", None) == campaign_id]
    return to_payload(scoped[:limit])


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


@router.post("/{campaign_id}/time/advance")
async def time_advance(
    campaign_id: str,
    payload: TimeAdvancePayload,
    time_engine: TimeEngineDep,
) -> Any:
    from grimoire.types.common import Duration, InGameTime
    from grimoire.types.time import TimeAdvanceReason

    try:
        reason = TimeAdvanceReason(payload.reason)
        if payload.target is not None:
            target = InGameTime(moment=datetime.fromisoformat(payload.target))
            result = await time_engine.skip_to(
                campaign_id,
                target,
                reason,
                scene_id=payload.scene_id,
                branch_id=payload.branch_id,
            )
        else:
            duration = Duration.model_validate(payload.duration or {"minutes": 0})
            result = await time_engine.advance(
                campaign_id,
                duration,
                reason,
                scene_id=payload.scene_id,
                branch_id=payload.branch_id,
            )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


@router.post("/{campaign_id}/images/generate", status_code=202)
async def generate_image(
    campaign_id: str,
    payload: ImageGenPayload,
    imagegen: ImageGenDep,
) -> Any:
    from grimoire.types.imagegen import GenerationRequest

    try:
        request = (
            GenerationRequest.model_validate(payload.request)
            if payload.request is not None
            else None
        )
        job_id = await imagegen.queue_generation(
            campaign_id=campaign_id,
            scene_id=payload.scene_id,
            post_id=payload.post_id,
            request=request,
            priority=payload.priority,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


@router.get("/{campaign_id}/images")
async def list_images(
    campaign_id: str,
    imagegen: ImageGenDep,
    scene_id: str | None = None,
    starred_only: bool = False,
) -> Any:
    try:
        return to_payload(
            await imagegen.list_images(campaign_id, scene_id=scene_id, starred_only=starred_only)
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


class StarImagePayload(BaseModel):
    starred: bool


class SetActiveBackendPayload(BaseModel):
    backend_id: str


class PrioritizeJobPayload(BaseModel):
    priority: int = 5


class VariationPayload(BaseModel):
    strength: float = 0.6


class TriggerConfigPayload(BaseModel):
    mode: str = "per_scene"
    every_n: int = 5
    on_scene_open: bool = True
    on_new_location: bool = True
    on_new_character_appearance: bool = True
    auto_during_combat: bool = False


class FallbackBackendPayload(BaseModel):
    backend_id: str | None = None


@router.get("/{campaign_id}/imagegen/active")
async def get_active_backend(campaign_id: str, imagegen: ImageGenDep) -> Any:
    try:
        return to_payload(await imagegen.active_backend(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/imagegen/active")
async def set_active_backend(
    campaign_id: str,
    payload: SetActiveBackendPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_active_backend(campaign_id, payload.backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/imagegen/trigger")
async def get_imagegen_trigger(campaign_id: str, imagegen: ImageGenDep) -> Any:
    cfg = await imagegen.get_trigger_config(campaign_id)
    return {
        "mode": cfg.mode,
        "every_n": cfg.every_n,
        "on_scene_open": cfg.on_scene_open,
        "on_new_location": cfg.on_new_location,
        "on_new_character_appearance": cfg.on_new_character_appearance,
        "auto_during_combat": cfg.auto_during_combat,
    }


@router.put("/{campaign_id}/imagegen/trigger")
async def set_imagegen_trigger(
    campaign_id: str,
    payload: TriggerConfigPayload,
    imagegen: ImageGenDep,
) -> Any:
    from grimoire.imagegen import TriggerConfig

    await imagegen.set_trigger_config(
        campaign_id,
        TriggerConfig(
            mode=payload.mode,
            every_n=payload.every_n,
            on_scene_open=payload.on_scene_open,
            on_new_location=payload.on_new_location,
            on_new_character_appearance=payload.on_new_character_appearance,
            auto_during_combat=payload.auto_during_combat,
        ),
    )
    return {"ok": True}


@router.get("/{campaign_id}/imagegen/fallback")
async def get_imagegen_fallback(campaign_id: str, imagegen: ImageGenDep) -> Any:
    return {"backend_id": await imagegen.get_fallback_backend(campaign_id)}


@router.put("/{campaign_id}/imagegen/fallback")
async def set_imagegen_fallback(
    campaign_id: str,
    payload: FallbackBackendPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.set_fallback_backend(campaign_id, payload.backend_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/images/jobs")
async def list_image_jobs(
    campaign_id: str,
    imagegen: ImageGenDep,
    status: str | None = None,
) -> Any:
    from grimoire.types.imagegen import JobStatus

    try:
        status_enum = JobStatus(status) if status else None
        return to_payload(await imagegen.list_jobs(campaign_id, status=status_enum))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.delete("/{campaign_id}/images/jobs/{job_id}", status_code=204)
async def cancel_image_job(
    campaign_id: str,
    job_id: str,
    imagegen: ImageGenDep,
) -> None:
    try:
        await imagegen.cancel_job(job_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.patch("/{campaign_id}/images/jobs/{job_id}")
async def prioritize_image_job(
    campaign_id: str,
    job_id: str,
    payload: PrioritizeJobPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.prioritize_job(job_id, payload.priority)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.get("/{campaign_id}/images/{image_id}")
async def get_image(
    campaign_id: str,
    image_id: str,
    imagegen: ImageGenDep,
) -> Any:
    try:
        return to_payload(await imagegen.get_image(image_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.put("/{campaign_id}/images/{image_id}/star")
async def star_image(
    campaign_id: str,
    image_id: str,
    payload: StarImagePayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        await imagegen.star_image(image_id, payload.starred)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.delete("/{campaign_id}/images/{image_id}", status_code=204)
async def delete_image(
    campaign_id: str,
    image_id: str,
    imagegen: ImageGenDep,
) -> None:
    try:
        await imagegen.delete_image(image_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/images/{image_id}/reroll", status_code=202)
async def reroll_image(
    campaign_id: str,
    image_id: str,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.reroll(image_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


@router.post("/{campaign_id}/images/{image_id}/variation", status_code=202)
async def variation_image(
    campaign_id: str,
    image_id: str,
    payload: VariationPayload,
    imagegen: ImageGenDep,
) -> Any:
    try:
        job_id = await imagegen.variation(image_id, payload.strength)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


class EditAndRegeneratePayload(BaseModel):
    prompt: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] | None = None
    keep_seed: bool = False


@router.post("/{campaign_id}/images/{image_id}/edit", status_code=202)
async def edit_and_regenerate_image(
    campaign_id: str,
    image_id: str,
    payload: EditAndRegeneratePayload,
    imagegen: ImageGenDep,
) -> Any:
    del campaign_id  # kept in URL for symmetry / future ownership check
    try:
        job_id = await imagegen.edit_and_regenerate(
            image_id,
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            params=payload.params,
            keep_seed=payload.keep_seed,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"job_id": job_id}


class SetTagsPayload(BaseModel):
    tags: list[str] = Field(default_factory=list)


@router.put("/{campaign_id}/images/{image_id}/tags")
async def set_image_tags(
    campaign_id: str,
    image_id: str,
    payload: SetTagsPayload,
    imagegen: ImageGenDep,
) -> Any:
    del campaign_id
    try:
        await imagegen.set_tags(image_id, payload.tags)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


@router.post("/{campaign_id}/export")
async def export_campaign(
    campaign_id: str,
    payload: ExportPayload,
    export: ExportDep,
) -> Any:
    from grimoire.types.export import ExportOptions, ExportSelection

    try:
        selection = ExportSelection.model_validate(payload.selection)
        options = ExportOptions.model_validate(payload.options)
        result = await export.export(campaign_id, payload.adapter_id, selection, options)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


# --------------------------------------------------------------------------- #
# Review queue
# --------------------------------------------------------------------------- #


async def _require_review_owned(state_store: Any, campaign_id: str, review_id: str) -> None:
    """Reject the request if ``review_id`` is not scoped to ``campaign_id``.

    The store's approve/reject methods key only on review id, so the routes
    enforce ownership here to prevent IDOR — a caller cannot affect another
    campaign's review item by guessing its id.
    """
    row = await state_store.db.fetchone(
        "SELECT campaign_id FROM review_queue WHERE id = ?",
        (review_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"review item {review_id!r} not found")
    if row["campaign_id"] != campaign_id:
        raise HTTPException(
            status_code=404,
            detail=f"review item {review_id!r} not found in campaign {campaign_id!r}",
        )


@router.post("/{campaign_id}/reviews/{review_id}/approve")
async def approve_review(
    campaign_id: str,
    review_id: str,
    state_store: StateStoreDep,
) -> Any:
    await _require_review_owned(state_store, campaign_id, review_id)
    try:
        delta_id = await state_store.approve_review_item(review_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"delta_id": delta_id}


@router.post("/{campaign_id}/reviews/{review_id}/reject")
async def reject_review(
    campaign_id: str,
    review_id: str,
    state_store: StateStoreDep,
    payload: ReviewUpdatePayload | None = None,
) -> Any:
    await _require_review_owned(state_store, campaign_id, review_id)
    try:
        await state_store.reject_review_item(review_id, notes=payload.notes if payload else "")
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.patch("/{campaign_id}/reviews/{review_id}")
async def update_review(
    campaign_id: str,
    review_id: str,
    payload: ReviewUpdatePayload,
    state_store: StateStoreDep,
) -> Any:
    await _require_review_owned(state_store, campaign_id, review_id)
    try:
        await state_store.db.execute(
            "UPDATE review_queue SET reviewer_notes = ? WHERE id = ? AND campaign_id = ?",
            (payload.notes, review_id, campaign_id),
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


__all__ = ["router"]
