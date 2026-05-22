"""Per-module performance metrics with sample-based collection.

Each ``record`` call writes a row to ``metric_samples``. Modules tagged as
*hot path* (``orchestrator/turn``, ``llm_gateway/complete``, etc.) sample
at ``sample_rate_hot_path``; everything else is exhaustive. Queries
compute percentile latencies and counts over a rolling window.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from grimoire.observability.config import MetricsConfig
from grimoire.storage.db import Database

logger = logging.getLogger(__name__)

# Operations classed as hot paths get sample-based collection. Everything
# else is exhaustive. Producers can override per-call via ``force=True``.
_HOT_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("orchestrator", "turn"),
        ("context_builder", "build"),
        ("llm_gateway", "complete"),
        ("llm_gateway", "stream"),
        ("state_store", "query"),
        ("state_store", "write"),
        ("extractor", "extract"),
        ("scene_manager", "scene_resolve"),
        ("time_engine", "advance"),
        ("imagegen", "generate"),
    }
)


class MetricsRegistryProtocol(Protocol):
    """Narrow surface so producers can accept either a real registry or
    :class:`_NullMetrics`. Adding methods here widens every producer's
    ``metrics`` kwarg type, so keep it minimal."""

    def measure(
        self,
        module: str,
        operation: str,
        *,
        labels: dict[str, Any] | None = None,
        force: bool = False,
    ) -> Any: ...


class _NullMetrics:
    """No-op stand-in so producers don't have to branch on
    ``metrics is None``. Default value for every producer's ``metrics``
    kwarg; the lifespan swaps in a real :class:`MetricsRegistry`."""

    @asynccontextmanager
    async def measure(
        self,
        module: str,
        operation: str,
        *,
        labels: dict[str, Any] | None = None,
        force: bool = False,
    ) -> AsyncIterator[None]:
        yield


NULL_METRICS: MetricsRegistryProtocol = _NullMetrics()


class MetricsRegistry:
    """Records and queries per-module metric samples."""

    def __init__(
        self,
        db: Database,
        *,
        config: MetricsConfig | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._db = db
        self._config = config or MetricsConfig()
        self._rng = rng or random.Random()

    def _should_record(self, module: str, operation: str, force: bool) -> bool:
        if force or not self._config.enabled:
            return self._config.enabled or force
        rate = (
            self._config.sample_rate_hot_path
            if (module, operation) in _HOT_PATHS
            else self._config.sample_rate_cold_path
        )
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        return self._rng.random() < rate

    async def record(
        self,
        *,
        module: str,
        operation: str,
        duration_ms: float,
        success: bool = True,
        labels: dict[str, Any] | None = None,
        force: bool = False,
        timestamp: datetime | None = None,
    ) -> None:
        if not self._should_record(module, operation, force):
            return
        payload = {
            "operation": operation,
            "success": success,
            "labels": labels or {},
        }
        await self._db.execute(
            "INSERT INTO metric_samples (module, metric, value, labels, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                module,
                operation,
                float(duration_ms),
                json.dumps(payload),
                (timestamp or datetime.now(UTC)).isoformat(),
            ),
        )

    @asynccontextmanager
    async def measure(
        self,
        module: str,
        operation: str,
        *,
        labels: dict[str, Any] | None = None,
        force: bool = False,
    ) -> AsyncIterator[None]:
        """Time the wrapped block and record success / failure.

        - Sampling decision lives in :meth:`record` (unchanged) — the timer
          always runs but the row may be dropped at the sampling layer.
        - ``BaseException`` ensures :class:`asyncio.CancelledError` and
          ``KeyboardInterrupt`` also flag as failures.
        - A failure inside :meth:`record` is logged and swallowed so an
          observability outage never propagates into the caller's hot path.
        """
        if not self._config.enabled:
            yield
            return
        start = time.perf_counter()
        success = True
        try:
            yield
        except BaseException:
            success = False
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            try:
                await self.record(
                    module=module,
                    operation=operation,
                    duration_ms=duration_ms,
                    success=success,
                    labels=labels,
                    force=force,
                )
            except Exception:
                logger.exception("metrics.measure: record failed for %s/%s", module, operation)

    async def query_recent(
        self,
        module: str | None = None,
        operation: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if module:
            clauses.append("module = ?")
            params.append(module)
        if operation:
            clauses.append("metric = ?")
            params.append(operation)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since.isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await self._db.fetchall(
            f"SELECT module, metric, value, labels, recorded_at FROM metric_samples"
            f"{where} ORDER BY recorded_at DESC LIMIT ?",
            (*params, int(limit)),
        )
        return [dict(r) for r in rows]

    async def trend(
        self,
        module: str,
        operation: str,
        *,
        bucket: str,
        window_seconds: int,
    ) -> list[dict[str, Any]]:
        """Group samples into ``minute|hour|day`` buckets over a window.

        Each bucket carries count, success/failure split, and p50/p95/p99
        latency. Empty buckets between samples are included with zero
        counts so the caller can draw a continuous trend line.
        """
        if bucket not in ("minute", "hour", "day"):
            raise ValueError(f"bucket must be minute|hour|day, got {bucket!r}")
        if window_seconds < 1 or window_seconds > 30 * 86400:
            raise ValueError(f"window_seconds must be in [1, {30 * 86400}], got {window_seconds}")

        bucket_seconds = {"minute": 60, "hour": 3600, "day": 86400}[bucket]
        max_buckets = (window_seconds + bucket_seconds - 1) // bucket_seconds + 1
        if max_buckets > 5000:
            raise ValueError(f"requested {max_buckets} buckets (limit 5000) for bucket={bucket}")

        now = datetime.now(UTC)
        since = now - timedelta(seconds=window_seconds)
        rows = await self._db.fetchall(
            "SELECT value, labels, recorded_at FROM metric_samples "
            "WHERE module = ? AND metric = ? AND recorded_at >= ? "
            "ORDER BY recorded_at ASC",
            (module, operation, since.isoformat()),
        )

        def _truncate(ts: datetime) -> datetime:
            if bucket == "minute":
                return ts.replace(second=0, microsecond=0)
            if bucket == "hour":
                return ts.replace(minute=0, second=0, microsecond=0)
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)

        grouped: dict[datetime, list[tuple[float, bool]]] = {}
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["recorded_at"])
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            payload: dict[str, Any] = {}
            if r["labels"]:
                try:
                    payload = json.loads(r["labels"])
                except (TypeError, ValueError):
                    payload = {}
            success = bool(payload.get("success", True))
            grouped.setdefault(_truncate(ts), []).append((float(r["value"]), success))

        if not grouped:
            return []

        first_bucket = min(grouped)
        last_bucket = _truncate(now)
        buckets: list[dict[str, Any]] = []
        cursor = first_bucket
        while cursor <= last_bucket:
            samples = grouped.get(cursor, [])
            values = sorted(v for v, _ in samples)
            successes = sum(1 for _, ok in samples if ok)
            failures = len(samples) - successes
            buckets.append(
                {
                    "bucket_start": cursor.isoformat(),
                    "count": len(samples),
                    "successes": successes,
                    "failures": failures,
                    "p50_ms": _percentile(values, 0.5),
                    "p95_ms": _percentile(values, 0.95),
                    "p99_ms": _percentile(values, 0.99),
                }
            )
            cursor = cursor + timedelta(seconds=bucket_seconds)
        return buckets

    async def summary(
        self,
        module: str,
        operation: str,
        window_seconds: int | None = None,
    ) -> dict[str, float]:
        """Return count + p50/p95/p99 latency over a rolling window."""
        seconds = window_seconds or self._config.rolling_window_seconds
        since = datetime.now(UTC) - timedelta(seconds=seconds)
        rows = await self._db.fetchall(
            "SELECT value, labels FROM metric_samples "
            "WHERE module = ? AND metric = ? AND recorded_at >= ?",
            (module, operation, since.isoformat()),
        )
        values = sorted(float(r["value"]) for r in rows)
        successes = 0
        failures = 0
        for r in rows:
            try:
                payload = json.loads(r["labels"]) if r["labels"] else {}
            except (TypeError, ValueError):
                payload = {}
            if payload.get("success", True):
                successes += 1
            else:
                failures += 1
        return {
            "count": float(len(values)),
            "successes": float(successes),
            "failures": float(failures),
            "p50_ms": _percentile(values, 0.5),
            "p95_ms": _percentile(values, 0.95),
            "p99_ms": _percentile(values, 0.99),
            "max_ms": values[-1] if values else 0.0,
        }

    async def known_pairs(self) -> list[dict[str, Any]]:
        """Return every ``(module, operation)`` with a row, plus its
        latest ``recorded_at`` (so the frontend can sort by recency)."""
        rows = await self._db.fetchall(
            "SELECT module, metric, MAX(recorded_at) AS last_recorded_at "
            "FROM metric_samples GROUP BY module, metric "
            "ORDER BY MAX(recorded_at) DESC"
        )
        return [
            {
                "module": r["module"],
                "operation": r["metric"],
                "last_recorded_at": r["last_recorded_at"],
            }
            for r in rows
        ]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    pos = q * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return values[lo] * (1.0 - frac) + values[hi] * frac


__all__ = ["NULL_METRICS", "MetricsRegistry", "MetricsRegistryProtocol", "_NullMetrics"]
