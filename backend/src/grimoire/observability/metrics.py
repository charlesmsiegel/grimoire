"""Per-module performance metrics with sample-based collection.

Each ``record`` call writes a row to ``metric_samples``. Modules tagged as
*hot path* (``orchestrator/turn``, ``llm_gateway/complete``, etc.) sample
at ``sample_rate_hot_path``; everything else is exhaustive. Queries
compute percentile latencies and counts over a rolling window.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from grimoire.observability.config import MetricsConfig
from grimoire.storage.db import Database

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
    }
)


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


__all__ = ["MetricsRegistry"]
