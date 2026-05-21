"""Observability HTTP routes.

Spec: ``docs/superpowers/specs/2026-05-18-observability-remaining-design.md``
(§5, §6, §8, §9, §10, §12, §15).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from grimoire.api.deps import ObservabilityDep
from grimoire.types.observability import LogLevel, LogQuery

router = APIRouter(prefix="/observability", tags=["observability"])


def _parse_iso(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid datetime: {value!r}") from exc


@router.get("/turns/{turn_id}")
async def get_turn_audit(turn_id: str, observability: ObservabilityDep) -> Any:
    try:
        audit = await observability.get_turn_audit(turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return audit.model_dump(mode="json")


@router.get("/turns")
async def list_turn_audits(
    observability: ObservabilityDep,
    campaign_id: str,
    limit: int = 50,
) -> Any:
    audits = await observability.list_turn_audits(campaign_id, limit=limit)
    return [a.model_dump(mode="json") for a in audits]


def _enrich_message(msg: Any) -> dict[str, Any]:
    """Normalize an assembled message into a dict and add ``tier`` / ``tokens``.

    Tier comes from ``metadata["tier"]`` (set by ContextBuilder); falls back to
    ``None`` for legacy audits. Tokens are estimated as ``len(content) // 4``
    so the debug view can render a per-message bar without re-running the
    tokenizer.
    """
    if hasattr(msg, "model_dump"):
        data = msg.model_dump(mode="json")
    elif isinstance(msg, dict):
        data = dict(msg)
    else:
        data = {"role": "system", "content": str(msg), "metadata": {}, "name": None}
    metadata = data.get("metadata") or {}
    tier = metadata.get("tier") if isinstance(metadata, dict) else None
    content = data.get("content") or ""
    data["tier"] = tier
    data["tokens"] = max(1, len(content) // 4) if content else 0
    return data


@router.get("/turns/{turn_id}/prompt")
async def get_turn_prompt(turn_id: str, observability: ObservabilityDep) -> Any:
    try:
        audit = await observability.get_turn_audit(turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    assembled = getattr(audit, "assembled_messages", None) or []
    messages = [_enrich_message(m) for m in assembled]
    sources = [s.model_dump(mode="json") for s in audit.context_sources]
    budget_used = {str(k): v for k, v in audit.context_budget_used.items()}
    composition = (
        audit.composition_snapshot.model_dump(mode="json") if audit.composition_snapshot else None
    )
    summary = audit.context_summary.model_dump(mode="json") if audit.context_summary else None
    return {
        "messages": messages,
        "sources": sources,
        "budget_used": budget_used,
        "messages_hash": audit.context_messages_hash,
        "composition_snapshot": composition,
        "summary": summary,
    }


@router.get("/turns/{turn_id}/prompt/diff")
async def get_turn_prompt_diff(
    turn_id: str,
    observability: ObservabilityDep,
    against: str,
) -> Any:
    """Diff this turn's assembled prompt against another (typically the
    immediately preceding turn). 404 if either turn id is unknown."""
    try:
        return await observability.audit_store.diff_prompts(against, turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/turns/{turn_id}/deltas")
async def get_turn_deltas(turn_id: str, observability: ObservabilityDep) -> Any:
    """Per-turn delta diff for the "What changed?" debug view.

    Returns an envelope ``{applied: [...], queued: [...]}`` where each
    entry has ``kind``, ``target_scope`` / ``target_id``, ``before`` /
    ``after``, ``confidence``, ``source`` (the producing strategy),
    ``evidence`` (the response-text snippet that justified the delta),
    and ``status`` of ``"auto"`` or ``"queued"``. Queued entries also
    carry ``review_id`` and ``review_status``.
    """
    try:
        return await observability.audit_store.deltas_for_turn(turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/turns/{turn_id}/costs")
async def get_turn_costs(turn_id: str, observability: ObservabilityDep) -> Any:
    breakdown = await observability.costs().by_turn(turn_id)
    rows = [{"task": task, **total.model_dump()} for task, total in breakdown.items()]
    rows.sort(key=lambda r: (-r["total_usd"], r["task"]))
    return rows


@router.get("/costs/session")
async def get_session_costs(
    observability: ObservabilityDep,
    campaign_id: str,
    since: str | None = None,
) -> Any:
    since_dt = _parse_iso(since)
    total = await observability.costs().total(campaign_id=campaign_id, since=since_dt)
    return total.model_dump(mode="json")


@router.get("/costs/rollup")
async def get_costs_rollup(
    observability: ObservabilityDep,
    campaign_id: str,
    days: int = 30,
) -> Any:
    rollup = await observability.costs().by_day(campaign_id, days=days)
    return [d.model_dump(mode="json") for d in rollup]


@router.get("/costs/total_today")
async def get_total_today(observability: ObservabilityDep, campaign_id: str) -> Any:
    total = await observability.costs().total_today(campaign_id)
    return {"total_usd": total}


@router.get("/metrics/summary")
async def get_metrics_summary(
    observability: ObservabilityDep,
    module: str,
    operation: str,
    window_seconds: int | None = None,
) -> Any:
    return await observability.metrics().summary(module, operation, window_seconds)


@router.get("/metrics/recent")
async def get_metrics_recent(
    observability: ObservabilityDep,
    module: str | None = None,
    operation: str | None = None,
    limit: int = 500,
) -> Any:
    return await observability.metrics().query_recent(
        module=module, operation=operation, limit=limit
    )


@router.get("/metrics/trend")
async def get_metrics_trend(
    observability: ObservabilityDep,
    module: str,
    operation: str,
    bucket: str,
    window_seconds: int,
) -> Any:
    try:
        return await observability.metrics().trend(
            module, operation, bucket=bucket, window_seconds=window_seconds
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/metrics/known")
async def get_metrics_known(observability: ObservabilityDep) -> Any:
    return await observability.metrics().known_pairs()


@router.get("/health/latest")
async def get_health_latest(observability: ObservabilityDep) -> Any:
    latest = observability.health().latest()
    return {tid: status.model_dump(mode="json") for tid, status in latest.items()}


@router.post("/health/probe")
async def post_health_probe(observability: ObservabilityDep, target_id: str) -> Any:
    monitor = observability.health()
    target = next((t for t in monitor.targets() if t.id == target_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown target {target_id!r}")
    status = await monitor.probe(target)
    return status.model_dump(mode="json")


@router.get("/errors/recent")
async def get_errors_recent(observability: ObservabilityDep, limit: int = 50) -> Any:
    errors = await observability.recent_errors(limit=limit)
    return [e.model_dump(mode="json") for e in errors]


@router.get("/errors/aggregate")
async def get_errors_aggregate(
    observability: ObservabilityDep,
    since: str | None = None,
) -> Any:
    since_dt = _parse_iso(since)
    return await observability.errors_store.aggregate_by_module(since_dt)


@router.get("/log")
async def get_log(
    observability: ObservabilityDep,
    level: str | None = None,
    module: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> Any:
    levels: list[LogLevel] | None = None
    if level:
        try:
            levels = [LogLevel(level.upper())]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid level: {level!r}") from exc
    query = LogQuery(
        since=_parse_iso(since),
        until=_parse_iso(until),
        levels=levels,
        modules=[module] if module else None,
        limit=limit,
    )
    events = await observability.query_log(query)
    return [e.model_dump(mode="json") for e in events]


__all__ = ["router"]
