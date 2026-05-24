"""Narrative-extras REST routes.

Wraps :class:`grimoire.extras.ExtrasService`. The endpoint surface mirrors
the design doc §REST surface: library + campaign read/write, pin/unpin,
promotion paths, and an FTS-backed search.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from grimoire.api.deps import ExtrasServiceDep
from grimoire.api.util import map_lookup_errors
from grimoire.types.common import EntityKind
from grimoire.types.extras import ExtraScope

router = APIRouter()


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


class SetExtraPayload(BaseModel):
    value: Any = None
    evidence: str | None = None
    actor: str = "user"


class PromotePayload(BaseModel):
    actor: str = Field(default="promotion")


def _parse_kind(kind: str) -> EntityKind:
    try:
        return EntityKind(kind.rstrip("s"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"unknown entity kind: {kind!r}") from exc


def _serialize_extras(extras: dict) -> dict[str, Any]:
    return {key: value.model_dump(mode="json") for key, value in extras.items()}


# --------------------------------------------------------------------------- #
# Library scope
# --------------------------------------------------------------------------- #


@router.get("/library/{world_id}/{kind}/{entity_id}/extras")
async def list_library_extras(
    world_id: str, kind: str, entity_id: str, extras: ExtrasServiceDep
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind)

    try:
        result = await extras.get_raw(
            entity_kind=entity_kind,
            entity_id=entity_id,
            scope=ExtraScope.LIBRARY,
            world_id=world_id,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"extras": _serialize_extras(result)}


@router.put("/library/{world_id}/{kind}/{entity_id}/extras/{key}")
async def put_library_extra(
    world_id: str,
    kind: str,
    entity_id: str,
    key: str,
    payload: SetExtraPayload,
    extras: ExtrasServiceDep,
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind)

    try:
        result = await extras.set(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=key,
            value=payload.value,
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=world_id,
            actor=payload.actor,
            evidence=payload.evidence,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "extra": result.extra.model_dump(mode="json"),
        "warnings": result.warnings,
    }


@router.delete("/library/{world_id}/{kind}/{entity_id}/extras/{key}", status_code=204)
async def delete_library_extra(
    world_id: str,
    kind: str,
    entity_id: str,
    key: str,
    extras: ExtrasServiceDep,
) -> None:
    entity_kind = _parse_kind(kind)

    try:
        await extras.delete(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=key,
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=world_id,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Campaign scope (cascade-resolved + raw)
# --------------------------------------------------------------------------- #


@router.get("/campaigns/{campaign_id}/{kind}/{entity_id}/extras")
async def list_campaign_extras_resolved(
    campaign_id: str,
    kind: str,
    entity_id: str,
    extras: ExtrasServiceDep,
    world_id: str | None = Query(default=None),
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind)

    try:
        result = await extras.get(
            entity_kind=entity_kind,
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"extras": _serialize_extras(result)}


@router.get("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/raw")
async def list_campaign_extras_raw(
    campaign_id: str,
    kind: str,
    entity_id: str,
    extras: ExtrasServiceDep,
    world_id: str | None = Query(default=None),
    scope: str = Query(default="campaign-local"),
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind)
    try:
        scope_enum = ExtraScope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown scope: {scope!r}") from exc

    try:
        result = await extras.get_raw(
            entity_kind=entity_kind,
            entity_id=entity_id,
            scope=scope_enum,
            campaign_id=campaign_id,
            world_id=world_id,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"extras": _serialize_extras(result)}


@router.put("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/{key}")
async def put_campaign_extra(
    campaign_id: str,
    kind: str,
    entity_id: str,
    key: str,
    payload: SetExtraPayload,
    extras: ExtrasServiceDep,
    world_id: str | None = Query(default=None),
    scope: str = Query(default="override"),
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind)
    try:
        scope_enum = ExtraScope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown scope: {scope!r}") from exc

    try:
        result = await extras.set(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=key,
            value=payload.value,
            scope=scope_enum,
            campaign_id=campaign_id,
            world_id=world_id,
            actor=payload.actor,
            evidence=payload.evidence,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "extra": result.extra.model_dump(mode="json"),
        "warnings": result.warnings,
    }


@router.delete("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/{key}", status_code=204)
async def delete_campaign_extra(
    campaign_id: str,
    kind: str,
    entity_id: str,
    key: str,
    extras: ExtrasServiceDep,
    world_id: str | None = Query(default=None),
    scope: str = Query(default="override"),
) -> None:
    entity_kind = _parse_kind(kind)
    try:
        scope_enum = ExtraScope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown scope: {scope!r}") from exc

    try:
        await extras.delete(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=key,
            scope=scope_enum,
            campaign_id=campaign_id,
            world_id=world_id,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


# --------------------------------------------------------------------------- #
# Pin/unpin (HUD config delegate)
# --------------------------------------------------------------------------- #


@router.post("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/{key}/pin", status_code=204)
async def pin_extra(
    campaign_id: str,
    kind: str,
    entity_id: str,
    key: str,
    extras: ExtrasServiceDep,
) -> None:
    # Pin state lives in hud.yaml per scene-hud-design; we expose the
    # endpoint here so the entity-detail UI doesn't need to know about
    # the HUD config service. If extras.pin is not wired, return 501.
    pin = getattr(extras, "pin", None)
    if pin is None:
        raise HTTPException(status_code=501, detail="pin support not wired")
    await pin(
        campaign_id=campaign_id,
        entity_kind=_parse_kind(kind),
        entity_id=entity_id,
        key=key,
        pinned=True,
    )


@router.post("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/{key}/unpin", status_code=204)
async def unpin_extra(
    campaign_id: str,
    kind: str,
    entity_id: str,
    key: str,
    extras: ExtrasServiceDep,
) -> None:
    pin = getattr(extras, "pin", None)
    if pin is None:
        raise HTTPException(status_code=501, detail="pin support not wired")
    await pin(
        campaign_id=campaign_id,
        entity_kind=_parse_kind(kind),
        entity_id=entity_id,
        key=key,
        pinned=False,
    )


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #


@router.post("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/{key}/promote-to-library")
async def promote_to_library(
    campaign_id: str,
    kind: str,
    entity_id: str,
    key: str,
    extras: ExtrasServiceDep,
    payload: PromotePayload = Body(default_factory=PromotePayload),  # noqa: B008
    world_id: str | None = Query(default=None),
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind)
    if world_id is None:
        raise HTTPException(status_code=422, detail="world_id is required to promote")

    try:
        result = await extras.promote_to_library(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=key,
            campaign_id=campaign_id,
            world_id=world_id,
            actor=payload.actor,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "extra": result.extra.model_dump(mode="json"),
        "warnings": result.warnings,
    }


@router.post("/campaigns/{campaign_id}/{kind}/{entity_id}/extras/{key}/promote-to-fact")
async def promote_to_fact(
    campaign_id: str,
    kind: str,
    entity_id: str,
    key: str,
    extras: ExtrasServiceDep,
) -> dict[str, Any]:
    # Promote-to-fact requires Continuity wiring; surface a clear 501 when
    # that's not present so the UI can hide the menu item.
    promote = getattr(extras, "promote_to_fact", None)
    if promote is None:
        raise HTTPException(status_code=501, detail="promote_to_fact not wired")
    fact_id = await promote(
        campaign_id=campaign_id,
        entity_kind=_parse_kind(kind),
        entity_id=entity_id,
        key=key,
    )
    return {"fact_id": fact_id}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@router.get("/search/extras")
async def search_extras(
    extras: ExtrasServiceDep,
    q: str = Query(min_length=1),
    kind: str | None = Query(default=None),
    key: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    entity_kind = _parse_kind(kind) if kind else None
    hits = await extras.search(q, entity_kind=entity_kind, key=key, limit=limit)
    return {
        "hits": [
            {
                "entity_kind": hit.entity_kind,
                "entity_id": hit.entity_id,
                "key": hit.key,
                "value_text": hit.value_text,
            }
            for hit in hits
        ]
    }
