"""REST routes for the transient-state subsystem.

Spec: docs/superpowers/specs/2026-05-19-transient-state-design.md §REST surface.

Each handler is a thin wrapper around :class:`TransientStateService`. PATCH
writes route through ``provenance=user:edit`` with ``confidence=1.0``; the
HUD widget edits go through the canonical owner endpoint per the
scene-hud spec.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from grimoire.api.deps import ContinuityDep, TransientStateDep
from grimoire.types.transient import (
    EntityKind,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientValue,
)

router = APIRouter(prefix="/campaigns")


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class TransientValueResponse(BaseModel):
    id: int
    entity_id: str
    field: str
    value: Any
    provenance: str
    confidence: float
    source_post_id: str | None
    created_at: str
    expires_at: str | None
    in_game_at: str | None
    decayed: bool

    @classmethod
    def from_value(cls, v: TransientValue) -> TransientValueResponse:
        prov = v.provenance.value if hasattr(v.provenance, "value") else str(v.provenance)
        return cls(
            id=v.id,
            entity_id=v.entity_id,
            field=v.field,
            value=v.value,
            provenance=prov,
            confidence=v.confidence,
            source_post_id=v.source_post_id,
            created_at=v.created_at.isoformat(),
            expires_at=v.expires_at.isoformat() if v.expires_at else None,
            in_game_at=v.in_game_at.isoformat() if v.in_game_at else None,
            decayed=v.decayed,
        )


class TransientBundleResponse(BaseModel):
    entity_id: str
    fields: dict[str, TransientValueResponse]


class TransientHistoryResponse(BaseModel):
    field: str
    entries: list[TransientValueResponse]


class TransientPatchPayload(BaseModel):
    value: Any
    confidence: float = 1.0
    source_post_id: str | None = None
    provenance: str = Provenance.USER_EDIT.value


class TransientPromotePayload(BaseModel):
    evidence: str = ""
    turn_id: str = Field(default="")


class TransientConflictResponse(BaseModel):
    current: TransientValueResponse
    losing: TransientValueResponse

    @classmethod
    def from_pair(cls, c: TransientConflict) -> TransientConflictResponse:
        return cls(
            current=TransientValueResponse.from_value(c.current),
            losing=TransientValueResponse.from_value(c.losing),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _parse_entity_kind(raw: str) -> EntityKind:
    try:
        return EntityKind(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"unknown entity kind: {raw!r}") from e


def _parse_observer(raw: str | None) -> ObserverKind | None:
    if raw is None:
        return None
    try:
        return ObserverKind(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"unknown observer kind: {raw!r}") from e


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient",
    response_model=TransientBundleResponse,
)
async def get_bundle(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    transient_state: TransientStateDep,
    observer: str | None = None,
) -> TransientBundleResponse:
    kind = _parse_entity_kind(entity_kind)
    obs = _parse_observer(observer)
    bundle = await transient_state.get(campaign_id, kind, entity_id, for_observer=obs)
    if bundle is None:
        bundle = {}
    return TransientBundleResponse(
        entity_id=entity_id,
        fields={k: TransientValueResponse.from_value(v) for k, v in bundle.items()},
    )


@router.get(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient/{field}",
    response_model=TransientValueResponse,
)
async def get_field(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    field: str,
    transient_state: TransientStateDep,
    observer: str | None = None,
) -> TransientValueResponse:
    kind = _parse_entity_kind(entity_kind)
    obs = _parse_observer(observer)
    v = await transient_state.get(campaign_id, kind, entity_id, field, for_observer=obs)
    if v is None:
        raise HTTPException(status_code=404, detail="field not set")
    return TransientValueResponse.from_value(v)


@router.patch(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient/{field}",
    response_model=TransientValueResponse,
)
async def patch_field(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    field: str,
    payload: TransientPatchPayload,
    transient_state: TransientStateDep,
) -> TransientValueResponse:
    kind = _parse_entity_kind(entity_kind)
    try:
        provenance = Provenance(payload.provenance)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provenance: {payload.provenance!r}",
        ) from e
    v = await transient_state.set(
        campaign_id,
        kind,
        entity_id,
        field,
        payload.value,
        provenance=provenance,
        confidence=payload.confidence,
        source_post_id=payload.source_post_id,
    )
    return TransientValueResponse.from_value(v)


@router.delete(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient/{field}",
    status_code=204,
)
async def delete_field(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    field: str,
    transient_state: TransientStateDep,
) -> None:
    kind = _parse_entity_kind(entity_kind)
    await transient_state.clear(campaign_id, kind, entity_id, field=field)


@router.delete(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient",
    status_code=204,
)
async def delete_bundle(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    transient_state: TransientStateDep,
) -> None:
    kind = _parse_entity_kind(entity_kind)
    await transient_state.clear(campaign_id, kind, entity_id)


@router.get(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient/{field}/history",
    response_model=TransientHistoryResponse,
)
async def get_history(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    field: str,
    transient_state: TransientStateDep,
    limit: int = 20,
) -> TransientHistoryResponse:
    kind = _parse_entity_kind(entity_kind)
    rows = await transient_state.history(campaign_id, kind, entity_id, field, limit=limit)
    return TransientHistoryResponse(
        field=field,
        entries=[TransientValueResponse.from_value(r) for r in rows],
    )


@router.post(
    "/{campaign_id}/entities/{entity_kind}/{entity_id}/transient/{field}/promote-to-fact",
)
async def promote_to_fact(
    campaign_id: str,
    entity_kind: str,
    entity_id: str,
    field: str,
    payload: TransientPromotePayload,
    transient_state: TransientStateDep,
    continuity: ContinuityDep,
) -> dict[str, Any]:
    kind = _parse_entity_kind(entity_kind)
    try:
        fact_id, transient_id = await transient_state.promote_to_fact(
            campaign_id,
            kind,
            entity_id,
            field,
            evidence=payload.evidence,
            turn_id=payload.turn_id,
            continuity=continuity,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"fact_id": fact_id, "transient_id": transient_id}


@router.get(
    "/{campaign_id}/transient/conflicts",
    response_model=list[TransientConflictResponse],
)
async def list_conflicts(
    campaign_id: str,
    transient_state: TransientStateDep,
) -> list[TransientConflictResponse]:
    conflicts = await transient_state.list_conflicts(campaign_id)
    return [TransientConflictResponse.from_pair(c) for c in conflicts]
