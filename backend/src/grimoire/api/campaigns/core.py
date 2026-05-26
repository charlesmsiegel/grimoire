"""Campaign CRUD routes: list, rescan, create, get, update, delete."""

from __future__ import annotations

import logging
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from grimoire.api.deps import (
    FileWatcherDep,
    LibraryDep,
    LLMGatewayDep,
    MechanicsDep,
    ScenesDep,
    StateStoreDep,
    WorldDep,
)
from grimoire.api.util import map_lookup_errors, to_payload
from grimoire.state_store.paths import campaigns_root

from .helpers import (
    _load_campaign_config,
    _seed_greeting_first_post,
    _write_campaign_config,
)
from .schemas import (
    CampaignCreatePayload,
    CampaignSummary,
    CampaignUpdatePayload,
    CompositionPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[CampaignSummary])
async def list_campaigns(state_store: StateStoreDep) -> Any:
    rows = await state_store.db.fetchall(
        "SELECT id, name, description, mechanics_module, style_guide_id, image_preset_id, "
        "created_at, last_played_at, forked_from_campaign_id, forked_at_post_id, "
        "forked_at_turn_id, forked_image_handling FROM campaigns ORDER BY id"
    )
    return [dict(row) for row in rows]


@router.post("/rescan")
async def rescan_campaigns(file_watcher: FileWatcherDep, request: Request) -> Any:
    try:
        result = await file_watcher.scan_now(scope="campaigns")
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    container = getattr(request.app.state, "container", None)
    if container and getattr(container, "scene_indexer", None):
        try:
            await container.scene_indexer.backfill()
        except Exception:
            logger.warning("rescan: scene backfill failed", exc_info=True)
    return result


@router.post("/discover")
async def discover_campaigns(
    state_store: StateStoreDep,
    file_watcher: FileWatcherDep,
    request: Request,
) -> Any:
    """Scan data/campaigns/ for directories with a campaign.yaml and register
    any that are missing from the database."""
    from grimoire.files.yaml_io import load_yaml

    root = campaigns_root(state_store.data_root)
    if not root.is_dir():
        return {"discovered": 0, "campaigns": []}

    existing = {
        row["id"]
        for row in await state_store.db.fetchall("SELECT id FROM campaigns")
    }

    registered: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        campaign_id = child.name
        if campaign_id in existing:
            continue
        yaml_path = child / "campaign.yaml"
        if not yaml_path.is_file():
            continue
        raw: dict = {}
        try:
            loaded = load_yaml(yaml_path)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            pass
        name = str(raw.get("name") or campaign_id)
        description = raw.get("description")
        await state_store.upsert_campaign(
            campaign_id=campaign_id,
            name=name,
            description=str(description) if description else None,
            mechanics_module=raw.get("mechanics_module") or raw.get("mechanics"),
            style_guide_id=raw.get("style_guide_id"),
        )
        registered.append(campaign_id)
        logger.info("discover: registered campaign %r from disk", campaign_id)

    if registered:
        try:
            await file_watcher.scan_now(scope="campaigns")
        except Exception:
            logger.warning("discover: post-registration rescan failed", exc_info=True)
        container = getattr(request.app.state, "container", None)
        if container and getattr(container, "scene_indexer", None):
            try:
                await container.scene_indexer.backfill()
            except Exception:
                logger.warning("discover: scene backfill failed", exc_info=True)

    return {"discovered": len(registered), "campaigns": registered}


@router.post("", status_code=201)
async def create_campaign(
    payload: CampaignCreatePayload,
    state_store: StateStoreDep,
    library: LibraryDep,
    world: WorldDep,
    scenes: ScenesDep,
    gateway: LLMGatewayDep,
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
        try:
            from grimoire.api.config import _DEFAULT_LLM_DEFAULTS, _read_app_yaml
            from grimoire.llm_gateway.tiers import Tier

            app_yaml = _read_app_yaml()
            raw_block = app_yaml.get("llm_defaults")
            block = raw_block if isinstance(raw_block, dict) else {}
            heavy = str(block.get("heavy") or _DEFAULT_LLM_DEFAULTS["heavy"])
            light = str(block.get("light") or _DEFAULT_LLM_DEFAULTS["light"])
            await gateway.set_tier_route(payload.id, Tier.HEAVY, heavy)
            await gateway.set_tier_route(payload.id, Tier.LIGHT, light)
        except Exception:
            logger.warning(
                "seeding default model_tiers for new campaign %s failed",
                payload.id,
                exc_info=True,
            )
        try:
            row = await state_store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (payload.id,)
            )
            cfg = _load_campaign_config(dict(row) if row else {})
            cfg["integrated_deltas"] = True
            await _write_campaign_config(state_store, payload.id, cfg)
        except Exception:
            logger.warning(
                "seeding integrated_deltas for new campaign %s failed",
                payload.id,
                exc_info=True,
            )
        if payload.greeting_id and comp.worlds:
            owning_ref = sorted(comp.worlds, key=lambda r: r.priority)[0]
            try:
                scene = await world.seed_scene_from_greeting(
                    campaign_id=payload.id,
                    greeting_id=payload.greeting_id,
                    world_id=owning_ref.world_id,
                    scene_manager=scenes,
                )
                greeting = await library.get_greeting(owning_ref.world_id, payload.greeting_id)
                await _seed_greeting_first_post(
                    scenes=scenes,
                    scene=scene,
                    greeting=greeting,
                    state_store=state_store,
                    library=library,
                    world_id=owning_ref.world_id,
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
        data["composition_error"] = None
    except Exception as exc:
        logger.exception("get_campaign: composition resolution failed for %s", campaign_id)
        data["composition"] = None
        data["composition_error"] = f"{type(exc).__name__}: {exc}"
    return data


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    payload: CampaignUpdatePayload,
    state_store: StateStoreDep,
    mechanics: MechanicsDep,
) -> Any:
    row = await state_store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id!r} not found")
    current = dict(row)
    try:
        if payload.mechanics is not None and payload.mechanics != current.get("mechanics_module"):
            await mechanics.switch_module(campaign_id, payload.mechanics or None)
            row = await state_store.db.fetchone(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            )
            current = dict(row) if row else current
        await state_store.upsert_campaign(
            campaign_id=campaign_id,
            name=payload.name if payload.name is not None else current["name"],
            description=payload.description
            if payload.description is not None
            else current.get("description"),
            mechanics_module=current.get("mechanics_module"),
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
    campaign_dir = campaigns_root(state_store.data_root) / campaign_id
    if campaign_dir.exists():
        shutil.rmtree(campaign_dir, ignore_errors=True)
