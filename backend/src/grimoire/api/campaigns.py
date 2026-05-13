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
    SettingDep,
    StateStoreDep,
    TimeEngineDep,
)
from grimoire.api.util import map_lookup_errors, to_payload

router = APIRouter(prefix="/campaigns")


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class SettingRefPayload(BaseModel):
    setting_id: str
    priority: int = 1
    include: list[str] = Field(default_factory=list)
    track_latest: bool = False
    bound_at_version: int | None = None


class CompositionPayload(BaseModel):
    settings: list[SettingRefPayload] = Field(default_factory=list)
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
    target_setting_id: str
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
        )
        for ref in comp.settings:
            await state_store.upsert_setting_ref(
                campaign_id=payload.id,
                setting_id=ref.setting_id,
                priority=ref.priority,
                include=list(ref.include),
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
    from grimoire.types.composition import Composition, SettingRef

    try:
        comp = Composition(
            settings=[SettingRef(**ref.model_dump()) for ref in payload.settings],
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
async def add_setting_ref(
    campaign_id: str,
    ref: SettingRefPayload,
    state_store: StateStoreDep,
) -> Any:
    try:
        await state_store.upsert_setting_ref(
            campaign_id=campaign_id,
            setting_id=ref.setting_id,
            priority=ref.priority,
            include=list(ref.include),
            track_latest=ref.track_latest,
            bound_at_version=ref.bound_at_version,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.delete("/{campaign_id}/composition/refs/{setting_id}", status_code=204)
async def remove_setting_ref(
    campaign_id: str,
    setting_id: str,
    state_store: StateStoreDep,
) -> None:
    await state_store.db.execute(
        "DELETE FROM campaign_setting_refs WHERE campaign_id = ? AND setting_id = ?",
        (campaign_id, setting_id),
    )


@router.post("/{campaign_id}/composition/refs/{setting_id}/upgrade")
async def upgrade_setting_ref(
    campaign_id: str,
    setting_id: str,
    library: LibraryDep,
) -> Any:
    try:
        return to_payload(await library.upgrade_setting_ref(campaign_id, setting_id))
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


# --------------------------------------------------------------------------- #
# Resolved per-campaign entity views
# --------------------------------------------------------------------------- #


@router.get("/{campaign_id}/characters")
async def list_characters(campaign_id: str, characters: CharactersDep) -> Any:
    try:
        return to_payload(await characters.list_for_campaign(campaign_id))
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


async def _list_kind(campaign_id: str, kind: str, setting: Any) -> Any:
    return to_payload(await setting.list_for_campaign(campaign_id, kind))


@router.get("/{campaign_id}/items")
async def list_items(campaign_id: str, setting: SettingDep) -> Any:
    try:
        return await _list_kind(campaign_id, "item", setting)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/locations")
async def list_locations(campaign_id: str, setting: SettingDep) -> Any:
    try:
        return await _list_kind(campaign_id, "location", setting)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/lore")
async def list_lore(campaign_id: str, setting: SettingDep) -> Any:
    try:
        return await _list_kind(campaign_id, "lore", setting)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/factions")
async def list_factions(campaign_id: str, setting: SettingDep) -> Any:
    try:
        return await _list_kind(campaign_id, "faction", setting)
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
                payload.target_setting_id,
                source=payload.source,
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
    setting: SettingDep,
) -> Any:
    if kind == "characters":
        raise HTTPException(status_code=404, detail="use /characters/{id}/promote-to-library")
    try:
        return to_payload(
            await setting.promote_to_library(
                campaign_id,
                kind.rstrip("s"),
                entity_id,
                payload.target_setting_id,
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
    mechanics: MechanicsDep,
    payload: Annotated[dict[str, Any], Body()],
) -> Any:
    # Use the active mechanics module id so the file path is correct.
    module_id = "null"
    try:
        registered = mechanics.installed()
        if registered:
            module_id = registered[0].manifest.id
    except Exception:
        pass
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
