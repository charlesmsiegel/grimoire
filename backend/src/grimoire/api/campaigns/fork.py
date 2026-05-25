"""Campaign fork and branch routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import OrchestratorDep
from grimoire.api.util import map_lookup_errors, to_payload

from .schemas import BranchForkPayload, ForkPayload

router = APIRouter()


@router.post("/{campaign_id}/forks", status_code=201)
async def fork_campaign(
    campaign_id: str,
    payload: ForkPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    from grimoire.orchestrator.errors import CampaignIdExists

    try:
        result = await orchestrator.fork_campaign(
            campaign_id=campaign_id,
            new_campaign_id=payload.new_campaign_id,
            new_name=payload.new_name,
            fork_at_post_id=payload.fork_at_post_id,
            description=payload.description,
            make_active=payload.make_active,
        )
    except CampaignIdExists as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "CAMPAIGN_ID_EXISTS",
                "campaign_id": exc.campaign_id,
            },
        ) from exc
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/{campaign_id}/forks/pending")
async def list_pending_forks(
    campaign_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        return await orchestrator.list_pending_forks(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/lineage")
async def get_lineage(
    campaign_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        return await orchestrator.get_lineage(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/lineage/ancestors")
async def get_lineage_ancestors(
    campaign_id: str,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        return await orchestrator.get_lineage_ancestors(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.post("/{campaign_id}/branches", status_code=201)
async def fork_branch(
    campaign_id: str,
    payload: BranchForkPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.fork(campaign_id, payload.from_turn_id, payload.label)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)
