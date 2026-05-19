"""Context Inspector HTTP routes.

Spec: ``docs/superpowers/specs/2026-05-19-context-inspector-design.md``.

Exposes the inspector's preview / explain / pin / exclude / diff
surface so the frontend panel can drive live previews while the user
types and let them pin or exclude entities from the next turn's prompt.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from grimoire.api.container import ServiceContainer
from grimoire.api.deps import get_container
from grimoire.context.inspector import (
    ContextInspector,
    HandleNotFound,
    PinTarget,
)

router = APIRouter(tags=["context"])


def _get_inspector(request: Request) -> ContextInspector:
    container: ServiceContainer = get_container(request)
    inspector = container.extras.get("context_inspector")
    if inspector is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="context inspector service not configured",
        )
    return inspector


InspectorDep = Annotated[ContextInspector, Depends(_get_inspector)]


# ---------------------------------------------------------------------------#
# Request bodies                                                              #
# ---------------------------------------------------------------------------#


class PreviewBody(BaseModel):
    player_input: str = ""
    session_id: str
    branch_id: str | None = None
    pc_ref: str | None = None


class PinTargetBody(BaseModel):
    source_id: str | None = None
    entity_kind: str | None = None
    entity_id: str | None = None

    def to_target(self) -> PinTarget:
        return PinTarget(
            source_id=self.source_id,
            entity_kind=self.entity_kind,
            entity_id=self.entity_id,
        )


class PinBody(BaseModel):
    target: PinTargetBody
    kind: str = Field(default="pin", pattern="^(pin|exclude)$")
    ttl_turns: int | None = None
    created_at_turn_id: str | None = None
    branch_id: str | None = None
    actor: str = "user"


class DiffBody(BaseModel):
    a: str
    b: str
    session_id: str | None = None


# ---------------------------------------------------------------------------#
# Routes                                                                      #
# ---------------------------------------------------------------------------#


@router.post("/campaigns/{campaign_id}/context/preview")
async def post_preview(
    campaign_id: str,
    body: PreviewBody,
    inspector: InspectorDep,
) -> Any:
    handle, summary = await inspector.preview(
        campaign_id=campaign_id,
        player_input=body.player_input,
        session_id=body.session_id,
        branch_id=body.branch_id,
        pc_ref=body.pc_ref,
    )
    return {"handle": handle, "summary": summary.model_dump(mode="json")}


@router.get("/campaigns/{campaign_id}/context/preview/{handle}")
async def get_preview(
    campaign_id: str,
    handle: str,
    session_id: str,
    inspector: InspectorDep,
) -> Any:
    try:
        prompt = await inspector.get(session_id=session_id, handle=handle)
    except HandleNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown handle {handle!r}") from exc
    return prompt.model_dump(mode="json")


@router.get("/campaigns/{campaign_id}/context/preview/{handle}/explain")
async def get_preview_explain(
    campaign_id: str,
    handle: str,
    session_id: str,
    inspector: InspectorDep,
) -> Any:
    try:
        explanations = await inspector.explain(session_id=session_id, handle=handle)
    except HandleNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown handle {handle!r}") from exc
    return [e.model_dump(mode="json") for e in explanations]


@router.post("/campaigns/{campaign_id}/context/pins")
async def post_pin(
    campaign_id: str,
    body: PinBody,
    inspector: InspectorDep,
) -> Any:
    try:
        target = body.target.to_target()
        if body.kind == "pin":
            pin_id = await inspector.pin(
                campaign_id=campaign_id,
                target=target,
                branch_id=body.branch_id,
                ttl_turns=body.ttl_turns,
                created_at_turn_id=body.created_at_turn_id,
                actor=body.actor,
            )
        else:
            pin_id = await inspector.exclude(
                campaign_id=campaign_id,
                target=target,
                branch_id=body.branch_id,
                ttl_turns=body.ttl_turns,
                created_at_turn_id=body.created_at_turn_id,
                actor=body.actor,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"pin_id": pin_id, "kind": body.kind}


@router.delete("/campaigns/{campaign_id}/context/pins/{pin_id}")
async def delete_pin(
    campaign_id: str,
    pin_id: str,
    inspector: InspectorDep,
    actor: str = "user",
) -> Any:
    try:
        await inspector.clear_pin(pin_id=pin_id, actor=actor)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"cleared": pin_id}


@router.get("/campaigns/{campaign_id}/context/pins")
async def list_pins(
    campaign_id: str,
    inspector: InspectorDep,
    branch_id: str | None = None,
    current_turn_id: str | None = None,
) -> Any:
    return await inspector.list_pins(
        campaign_id=campaign_id,
        branch_id=branch_id,
        current_turn_id=current_turn_id,
    )


@router.post("/campaigns/{campaign_id}/context/diff")
async def post_diff(
    campaign_id: str,
    body: DiffBody,
    inspector: InspectorDep,
) -> Any:
    try:
        diff = await inspector.diff(a=body.a, b=body.b, session_id=body.session_id)
    except HandleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return diff.model_dump(mode="json")


__all__ = ["router"]
