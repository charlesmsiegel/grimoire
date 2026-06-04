"""``CostTracker`` (spec 16 §cost tracking).

Each LLM call's cost is recorded as a row in ``cost_records``. Aggregations
compute total, daily, by-task and by-model breakdowns. The LLM Gateway
already writes a slim record to ``llm_requests`` per call; this writer is
the authoritative cost log used by the budget UI.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from grimoire.storage.db import Database
from grimoire.types.common import CampaignId
from grimoire.types.llm import LLMCallRecord
from grimoire.types.observability import CostTotal, DailyCost
from grimoire.util import now_iso


class CostTrackerService:
    """Concrete CostTracker backed by ``cost_records``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, call: LLMCallRecord) -> None:
        await self._db.execute(
            "INSERT INTO cost_records"
            " (campaign_id, turn_id, task, model, cost_usd,"
            " input_tokens, output_tokens, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call.campaign_id,
                call.turn_id,
                call.task,
                call.model,
                call.cost_usd,
                call.input_tokens,
                call.output_tokens,
                now_iso(),
            ),
        )

    async def total(
        self,
        campaign_id: CampaignId | None = None,
        model: str | None = None,
        task: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CostTotal:
        clauses: list[str] = []
        params: list[Any] = []
        if campaign_id:
            clauses.append("campaign_id = ?")
            params.append(campaign_id)
        if model:
            clauses.append("model = ?")
            params.append(model)
        if task:
            clauses.append("task = ?")
            params.append(task)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("recorded_at < ?")
            params.append(until.isoformat())

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT
                COALESCE(SUM(cost_usd), 0.0) AS total_usd,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COUNT(id) AS call_count
            FROM cost_records
            {where}
        """
        row = await self._db.fetchone(sql, tuple(params))
        if row is None:
            return CostTotal(total_usd=0.0)
        return CostTotal(
            total_usd=float(row["total_usd"] or 0.0),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            call_count=int(row["call_count"] or 0),
        )

    async def by_day(self, campaign_id: CampaignId, days: int = 30) -> list[DailyCost]:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = await self._db.fetchall(
            """
            SELECT substr(recorded_at, 1, 10) AS day,
                   SUM(cost_usd) AS total,
                   COUNT(id) AS calls
            FROM cost_records
            WHERE campaign_id = ? AND recorded_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (campaign_id, since.isoformat()),
        )
        out: list[DailyCost] = []
        for row in rows:
            try:
                day = datetime.fromisoformat(row["day"]).replace(tzinfo=UTC)
            except ValueError:
                continue
            out.append(
                DailyCost(
                    date=day,
                    total_usd=float(row["total"] or 0.0),
                    call_count=int(row["calls"] or 0),
                )
            )
        return out

    async def by_turn(self, turn_id: str) -> dict[str, CostTotal]:
        rows = await self._db.fetchall(
            """
            SELECT task,
                   COALESCE(SUM(cost_usd), 0.0) AS total_usd,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COUNT(id) AS call_count
            FROM cost_records
            WHERE turn_id = ?
            GROUP BY task
            """,
            (turn_id,),
        )
        return {
            (row["task"] or ""): CostTotal(
                total_usd=float(row["total_usd"] or 0.0),
                input_tokens=int(row["input_tokens"] or 0),
                output_tokens=int(row["output_tokens"] or 0),
                call_count=int(row["call_count"] or 0),
            )
            for row in rows
        }

    async def total_today(self, campaign_id: CampaignId) -> float:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        row = await self._db.fetchone(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM cost_records "
            "WHERE campaign_id = ? AND recorded_at >= ?",
            (campaign_id, midnight.isoformat()),
        )
        if row is None:
            return 0.0
        return float(row["total"] or 0.0)

    async def by_task(self, campaign_id: CampaignId) -> dict[str, float]:
        rows = await self._db.fetchall(
            "SELECT task, SUM(cost_usd) AS total FROM cost_records "
            "WHERE campaign_id = ? GROUP BY task",
            (campaign_id,),
        )
        return {(r["task"] or ""): float(r["total"] or 0.0) for r in rows}

    async def by_model(self, campaign_id: CampaignId) -> dict[str, float]:
        rows = await self._db.fetchall(
            "SELECT model, SUM(cost_usd) AS total FROM cost_records "
            "WHERE campaign_id = ? GROUP BY model",
            (campaign_id,),
        )
        return {(r["model"] or ""): float(r["total"] or 0.0) for r in rows}


__all__ = ["CostTrackerService"]
