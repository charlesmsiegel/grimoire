"""Campaign turn routes: submit, advance, regenerate, undo, proposals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import OrchestratorDep
from grimoire.api.util import map_lookup_errors, to_payload

from .schemas import (
    AdvanceTurnPayload,
    NextSpeakerPayload,
    ResolveProposalsPayload,
    ResolveSceneBreakPayload,
    SubmitTurnPayload,
    UndoPayload,
)

router = APIRouter()


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


@router.post("/{campaign_id}/turns/next-speaker")
async def next_speaker(
    campaign_id: str,
    payload: NextSpeakerPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        await orchestrator.next_speaker(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"accepted": True}


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


@router.post("/{campaign_id}/turns/{turn_id}/resolve-proposals")
async def resolve_pre_roll_proposals(
    campaign_id: str,
    turn_id: str,
    payload: ResolveProposalsPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    from grimoire.types.mechanics import ProposalResolution

    resolutions = [
        ProposalResolution(label=r.label, accepted=r.accepted, modifications=r.modifications)
        for r in payload.resolutions
    ]
    try:
        await orchestrator.resolve_pre_roll(campaign_id, turn_id, resolutions)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"accepted": True, "turn_id": turn_id}


@router.post("/{campaign_id}/turns/{turn_id}/resolve-scene-break")
async def resolve_scene_break(
    campaign_id: str,
    turn_id: str,
    payload: ResolveSceneBreakPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    if payload.choice not in ("continue", "new_scene"):
        raise HTTPException(
            status_code=422,
            detail=f"choice must be 'continue' or 'new_scene', got {payload.choice!r}",
        )
    try:
        resolved = await orchestrator.resolve_scene_break(
            campaign_id,
            turn_id,
            payload.choice,  # type: ignore[arg-type]
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"no scene-break prompt pending for turn {turn_id!r}",
        )
    return {"resolved": True, "turn_id": turn_id, "choice": payload.choice}
