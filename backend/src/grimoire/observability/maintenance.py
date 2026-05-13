"""Retention policy + nightly maintenance (spec 16 §storage).

For long-running campaigns audit data can dominate database size. This
runner applies the configured retention windows:

- Drop old log_events according to per-level retention
- Drop old metric_samples
- Drop old health_status rows
- Drop very old turn_audits / compress full prompts after compress_after

It runs once on demand or periodically (interval defaults to 24h).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from grimoire.observability.config import RetentionConfig
from grimoire.storage.db import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceReport:
    deleted_log_events: int = 0
    deleted_metric_samples: int = 0
    deleted_health_rows: int = 0
    deleted_turn_audits: int = 0
    compressed_turn_audits: int = 0
    deleted_cost_records: int = 0
    deleted_error_records: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RetentionMaintainer:
    """Applies retention windows and (optionally) runs a daily loop."""

    def __init__(
        self,
        db: Database,
        *,
        config: RetentionConfig | None = None,
        interval_seconds: int = 24 * 60 * 60,
    ) -> None:
        self._db = db
        self._config = config or RetentionConfig()
        self._interval = max(60, int(interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    async def run_once(self) -> MaintenanceReport:
        if not self._config.enabled:
            return MaintenanceReport()
        started = datetime.now(UTC)
        deleted_logs = await self._purge_logs()
        deleted_metrics = await self._purge_by_age(
            "metric_samples", "recorded_at", self._config.metric_samples_days
        )
        deleted_health = await self._purge_by_age(
            "health_status", "checked_at", self._config.health_status_days
        )
        deleted_costs = await self._purge_by_age(
            "cost_records", "recorded_at", self._config.cost_records_days
        )
        deleted_errors = await self._purge_by_age(
            "error_records", "recorded_at", self._config.error_records_days
        )
        deleted_audits = await self._purge_by_age(
            "turn_audits", "created_at", self._config.turn_audits_days
        )
        compressed = await self._compress_audits()
        return MaintenanceReport(
            deleted_log_events=deleted_logs,
            deleted_metric_samples=deleted_metrics,
            deleted_health_rows=deleted_health,
            deleted_cost_records=deleted_costs,
            deleted_error_records=deleted_errors,
            deleted_turn_audits=deleted_audits,
            compressed_turn_audits=compressed,
            started_at=started,
            completed_at=datetime.now(UTC),
        )

    async def start_periodic(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="observability-retention")

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop is not None
        self._stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._stop = None

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    async def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("retention maintainer failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue

    async def _purge_by_age(self, table: str, column: str, days: int | None) -> int:
        if days is None:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        async with self._db.acquire() as conn:
            cur = await conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
            return int(cur.rowcount or 0)

    async def _purge_logs(self) -> int:
        if not self._config.enabled:
            return 0
        total = 0
        now = datetime.now(UTC)
        for level, days in (
            ("debug", self._config.log_debug_days),
            ("info", self._config.log_info_days),
            ("warning", self._config.log_warning_days),
            ("error", self._config.log_error_days),
        ):
            if days is None:
                continue
            cutoff = (now - timedelta(days=days)).isoformat()
            async with self._db.acquire() as conn:
                cur = await conn.execute(
                    "DELETE FROM log_events WHERE recorded_at < ? AND lower(level) = ?",
                    (cutoff, level),
                )
                total += int(cur.rowcount or 0)
        return total

    async def _compress_audits(self) -> int:
        """After ``turn_audits_compress_after_days`` clear the response and
        prompt blobs but keep the row so cost/metrics joins still work."""
        days = self._config.turn_audits_compress_after_days
        if days is None:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        async with self._db.acquire() as conn:
            cur = await conn.execute(
                "UPDATE turn_audits "
                "SET response_text = NULL, prompt_messages = NULL "
                "WHERE created_at < ? "
                "AND (response_text IS NOT NULL OR prompt_messages IS NOT NULL)",
                (cutoff,),
            )
            return int(cur.rowcount or 0)


__all__ = ["MaintenanceReport", "RetentionMaintainer"]
