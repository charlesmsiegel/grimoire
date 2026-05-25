"""Campaign continuity routes: facts, commitments, ledger, contradictions, time advance."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import ContinuityDep, TimeEngineDep
from grimoire.api.util import map_lookup_errors, to_payload

from .helpers import _continuity_for
from .schemas import CreateFactPayload, TimeAdvancePayload

router = APIRouter()


@router.get("/{campaign_id}/facts")
async def list_facts(
    campaign_id: str,
    continuity: ContinuityDep,
    limit: int = 50,
) -> Any:
    service = _continuity_for(continuity, campaign_id)
    try:
        facts = await service.facts_about(limit=limit)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(facts)


@router.post("/{campaign_id}/facts", status_code=201)
async def create_fact(
    campaign_id: str,
    payload: CreateFactPayload,
    continuity: ContinuityDep,
) -> Any:
    from grimoire.types.continuity import Fact

    service = _continuity_for(continuity, campaign_id)
    try:
        fact_data = {**payload.fact, "campaign_id": campaign_id}
        fact = Fact.model_validate(fact_data)
        fact_id = await service.add_fact(fact, source=payload.source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"fact_id": fact_id}


@router.get("/{campaign_id}/commitments")
async def list_commitments(
    campaign_id: str,
    continuity: ContinuityDep,
    limit: int = 50,
) -> Any:
    service = _continuity_for(continuity, campaign_id)
    try:
        commitments = await service.open_commitments(limit=limit)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(commitments)


@router.get("/{campaign_id}/continuity/ledger")
async def continuity_ledger(
    campaign_id: str,
    continuity: ContinuityDep,
    limit_facts: int = 20,
    limit_commitments: int = 20,
) -> Any:
    from grimoire.continuity.types import Duration

    service = _continuity_for(continuity, campaign_id)
    try:
        active_rows = await service.open_commitments(limit=limit_commitments * 2)
        overdue_rows = [
            c for c in active_rows if getattr(getattr(c, "status", None), "value", "") == "overdue"
        ]
        open_rows = [
            c for c in active_rows if getattr(getattr(c, "status", None), "value", "") != "overdue"
        ][:limit_commitments]
        stale_rows = await service.stale_commitments(Duration.months(6))
        recent_facts = await service.facts_about(limit=limit_facts)
        unresolved = await service.pending_contradictions(limit=20)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "campaign_id": campaign_id,
        "open_commitments": to_payload(open_rows),
        "overdue_commitments": to_payload(overdue_rows),
        "stale_commitments": to_payload(stale_rows),
        "recent_facts": to_payload(recent_facts),
        "unresolved_contradictions": to_payload(unresolved),
    }


@router.get("/{campaign_id}/continuity/contradictions")
async def list_contradiction_reports_route(
    campaign_id: str,
    continuity: ContinuityDep,
    resolved: bool | None = None,
    limit: int = 50,
) -> Any:
    service = _continuity_for(continuity, campaign_id)
    try:
        store = getattr(service, "_store", None)
        if store is None or not hasattr(store, "list_contradiction_reports"):
            reports = await service.pending_contradictions(limit=limit)
        else:
            reports = await store.list_contradiction_reports(resolved=resolved, limit=limit)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(reports)


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
