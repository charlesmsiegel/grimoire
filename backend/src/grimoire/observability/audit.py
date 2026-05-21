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


def _coerce_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v is not None]


def _pop_matching_evidence(
    pool: list[dict[str, Any]], row: dict[str, Any]
) -> dict[str, Any] | None:
    """Find the audit's extracted-delta entry that matches a stored row.

    Correlation key is (kind, target_scope, target_id). Pops the first
    match so a turn with two same-shape deltas (e.g. two FACT_ADD against
    the same target) still gets distinct evidence per applied row.
    """
    kind = row.get("kind")
    scope = row.get("target_scope")
    target_id = row.get("target_id")
    for idx, entry in enumerate(pool):
        if (
            entry.get("kind") == kind
            and entry.get("target_scope") == scope
            and entry.get("target_id") == target_id
        ):
            return pool.pop(idx)
    return None


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
                # Verbatim assembled messages — required for the "What did
                # the model see?" debug view and byte-for-byte replay. The
                # maintenance compress pass nulls the column past the
                # configured retention window.
                "messages": list(audit.assembled_messages),
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
        applied_ids = _dump([d.id for d in audit.applied_deltas])
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

    async def deltas_for_turn(self, turn_id: TurnId) -> dict[str, list[dict[str, Any]]]:
        """Return the per-turn delta diff for the "What changed?" view.

        Joins the audit's applied/queued ids against the state-store
        ``deltas`` table, then folds in ``evidence`` / ``target_scope`` /
        ``extra`` from the audit's ``extracted_deltas`` JSON blob (which
        carry fields the SQL row does not retain). Each entry is tagged
        ``status`` = ``"auto"`` (applied automatically) or ``"queued"``
        (pending human review); queued entries also carry the
        ``review_id`` and ``review_status``.
        """
        row = await self._db.fetchone(
            "SELECT applied_delta_ids, queued_review_ids, extraction_summary "
            "FROM turn_audits WHERE turn_id = ?",
            (turn_id,),
        )
        if row is None:
            raise KeyError(f"unknown turn {turn_id!r}")

        applied_ids = _coerce_id_list(_load(row["applied_delta_ids"]))
        queued_review_ids = _coerce_id_list(_load(row["queued_review_ids"]))
        extraction = _load(row["extraction_summary"]) or {}
        evidence_pool = list(extraction.get("deltas") or [])

        applied_rows = await self._fetch_delta_rows(applied_ids)
        queued_rows, review_meta = await self._fetch_queued_rows(queued_review_ids)

        applied = [self._build_delta_entry(r, evidence_pool, status="auto") for r in applied_rows]
        queued = [
            self._build_delta_entry(
                r,
                evidence_pool,
                status="queued",
                review_id=review_meta[r["id"]]["review_id"],
                review_status=review_meta[r["id"]]["status"],
            )
            for r in queued_rows
        ]
        return {"applied": applied, "queued": queued}

    async def _fetch_delta_rows(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetchall(
            f"SELECT id, campaign_id, branch_id, turn_id, source, kind, "
            f"target_scope, target_table, target_path, target_id, "
            f"before, after, confidence, applied_at, reversed_at, notes "
            f"FROM deltas WHERE id IN ({placeholders})",
            tuple(ids),
        )
        by_id = {r["id"]: dict(r) for r in rows}
        # Preserve the caller's id ordering — applied_delta_ids reflects
        # the order in which the orchestrator applied them, which the
        # frontend uses to show "first thing that happened" first.
        return [by_id[i] for i in ids if i in by_id]

    async def _fetch_queued_rows(
        self, review_ids: list[str]
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        if not review_ids:
            return [], {}
        placeholders = ",".join("?" for _ in review_ids)
        review_rows = await self._db.fetchall(
            f"SELECT id, delta_id, status FROM review_queue WHERE id IN ({placeholders})",
            tuple(review_ids),
        )
        if not review_rows:
            return [], {}
        # Map delta_id → {review_id, status} so callers can render the
        # review state alongside the underlying delta.
        review_meta: dict[str, dict[str, Any]] = {}
        delta_ids: list[str] = []
        for r in review_rows:
            row = dict(r)
            review_meta[row["delta_id"]] = {
                "review_id": row["id"],
                "status": row["status"],
            }
            delta_ids.append(row["delta_id"])
        rows = await self._fetch_delta_rows(delta_ids)
        return rows, review_meta

    def _build_delta_entry(
        self,
        row: dict[str, Any],
        evidence_pool: list[dict[str, Any]],
        *,
        status: str,
        review_id: str | None = None,
        review_status: str | None = None,
    ) -> dict[str, Any]:
        evidence_match = _pop_matching_evidence(evidence_pool, row)
        before = _load(row.get("before"))
        after = _load(row.get("after"))
        entry: dict[str, Any] = {
            "id": row["id"],
            "kind": row.get("kind"),
            "target_scope": row.get("target_scope"),
            "target_table": row.get("target_table"),
            "target_path": row.get("target_path"),
            "target_id": row.get("target_id"),
            "before": before,
            "after": after,
            "confidence": row.get("confidence"),
            # "strategy" is the producer that emitted the delta — the
            # spec calls it strategy in the UI, the row stores it as
            # ``source`` (e.g. "extractor:wod-mechanics", "mechanics",
            # "user"). Surface both names so the frontend can pick.
            "source": row.get("source"),
            "strategy": row.get("source"),
            "evidence": evidence_match.get("evidence", "") if evidence_match else "",
            "extra": evidence_match.get("extra", {}) if evidence_match else {},
            "notes": row.get("notes") or (evidence_match.get("notes") if evidence_match else ""),
            "applied_at": row.get("applied_at"),
            "reversed_at": row.get("reversed_at"),
            "status": status,
        }
        if review_id is not None:
            entry["review_id"] = review_id
            entry["review_status"] = review_status
        return entry

    async def diff_prompts(
        self, turn_id_a: TurnId, turn_id_b: TurnId
    ) -> dict[str, Any]:
        """Diff two turns' assembled prompts.

        Returns a structured diff suitable for the "What did the model see?"
        debug view: per-message added/removed/changed entries (keyed by tier
        + role for stability), per-source added/removed entries, tier budget
        shifts, and whether the messages_hash changed.

        Raises ``KeyError`` if either turn id is unknown.
        """
        audit_a = await self.get(turn_id_a)
        audit_b = await self.get(turn_id_b)
        if audit_a is None:
            raise KeyError(f"unknown turn {turn_id_a!r}")
        if audit_b is None:
            raise KeyError(f"unknown turn {turn_id_b!r}")
        return _diff_audits(audit_a, audit_b)

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
                "assembled_messages": prompt_msgs.get("messages") or [],
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


def _msg_view(msg: Any) -> dict[str, Any]:
    """Normalize an ``assembled_messages`` entry to a comparable dict.

    Audits store messages as JSON (dicts on the way out); newer audits carry a
    ``metadata.tier`` annotation. Returns ``{role, tier, tokens, content}``
    where tokens is the cheap len/4 estimate used elsewhere in the debug view.
    """
    if hasattr(msg, "model_dump"):
        data = msg.model_dump(mode="json")
    elif isinstance(msg, dict):
        data = dict(msg)
    else:
        data = {"role": "system", "content": str(msg), "metadata": {}}
    metadata = data.get("metadata") or {}
    tier = metadata.get("tier") if isinstance(metadata, dict) else None
    content = data.get("content") or ""
    return {
        "role": str(data.get("role") or "system"),
        "tier": tier,
        "tokens": max(1, len(content) // 4) if content else 0,
        "content": content,
    }


def _source_key(src: Any) -> str:
    """Stable identity for a ContextSource across two audits."""
    if hasattr(src, "model_dump"):
        d = src.model_dump(mode="json")
    elif isinstance(src, dict):
        d = src
    else:
        return repr(src)
    sid = d.get("source_id") or ""
    if sid:
        return sid
    return f"{d.get('kind', '')}::{d.get('owner_id') or ''}"


def _source_view(src: Any) -> dict[str, Any]:
    if hasattr(src, "model_dump"):
        d = src.model_dump(mode="json")
    elif isinstance(src, dict):
        d = dict(src)
    else:
        d = {"kind": "", "owner_id": None, "tier": None, "tokens": 0, "scope": "", "summary": ""}
    return {
        "source_id": _source_key(src),
        "kind": d.get("kind", ""),
        "owner_id": d.get("owner_id"),
        "scope": d.get("scope"),
        "tier": d.get("tier"),
        "tokens": int(d.get("tokens") or 0),
        "override_applied": bool(d.get("override_applied") or False),
        "summary": d.get("summary", ""),
    }


def _diff_audits(audit_a: TurnAudit, audit_b: TurnAudit) -> dict[str, Any]:
    a_msgs = [_msg_view(m) for m in (audit_a.assembled_messages or [])]
    b_msgs = [_msg_view(m) for m in (audit_b.assembled_messages or [])]
    a_by_role_tier: dict[tuple[str, str | None], dict[str, Any]] = {}
    for m in a_msgs:
        a_by_role_tier.setdefault((m["role"], m["tier"]), m)
    b_by_role_tier: dict[tuple[str, str | None], dict[str, Any]] = {}
    for m in b_msgs:
        b_by_role_tier.setdefault((m["role"], m["tier"]), m)

    added_messages: list[dict[str, Any]] = []
    removed_messages: list[dict[str, Any]] = []
    changed_messages: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str | None]] = set()
    for key, m_a in a_by_role_tier.items():
        seen_keys.add(key)
        m_b = b_by_role_tier.get(key)
        if m_b is None:
            removed_messages.append(m_a)
            continue
        if m_a["content"] != m_b["content"]:
            changed_messages.append(
                {
                    "role": key[0],
                    "tier": key[1],
                    "before": m_a,
                    "after": m_b,
                }
            )
    for key, m_b in b_by_role_tier.items():
        if key in seen_keys:
            continue
        added_messages.append(m_b)

    a_sources = {_source_key(s): _source_view(s) for s in audit_a.context_sources}
    b_sources = {_source_key(s): _source_view(s) for s in audit_b.context_sources}
    added_sources = [v for k, v in b_sources.items() if k not in a_sources]
    removed_sources = [v for k, v in a_sources.items() if k not in b_sources]

    budget_a = {str(k): int(v) for k, v in (audit_a.context_budget_used or {}).items()}
    budget_b = {str(k): int(v) for k, v in (audit_b.context_budget_used or {}).items()}
    tiers = set(budget_a) | set(budget_b)
    tier_budget_shifts = {t: budget_b.get(t, 0) - budget_a.get(t, 0) for t in sorted(tiers)}

    return {
        "turn_id_a": audit_a.turn_id,
        "turn_id_b": audit_b.turn_id,
        "messages_hash_changed": audit_a.context_messages_hash != audit_b.context_messages_hash,
        "added_messages": added_messages,
        "removed_messages": removed_messages,
        "changed_messages": changed_messages,
        "added_sources": added_sources,
        "removed_sources": removed_sources,
        "tier_budget_shifts": tier_budget_shifts,
    }


__all__ = ["AuditStore"]
