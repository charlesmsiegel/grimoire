"""Campaign retcon/replay routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import OrchestratorDep
from grimoire.api.util import map_lookup_errors, to_payload
from .schemas import RetconPayload

router = APIRouter()


@router.post("/{campaign_id}/turns/{turn_id}/retcon")
async def retcon_post(
    campaign_id: str,
    turn_id: str,
    payload: RetconPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.retcon_post(
            payload.post_id,
            payload.new_text,
            campaign_id=campaign_id,
            replay_subsequent=payload.replay_subsequent,
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/{campaign_id}/retcon/replay/{batch_id}")
async def get_retcon_replay_state(
    campaign_id: str,
    batch_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.get_replay_state(campaign_id, batch_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/retcon/replay/{batch_id}/accept")
async def accept_retcon_replay(
    campaign_id: str,
    batch_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.accept_replay(campaign_id, batch_id=batch_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/retcon/replay/{batch_id}/try-again")
async def try_again_retcon_replay(
    campaign_id: str,
    batch_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.try_again_replay(campaign_id, batch_id=batch_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.post("/{campaign_id}/retcon/replay/{batch_id}/cancel")
async def cancel_retcon_replay(
    campaign_id: str,
    batch_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.cancel_replay(campaign_id, batch_id=batch_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)
