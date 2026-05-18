"""Campaign REST routes.

Covers everything under ``/campaigns/{id}`` from spec 14 §Backend contract:
CRUD, composition, PCs, turns, scenes, resolved entity views, sheets, facts,
commitments, time advance, images, exports, and the review queue.

Each endpoint is a thin wrapper that dispatches to a domain service from the
container. Errors are translated by :func:`grimoire.api.util.map_lookup_errors`.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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


class CharacterOverridePayload(BaseModel):
    override: dict[str, Any]
    world_id: str | None = None
    source: str = "user"


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
    world: WorldDep,
    scenes: ScenesDep,
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
        # §8 Greeting handoff: when a greeting is selected, seed scene 1
        # from it. Best-effort — a missing greeting shouldn't abort the
        # whole campaign creation. We pick the highest-priority world ref
        # (lowest priority number) to own the greeting lookup.
        if payload.greeting_id and comp.worlds:
            owning_ref = sorted(comp.worlds, key=lambda r: r.priority)[0]
            try:
                await world.seed_scene_from_greeting(
                    campaign_id=payload.id,
                    greeting_id=payload.greeting_id,
                    world_id=owning_ref.world_id,
                    scene_manager=scenes,
                )
            except Exception:
                logger.warning("greeting handoff failed; scene 1 not seeded", exc_info=True)
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
# Settings tabs (spec §15: Routing / ImageGen / Storage / Advanced)
# --------------------------------------------------------------------------- #
#
# All four payloads are stored as namespaced keys inside the
# ``campaigns.config`` JSON column. ImageGen settings here are decoupled from
# any backend-specific imagegen state stored elsewhere; downstream tasks that
# unify the two surfaces can read this dict as ``config["imagegen"]``.
#
# Each pair is GET (returns the persisted shape, falling back to a stable
# default when the campaign hasn't saved anything yet) + PUT (overwrite).


async def _require_campaign_row(state_store: Any, campaign_id: str) -> dict:
    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    return dict(row)


def _load_campaign_config(row: dict) -> dict:
    """Best-effort parse of ``campaigns.config`` into a dict."""
    import json as _json

    raw = row.get("config")
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _write_campaign_config(state_store: Any, campaign_id: str, config: dict) -> None:
    import json as _json

    await state_store.db.execute(
        "UPDATE campaigns SET config = ? WHERE id = ?",
        (_json.dumps(config, sort_keys=True), campaign_id),
    )


class RoutingPayload(BaseModel):
    llm: dict[str, str | None] = Field(default_factory=dict)
    embedding: dict[str, str | None] = Field(default_factory=dict)


class ImageGenSettingsPayload(BaseModel):
    backend: str | None = None
    preset: str | None = None
    sampler_defaults: dict[str, Any] | str | None = None


class StorageSettingsPayload(BaseModel):
    schedule: str = "off"
    retention_days: int = 30


class AdvancedSettingsPayload(BaseModel):
    debug_log: bool = False
    per_task_prompts: dict[str, str] = Field(default_factory=dict)


@router.get("/{campaign_id}/routing")
async def get_campaign_routing(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    routing = cfg.get("routing") or {}
    return {
        "llm": dict(routing.get("llm") or {}),
        "embedding": dict(routing.get("embedding") or {}),
    }


@router.put("/{campaign_id}/routing")
async def set_campaign_routing(
    campaign_id: str,
    payload: RoutingPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["routing"] = {
        "llm": {k: v for k, v in payload.llm.items() if v is not None and v != ""},
        "embedding": {k: v for k, v in payload.embedding.items() if v is not None and v != ""},
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["routing"]


@router.get("/{campaign_id}/imagegen")
async def get_campaign_imagegen(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    imagegen = cfg.get("imagegen") or {}
    return {
        "backend": imagegen.get("backend"),
        "preset": imagegen.get("preset"),
        "sampler_defaults": imagegen.get("sampler_defaults"),
    }


@router.put("/{campaign_id}/imagegen")
async def set_campaign_imagegen(
    campaign_id: str,
    payload: ImageGenSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["imagegen"] = {
        "backend": payload.backend or None,
        "preset": payload.preset or None,
        "sampler_defaults": payload.sampler_defaults,
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["imagegen"]


@router.get("/{campaign_id}/storage")
async def get_campaign_storage(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    storage = cfg.get("storage") or {}
    return {
        "schedule": storage.get("schedule", "off"),
        "retention_days": int(storage.get("retention_days", 30)),
    }


@router.put("/{campaign_id}/storage")
async def set_campaign_storage(
    campaign_id: str,
    payload: StorageSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["storage"] = {
        "schedule": payload.schedule,
        "retention_days": int(payload.retention_days),
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["storage"]


@router.get("/{campaign_id}/advanced")
async def get_campaign_advanced(campaign_id: str, state_store: StateStoreDep) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    advanced = cfg.get("advanced") or {}
    return {
        "debug_log": bool(advanced.get("debug_log", False)),
        "per_task_prompts": dict(advanced.get("per_task_prompts") or {}),
    }


@router.put("/{campaign_id}/advanced")
async def set_campaign_advanced(
    campaign_id: str,
    payload: AdvancedSettingsPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["advanced"] = {
        "debug_log": bool(payload.debug_log),
        "per_task_prompts": {
            k: v for k, v in payload.per_task_prompts.items() if isinstance(v, str)
        },
    }
    await _write_campaign_config(state_store, campaign_id, cfg)
    return cfg["advanced"]


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
        # No turn id is available at this endpoint (a player-initiated end is
        # not tied to an orchestrator turn). Use a stable sentinel so the
        # ``closed_at_turn`` audit column always has a value; downstream
        # tooling treats "manual" as "ended outside the play loop".
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


@router.patch("/{campaign_id}/characters/{entity_id}/override")
async def patch_character_override(
    campaign_id: str,
    entity_id: str,
    payload: CharacterOverridePayload,
    characters: CharactersDep,
) -> Any:
    """Write a campaign-local override against a library character.

    ``entity_id`` is the character's library asset id (e.g. ``alistair``).
    The owning ``world_id`` is taken from ``payload.world_id`` when
    supplied; otherwise the server scans the campaign's resolved cast for
    a library-backed entry whose id matches.
    """
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


@router.post("/{campaign_id}/sheets/bulk-create-missing")
async def bulk_create_missing_sheets(
    campaign_id: str,
    state_store: StateStoreDep,
    mechanics: MechanicsDep,
    characters: CharactersDep,
    world: WorldDep,
) -> Any:
    """Initialise sheets for every entity reachable from the campaign that
    doesn't already have one under the active mechanics module.

    Returns ``{"created": [...], "skipped": [...]}`` where each row is
    ``{kind, entity_id}``. Skipped rows already had a sheet on disk; the
    request is idempotent.
    """
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
        # Fall back to the manifest's declared sheet_kinds.
        manifest = await mechanics.module_info(module_id)
        sheet_kinds = list(manifest.sheet_kinds) if manifest else []
    if not sheet_kinds:
        sheet_kinds = ["character"]

    # Build the entity inventory once per kind.
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
        for entity_id in entity_ids:
            existing = await state_store.get_sheet(
                campaign_id=campaign_id,
                kind=kind,
                entity_id=entity_id,
                mechanics_id=module_id,
            )
            if existing is not None:
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


def _continuity_for(continuity_dep: Any, campaign_id: str) -> Any:
    """Resolve a per-campaign Continuity from either a registry or a
    single shared service.

    Routes that came in via ``ContinuityDep`` see the container's
    ``.continuity`` attribute, which is a :class:`ContinuityRegistry` in
    production and a single :class:`ContinuityService` in tests. The
    helper papers over both shapes so route handlers stay tidy.
    """
    from grimoire.continuity.registry import resolve_continuity

    return resolve_continuity(continuity_dep, campaign_id)


@router.get("/{campaign_id}/facts")
async def list_facts(
    campaign_id: str,
    continuity: ContinuityDep,
    limit: int = 50,
) -> Any:
    service = _continuity_for(continuity, campaign_id)
    try:
        facts = await service.facts_about(limit=limit)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(facts)


@router.post("/{campaign_id}/facts", status_code=201)
async def create_fact(
    campaign_id: str,
    payload: CreateFactPayload,
    continuity: ContinuityDep,
) -> Any:
    from grimoire.types.continuity import Fact

    service = _continuity_for(continuity, campaign_id)
    try:
        # Stamp campaign_id from the path so a forged payload can't write into
        # a different campaign's continuity.
        fact_data = {**payload.fact, "campaign_id": campaign_id}
        fact = Fact.model_validate(fact_data)
        fact_id = await service.add_fact(fact, source=payload.source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"fact_id": fact_id}


@router.get("/{campaign_id}/commitments")
async def list_commitments(
    campaign_id: str,
    continuity: ContinuityDep,
    limit: int = 50,
) -> Any:
    service = _continuity_for(continuity, campaign_id)
    try:
        commitments = await service.open_commitments(limit=limit)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(commitments)


@router.get("/{campaign_id}/continuity/ledger")
async def continuity_ledger(
    campaign_id: str,
    continuity: ContinuityDep,
    limit_facts: int = 20,
    limit_commitments: int = 20,
) -> Any:
    """Single-shot view of the campaign's continuity state.

    Returns the open / overdue / stale commitment lists, the most recent
    facts, and any unresolved contradiction reports — the surfaces the
    §10 "campaign ledger" panel needs in one round-trip so the Frontend
    doesn't fan out to multiple endpoints.

    ``open_commitments`` returns both OPEN and OVERDUE rows (the aging
    engine flips a passed due-date to OVERDUE on time advance); the
    response splits them for the UI rather than asking the caller to
    re-filter by status.
    """
    from grimoire.continuity.types import Duration

    service = _continuity_for(continuity, campaign_id)
    try:
        active_rows = await service.open_commitments(limit=limit_commitments * 2)
        overdue_rows = [
            c for c in active_rows if getattr(getattr(c, "status", None), "value", "") == "overdue"
        ]
        open_rows = [
            c for c in active_rows if getattr(getattr(c, "status", None), "value", "") != "overdue"
        ][:limit_commitments]
        stale_rows = await service.stale_commitments(Duration.months(6))
        recent_facts = await service.facts_about(limit=limit_facts)
        unresolved = await service.pending_contradictions(limit=20)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "campaign_id": campaign_id,
        "open_commitments": to_payload(open_rows),
        "overdue_commitments": to_payload(overdue_rows),
        "stale_commitments": to_payload(stale_rows),
        "recent_facts": to_payload(recent_facts),
        "unresolved_contradictions": to_payload(unresolved),
    }


@router.get("/{campaign_id}/continuity/contradictions")
async def list_contradiction_reports_route(
    campaign_id: str,
    continuity: ContinuityDep,
    resolved: bool | None = None,
    limit: int = 50,
) -> Any:
    """Enumerate contradiction reports for the resolution UI.

    ``resolved=None`` (the default) returns both resolved and pending;
    pass ``resolved=false`` to drive the "conflict detected — pick a
    resolution" panel.
    """
    service = _continuity_for(continuity, campaign_id)
    try:
        store = getattr(service, "_store", None)
        if store is None or not hasattr(store, "list_contradiction_reports"):
            reports = await service.pending_contradictions(limit=limit)
        else:
            reports = await store.list_contradiction_reports(resolved=resolved, limit=limit)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(reports)


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


@router.get("/{campaign_id}/exports/adapters")
async def list_export_adapters(
    campaign_id: str,
    export: ExportDep,
) -> Any:
    del campaign_id  # adapter set is global, not per-campaign
    out: list[dict[str, Any]] = []
    for adapter in export.list_adapters():
        capabilities = getattr(adapter, "capabilities", None)
        try:
            option_schema = adapter.option_schema()
        except Exception:  # pragma: no cover — defensive
            option_schema = {}
        out.append(
            {
                "id": adapter.id,
                "name": getattr(adapter, "name", adapter.id),
                "extensions": list(getattr(adapter, "extensions", []) or []),
                "mime_type": getattr(adapter, "mime_type", "application/octet-stream"),
                "capabilities": (capabilities.model_dump() if capabilities is not None else None),
                "option_schema": option_schema,
            }
        )
    return {"adapters": out}


@router.post("/{campaign_id}/exports/preview")
async def preview_export(
    campaign_id: str,
    payload: ExportPayload,
    export: ExportDep,
) -> Any:
    from grimoire.types.export import ExportOptions, ExportSelection

    try:
        selection = ExportSelection.model_validate(payload.selection)
        options = ExportOptions.model_validate(payload.options)
        preview = await export.preview(campaign_id, payload.adapter_id, selection, options)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(preview)


@router.get("/{campaign_id}/exports")
async def list_export_history(
    campaign_id: str,
    export: ExportDep,
    limit: int | None = None,
) -> Any:
    try:
        records = await export.history(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    if limit is not None and limit >= 0:
        # Slice as ``records[-limit:]`` only when limit > 0; ``records[-0:]``
        # is ``records[0:]`` (all rows), not an empty list.
        records = records[-limit:] if limit > 0 else []
    return {"records": [to_payload(record) for record in records]}


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
