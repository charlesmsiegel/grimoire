"""Sprite-serving + PC expression PATCH routes.

The GET endpoint resolves a character's current (or as-of-turn)
expression and walks the fallback chain:

    requested → neutral → avatar → null

Sprite URLs are served via the static ``/library/...`` route; this
endpoint only resolves the URL. Path-traversal guards reject anything
that would escape the library root.
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from grimoire import config as _config
from grimoire.api.deps import ContainerDep, StateStoreDep
from grimoire.expressions.service import ExpressionStateService
from grimoire.state_store.errors import InvalidRefError
from grimoire.state_store.paths import (
    CharacterLayout,
    character_dir_layout,
    relative_to_root,
    validate_path_component,
)
from grimoire.types.expressions import (
    CORE_EXPRESSION_VALUES,
    VocabularyError,
    is_known_label,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ExpressionResponse(BaseModel):
    emotion: str
    sprite_url: str | None
    fallback_used: bool


class ExpressionPatchBody(BaseModel):
    emotion: str
    post_id: str
    scene_id: str = ""
    turn_id: str = ""


def _get_expression_service(request: Request) -> ExpressionStateService:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(status_code=503, detail="container not initialised")
    svc = container.expressions
    if svc is None:
        if container.db is None:
            raise HTTPException(status_code=503, detail="database not initialised")
        svc = ExpressionStateService(container.db)
        container.expressions = svc
    return svc


async def _resolve_world_for_character(
    state_store: Any, campaign_id: str, character_id: str
) -> tuple[str, str] | None:
    """Resolve ``character_id`` to ``(world_id, asset_id)`` for this campaign.

    Looks up the campaign's world refs in priority order; returns the
    first ``library_index`` row matching the character asset id in any
    of those worlds. Returns ``None`` when the character isn't found.
    """
    try:
        validate_path_component(campaign_id, name="campaign_id")
        validate_path_component(character_id, name="character_id")
    except InvalidRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    refs = await state_store.list_world_refs(campaign_id)
    world_ids = [r["world_id"] for r in refs] if refs else []
    # Fall back to any world: useful for tests where world refs aren't seeded.
    for world_id in world_ids:
        row = await state_store.db.fetchone(
            """
            SELECT world_id, asset_id FROM library_index
            WHERE kind = 'character' AND world_id = ? AND asset_id = ?
            """,
            (world_id, character_id),
        )
        if row is not None:
            return row["world_id"], row["asset_id"]
    row = await state_store.db.fetchone(
        """
        SELECT world_id, asset_id FROM library_index
        WHERE kind = 'character' AND asset_id = ?
        LIMIT 1
        """,
        (character_id,),
    )
    if row is not None:
        return row["world_id"], row["asset_id"]
    return None


def _resolve_sprite(
    layout: CharacterLayout,
    *,
    emotion: str,
    data_root: Path,
) -> tuple[str | None, str, bool]:
    """Walk the fallback chain. Returns (sprite_url, effective_emotion, fallback_used).

    ``effective_emotion`` is the emotion that produced the served sprite
    (may differ from the requested one when the chain falls back). The
    URL is always rooted under ``/library/...`` so the existing static
    file handler serves the bytes.
    """
    sprite = _safe_sprite_path(layout, emotion)
    if sprite is not None:
        return _url_for(sprite, data_root), emotion, False
    if emotion != "neutral":
        neutral = _safe_sprite_path(layout, "neutral")
        if neutral is not None:
            return _url_for(neutral, data_root), "neutral", True
    if layout.avatar is not None and layout.avatar.exists():
        return _url_for(layout.avatar, data_root), emotion, True
    return None, emotion, True


def _safe_sprite_path(layout: CharacterLayout, emotion: str) -> Path | None:
    """Return the on-disk sprite path for ``emotion`` if it resolves safely.

    Rejects anything containing path separators or backreferences. The
    final resolved path must live under ``layout.sprites_dir``; this is
    the path-traversal guard called out as a research pitfall.
    """
    if layout.sprites_dir is None:
        return None
    if not emotion or "/" in emotion or "\\" in emotion or ".." in emotion:
        return None
    # Emotions are either core enum values or "<module>.<label>"; both
    # are simple identifiers (snake_case + optional dot). The static
    # validator in :func:`is_known_label` is enforced at write time, so
    # by the time we're serving we can trust the label is well-formed —
    # but we still resolve+verify just in case.
    target = layout.sprites_dir / f"{emotion}.png"
    try:
        resolved = target.resolve(strict=False)
        sprites_root = layout.sprites_dir.resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(sprites_root)
    except ValueError:
        return None
    if not resolved.exists():
        return None
    return resolved


def _url_for(path: Path, data_root: Path) -> str:
    """Build a ``/library/...`` URL for a path inside the data root."""
    rel = relative_to_root(data_root, path)
    # ``rel`` is something like ``library/worlds/w/characters/.../happy.png``;
    # the static handler is mounted at ``/library`` so we strip the leading
    # ``library/`` segment.
    rel = rel.replace("\\", "/")
    if rel.startswith("library/"):
        rel = rel[len("library/") :]
    return f"/library/{rel}"


async def _vocabulary_extensions(container: Any) -> dict[str, list[str]]:
    """Snapshot of currently-installed mechanics modules' vocab extensions."""
    mechanics = getattr(container, "mechanics", None)
    if mechanics is None:
        return {}
    try:
        manifests = await mechanics.list_manifests()
    except AttributeError:
        # MechanicsService doesn't always expose list_manifests; degrade
        # gracefully.
        return {}
    out: dict[str, list[str]] = {}
    for m in manifests or []:
        mod_id = getattr(m, "id", None)
        labels = getattr(m, "expression_vocabulary_extensions", None) or []
        if isinstance(mod_id, str) and isinstance(labels, list):
            out[mod_id] = [str(x) for x in labels]
    return out


