"""Error record store (spec 16 §error reporting).

Errors attributed to their originating module. The Frontend's Health
panel surfaces recent errors grouped by module; repeated errors of the
same kind aggregate into a single entry with a count.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from grimoire.storage.db import Database
from grimoire.types.observability import ErrorRecord


class ErrorStore:
    """Read/write façade over the ``error_records`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, err: ErrorRecord) -> None:
        attribution = json.dumps(
            {
                "operation": err.operation,
                "user_visible": err.user_visible,
                "user_action_taken": err.user_action_taken,
            }
        )
        payload = json.dumps(
            {
                "traceback": err.traceback,
                "context": err.context or {},
            }
        )
        await self._db.execute(
            "INSERT INTO error_records "
            "(module, turn_id, kind, message, attribution, payload, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                err.module,
                err.turn_id,
                err.error_kind,
                err.message,
                attribution,
                payload,
                err.timestamp.isoformat() if err.timestamp else datetime.now(UTC).isoformat(),
            ),
        )

    async def recent(self, limit: int = 50) -> list[ErrorRecord]:
        rows = await self._db.fetchall(
            "SELECT module, turn_id, kind, message, attribution, payload, recorded_at "
            "FROM error_records ORDER BY recorded_at DESC LIMIT ?",
            (int(limit),),
        )
        out: list[ErrorRecord] = []
        for row in rows:
            attribution = _load_json(row["attribution"]) or {}
            payload = _load_json(row["payload"]) or {}
            out.append(
                ErrorRecord(
                    timestamp=datetime.fromisoformat(row["recorded_at"]),
                    module=row["module"],
                    operation=attribution.get("operation") or "",
                    error_kind=row["kind"] or "",
                    message=row["message"] or "",
                    turn_id=row["turn_id"],
                    traceback=payload.get("traceback"),
                    context=payload.get("context") or {},
                    user_visible=bool(attribution.get("user_visible") or False),
                    user_action_taken=attribution.get("user_action_taken"),
                )
            )
        return out

    async def aggregate_by_module(self, since: datetime | None = None) -> dict[str, dict[str, int]]:
        """Group recent errors by module + kind. Used by the Health panel."""
        sql = "SELECT module, kind, COUNT(*) AS cnt FROM error_records"
        params: tuple[Any, ...] = ()
        if since is not None:
            sql += " WHERE recorded_at >= ?"
            params = (since.isoformat(),)
        sql += " GROUP BY module, kind ORDER BY cnt DESC"
        rows = await self._db.fetchall(sql, params)
        out: dict[str, dict[str, int]] = {}
        for row in rows:
            out.setdefault(row["module"], {})[row["kind"] or ""] = int(row["cnt"] or 0)
        return out


def _load_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else None
    except (TypeError, ValueError):
        return None


__all__ = ["ErrorStore"]
