"""``TurnAudit`` storage and retrieval (spec 16 §turn audit record).

The audit record is the single source of truth for "what happened on this
turn." Each row in ``turn_audits`` carries the composition snapshot,
context summary, assembled prompt (verbatim), mechanics results, LLM call
metadata, response text, extracted deltas and applied deltas. The schema
stores most slots as JSON blobs so the spec can evolve without further
migrations.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from grimoire.storage.db import Database
from grimoire.types.common import CampaignId, TurnId
from grimoire.types.observability import TurnAudit


def _dump(value: Any) -> str:
    """Serialize a value to JSON, handling pydantic models and dataclasses."""
    if value is None:
        return "null"
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    if isinstance(value, list):
        return json.dumps([_to_jsonable(v) for v in value], default=_default)
    if isinstance(value, dict):
        return json.dumps({k: _to_jsonable(v) for k, v in value.items()}, default=_default)
    return json.dumps(value, default=_default)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unserializable {type(value).__name__}")


def _load(raw: str | None) -> Any:
    if raw is None:
        return None
    if raw == "":
        return None
    return json.loads(raw)


class AuditStore:
    """Read/write façade over the ``turn_audits`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, audit: TurnAudit) -> None:
        composition = _dump(audit.composition_snapshot)
        context_summary = _dump(audit.context_summary)
        prompt_messages = _dump(
            {
                "hash": audit.context_messages_hash,
                "sources": [s.model_dump(mode="json") for s in audit.context_sources],
                "player_input": audit.player_input,
                "options": audit.options,
                "scene_break_decision": (
                    audit.scene_break_decision.model_dump(mode="json")
                    if audit.scene_break_decision
                    else None
                ),
            }
        )
        prompt_budget = _dump({str(k): v for k, v in audit.context_budget_used.items()})
        mechanics_results = _dump(
            {
                "proposed": [r.model_dump(mode="json") for r in audit.proposed_rolls],
                "resolved": [r.model_dump(mode="json") for r in audit.resolved_rolls],
            }
        )
        llm_metadata = _dump(
            {
                "provider": audit.llm_provider,
                "model": audit.llm_model,
                "params": audit.llm_params,
                "prompt_tokens": audit.llm_prompt_tokens,
                "completion_tokens": audit.llm_completion_tokens,
                "cost_usd": audit.llm_cost_usd,
                "latency_ms": audit.llm_latency_ms,
                "finish_reason": audit.llm_finish_reason,
                "retries": audit.llm_retries,
                "started_at": audit.started_at.isoformat() if audit.started_at else None,
                "completed_at": (audit.completed_at.isoformat() if audit.completed_at else None),
                "duration_ms": audit.duration_ms,
            }
        )
        extraction = _dump(
            {
                "strategies_run": list(audit.extraction_strategies_run),
                "duration_ms": audit.extraction_duration_ms,
                "deltas": [d.model_dump(mode="json") for d in audit.extracted_deltas],
                "flags": [f.model_dump(mode="json") for f in audit.extraction_flags],
            }
        )
        applied_ids = _dump([d.delta.id for d in audit.applied_deltas])
        review_ids = _dump([r.id for r in audit.queued_for_review])
        side_effects = _dump(
            {
                "scene_appended": audit.scene_appended,
                "scene_closed": audit.scene_closed,
                "images_scheduled": list(audit.images_scheduled),
                "time_advanced": (
                    audit.time_advanced.model_dump(mode="json") if audit.time_advanced else None
                ),
            }
        )
        errors = _dump(
            {
                "errors": [e.model_dump(mode="json") for e in audit.errors],
                "warnings": [w.model_dump(mode="json") for w in audit.warnings],
            }
        )

        pc_ref = audit.options.get("pc_ref") if isinstance(audit.options, dict) else None

        await self._db.execute(
            """
            INSERT INTO turn_audits (
                turn_id, campaign_id, branch_id, scene_id, pc_ref,
                composition, context_summary, prompt_messages, prompt_budget,
                mechanics_results, llm_metadata, response_text,
                extraction_summary, applied_delta_ids, queued_review_ids,
                side_effects, errors, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
                campaign_id=excluded.campaign_id,
                branch_id=excluded.branch_id,
                scene_id=excluded.scene_id,
                pc_ref=excluded.pc_ref,
                composition=excluded.composition,
                context_summary=excluded.context_summary,
                prompt_messages=excluded.prompt_messages,
                prompt_budget=excluded.prompt_budget,
                mechanics_results=excluded.mechanics_results,
                llm_metadata=excluded.llm_metadata,
                response_text=excluded.response_text,
                extraction_summary=excluded.extraction_summary,
                applied_delta_ids=excluded.applied_delta_ids,
                queued_review_ids=excluded.queued_review_ids,
                side_effects=excluded.side_effects,
                errors=excluded.errors,
                created_at=excluded.created_at
            """,
            (
                audit.turn_id,
                audit.campaign_id,
                audit.branch_id,
                audit.scene_id or None,
                pc_ref or None,
                composition,
                context_summary,
                prompt_messages,
                prompt_budget,
                mechanics_results,
                llm_metadata,
                audit.response_text if audit.response_text else None,
                extraction,
                applied_ids,
                review_ids,
                side_effects,
                errors,
                audit.started_at.isoformat() if audit.started_at else datetime.now(UTC).isoformat(),
            ),
        )

    async def get(self, turn_id: TurnId) -> TurnAudit | None:
        row = await self._db.fetchone("SELECT * FROM turn_audits WHERE turn_id = ?", (turn_id,))
        if row is None:
            return None
        return self._row_to_audit(dict(row))

    async def list(
        self,
        campaign_id: CampaignId,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[TurnAudit]:
        sql = "SELECT * FROM turn_audits WHERE campaign_id = ?"
        params: list[Any] = [campaign_id]
        if since is not None:
            sql += " AND created_at >= ?"
            params.append(since.isoformat())
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        rows = await self._db.fetchall(sql, tuple(params))
        return [self._row_to_audit(dict(r)) for r in rows]

    @staticmethod
    def _row_to_audit(row: dict[str, Any]) -> TurnAudit:
        """Reconstruct a TurnAudit from a stored row.

        Field values that were serialized as JSON come back as dicts; we
        let pydantic's ``model_validate`` coerce them into the right
        nested models. Unknown fields fall back to defaults.
        """
        prompt_msgs = _load(row.get("prompt_messages")) or {}
        prompt_budget = _load(row.get("prompt_budget")) or {}
        mechanics = _load(row.get("mechanics_results")) or {}
        llm = _load(row.get("llm_metadata")) or {}
        extraction = _load(row.get("extraction_summary")) or {}
        side = _load(row.get("side_effects")) or {}
        errors = _load(row.get("errors")) or {}
        composition = _load(row.get("composition"))
        ctx_summary = _load(row.get("context_summary"))

        return TurnAudit.model_validate(
            {
                "turn_id": row["turn_id"],
                "campaign_id": row["campaign_id"],
                "branch_id": row["branch_id"],
                "scene_id": row.get("scene_id") or "",
                "started_at": llm.get("started_at") or row.get("created_at"),
                "completed_at": llm.get("completed_at"),
                "duration_ms": llm.get("duration_ms"),
                "player_input": prompt_msgs.get("player_input", "") or "",
                "options": prompt_msgs.get("options") or {},
                "composition_snapshot": composition,
                "scene_break_decision": prompt_msgs.get("scene_break_decision"),
                "context_summary": ctx_summary,
                "context_sources": prompt_msgs.get("sources") or [],
                "context_budget_used": prompt_budget,
                "context_messages_hash": prompt_msgs.get("hash") or "",
                "proposed_rolls": mechanics.get("proposed") or [],
                "resolved_rolls": mechanics.get("resolved") or [],
                "llm_provider": llm.get("provider") or "",
                "llm_model": llm.get("model") or "",
                "llm_params": llm.get("params") or {},
                "llm_prompt_tokens": llm.get("prompt_tokens") or 0,
                "llm_completion_tokens": llm.get("completion_tokens") or 0,
                "llm_cost_usd": llm.get("cost_usd"),
                "llm_latency_ms": llm.get("latency_ms") or 0,
                "llm_finish_reason": llm.get("finish_reason") or "",
                "llm_retries": llm.get("retries") or 0,
                "response_text": row.get("response_text") or "",
                "extraction_strategies_run": extraction.get("strategies_run") or [],
                "extraction_duration_ms": extraction.get("duration_ms") or 0,
                "extracted_deltas": extraction.get("deltas") or [],
                "extraction_flags": extraction.get("flags") or [],
                "applied_deltas": [],  # ids only; rehydration is via state_store
                "queued_for_review": [],
                "scene_appended": side.get("scene_appended") or False,
                "scene_closed": side.get("scene_closed") or False,
                "images_scheduled": side.get("images_scheduled") or [],
                "time_advanced": side.get("time_advanced"),
                "errors": errors.get("errors") or [],
                "warnings": errors.get("warnings") or [],
            }
        )


__all__ = ["AuditStore"]
