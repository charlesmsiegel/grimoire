"""Per-post alternate (swipes) REST routes.

Wraps the orchestrator's swipes-alternates surface from spec
``docs/superpowers/specs/2026-05-19-swipes-alternates-design.md`` §F1:
regenerate, list alternates, switch primary, pin/unpin, delete.

Routes are scoped to ``/campaigns/{cid}/scenes/{sid}/posts/{pid}`` so each
request carries the scene id explicitly — that lets us reject a stray
``scene_id`` that doesn't match the post's scene rather than silently
resolving by post id alone.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from grimoire.api.deps import OrchestratorDep, ScenesDep
from grimoire.api.util import map_lookup_errors, to_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns")


class RegeneratePayload(BaseModel):
    steering_hint: str | None = None
    model_override: str | None = None


class PinPayload(BaseModel):
    pinned: bool


class EditPostPayload(BaseModel):
    body: str
    source: str = "manual_edit"


async def _resolve_post(scenes: Any, campaign_id: str, scene_id: str, post_id: str) -> Any:
    """Return the post if it belongs to (campaign_id, scene_id), else raise 404."""
    scene = await scenes.get_scene(scene_id)
    if getattr(scene, "campaign_id", None) != campaign_id:
        raise HTTPException(
            status_code=404,
            detail=f"scene {scene_id!r} not found in campaign {campaign_id!r}",
        )
    posts = await scenes.get_posts(scene_id)
    for post in posts:
        if post.id == post_id:
            return post
    raise HTTPException(
        status_code=404,
        detail=f"post {post_id!r} not found in scene {scene_id!r}",
    )



@router.post("/{campaign_id}/scenes/{scene_id}/posts/{post_id}/regenerate")
async def regenerate_post(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    orchestrator: OrchestratorDep,
    scenes: ScenesDep,
    payload: Annotated[RegeneratePayload | None, Body()] = None,
) -> Any:
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    body = payload or RegeneratePayload()
    try:
        result = await orchestrator.regenerate_post(
            campaign_id=campaign_id,
            post_id=post_id,
            steering_hint=body.steering_hint,
            model_override=body.model_override,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/{campaign_id}/scenes/{scene_id}/posts/{post_id}/alternates")
async def list_alternates(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    scenes: ScenesDep,
) -> Any:
    post = await _resolve_post(scenes, campaign_id, scene_id, post_id)
    return {
        "post_id": post.id,
        "primary_alternate_id": post.primary_alternate_id,
        "alternates": [to_payload(a) for a in post.alternates],
    }


@router.post("/{campaign_id}/scenes/{scene_id}/posts/{post_id}/alternates/{alternate_id}/primary")
async def switch_primary_alternate(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    alternate_id: str,
    orchestrator: OrchestratorDep,
    scenes: ScenesDep,
) -> Any:
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    try:
        result = await orchestrator.switch_primary_alternate(
            campaign_id=campaign_id,
            post_id=post_id,
            alternate_id=alternate_id,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/scenes/{scene_id}/posts/{post_id}/alternates/{alternate_id}/pin")
async def pin_alternate(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    alternate_id: str,
    payload: PinPayload,
    orchestrator: OrchestratorDep,
    scenes: ScenesDep,
) -> Any:
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    try:
        await orchestrator.pin_alternate(
            post_id=post_id,
            alternate_id=alternate_id,
            pinned=payload.pinned,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"post_id": post_id, "alternate_id": alternate_id, "pinned": payload.pinned}


@router.patch("/{campaign_id}/scenes/{scene_id}/posts/{post_id}")
async def edit_post_body(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    payload: EditPostPayload,
    scenes: ScenesDep,
) -> Any:
    """Directly edit a post's markdown body.

    This is a manual edit by the user — no LLM, no alternates. It updates
    the persisted .md and the post body returned by subsequent fetches.
    """
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    try:
        await scenes.edit_post(post_id, payload.body, source=payload.source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    # Re-fetch to return the updated post
    post = await _resolve_post(scenes, campaign_id, scene_id, post_id)
    return to_payload(post)


@router.delete(
    "/{campaign_id}/scenes/{scene_id}/posts/{post_id}/alternates/{alternate_id}",
    status_code=204,
)
async def delete_alternate(
    campaign_id: str,
    scene_id: str,
    post_id: str,
    alternate_id: str,
    orchestrator: OrchestratorDep,
    scenes: ScenesDep,
) -> None:
    await _resolve_post(scenes, campaign_id, scene_id, post_id)
    try:
        await orchestrator.delete_alternate(post_id=post_id, alternate_id=alternate_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


__all__ = ["router"]