async def _is_expression_enabled(state_store: Any, campaign_id: str, character_id: str) -> bool:
    """Check whether expressions are enabled for this character in this campaign."""
    row = await state_store.db.fetchone("SELECT config FROM campaigns WHERE id = ?", (campaign_id,))
    if row is None:
        return False
    try:
        cfg = _json.loads(row["config"] or "{}")
    except (TypeError, ValueError):
        return False
    block = cfg.get("expressions") or {}
    enabled = block.get("enabled_characters") or []
    bare_id = character_id.rsplit("/", 1)[-1] if "/" in character_id else character_id
    return bare_id in enabled


@router.get(
    "/campaigns/{campaign_id}/characters/{character_id}/expression",
    response_model=ExpressionResponse,
)
async def get_expression(
    campaign_id: str,
    character_id: str,
    request: Request,
    state_store: StateStoreDep,
    container: ContainerDep,
    as_of_turn: str | None = None,
) -> ExpressionResponse:
    if not await _is_expression_enabled(state_store, campaign_id, character_id):
        return ExpressionResponse(emotion="neutral", sprite_url=None, fallback_used=True)
    service = _get_expression_service(request)
    resolved = await _resolve_world_for_character(state_store, campaign_id, character_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"character {character_id!r} not found")
    world_id, asset_id = resolved
    data_root = _config.settings.data_root
    try:
        layout = character_dir_layout(world_id, asset_id, data_root=data_root)
    except InvalidRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = await service.current_for(campaign_id, character_id, as_of_turn=as_of_turn)
    emotion = record.emotion if record is not None else "neutral"
    sprite_url, effective_emotion, fallback_used = _resolve_sprite(
        layout, emotion=emotion, data_root=data_root
    )
    return ExpressionResponse(
        emotion=effective_emotion,
        sprite_url=sprite_url,
        fallback_used=fallback_used,
    )


@router.patch(
    "/campaigns/{campaign_id}/characters/{character_id}/expression",
    response_model=ExpressionResponse,
)
async def patch_expression(
    campaign_id: str,
    character_id: str,
    body: ExpressionPatchBody,
    request: Request,
    state_store: StateStoreDep,
    container: ContainerDep,
) -> ExpressionResponse:
    try:
        validate_path_component(campaign_id, name="campaign_id")
        validate_path_component(character_id, name="character_id")
    except InvalidRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    extensions = await _vocabulary_extensions(container)
    if not is_known_label(body.emotion, module_extensions=extensions):
        raise HTTPException(
            status_code=400,
            detail=f"{body.emotion!r} is not part of the active expression vocabulary",
        )
    service = _get_expression_service(request)
    try:
        await service.set_strict(
            campaign_id=campaign_id,
            scene_id=body.scene_id,
            character_id=character_id,
            turn_id=body.turn_id or body.post_id,
            post_id=body.post_id,
            emotion=body.emotion,
            provenance="user:pc",
            module_extensions=extensions,
        )
    except VocabularyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await get_expression(
        campaign_id=campaign_id,
        character_id=character_id,
        request=request,
        state_store=state_store,
        container=container,
        as_of_turn=None,
    )


@router.get("/expressions/vocabulary")
async def vocabulary(container: ContainerDep) -> dict[str, Any]:
    """Active expression vocabulary: core labels plus per-module extensions."""
    extensions = await _vocabulary_extensions(container)
    return {
        "core": sorted(CORE_EXPRESSION_VALUES),
        "extensions": {k: list(v) for k, v in extensions.items()},
    }


__all__ = ["router"]
