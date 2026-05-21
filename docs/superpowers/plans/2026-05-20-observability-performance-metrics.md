# Observability Performance Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire performance metrics through all 8 producer modules (Orchestrator, Context Builder, LLM Gateway, Extractor, State Store, Scene Manager, Time Engine, ImageGen), add a trend-line endpoint, and expose a new `/observability` route with a Performance tab.

**Architecture:** A new `MetricsRegistry.measure(...)` async context manager makes producer instrumentation a one-liner; producers gain an optional `metrics` kwarg defaulting to a `_NullMetrics()` no-op so tests don't have to wire it. The trend endpoint groups raw samples into time buckets in Python (no SQLite percentile primitives). The frontend gets a brand-new `/observability` top-level route whose first tab is Performance.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest, React + TypeScript + Vite, Vitest.

**Spec:** [`docs/superpowers/specs/2026-05-20-observability-performance-metrics-design.md`](../specs/2026-05-20-observability-performance-metrics-design.md)

---

## File Inventory

**Backend — modified:**
- `backend/src/grimoire/observability/metrics.py` — add `measure()` context manager, `trend()` method, `_NullMetrics` shim, `MetricsRegistryProtocol`, expand `_HOT_PATHS`.
- `backend/src/grimoire/api/observability.py` — add `/metrics/trend` and `/metrics/known` routes.
- `backend/src/grimoire/orchestrator/service.py` — accept `metrics` kwarg, wrap `advance()` body.
- `backend/src/grimoire/context/builder.py` — accept `metrics` kwarg, wrap `build()` body.
- `backend/src/grimoire/llm_gateway/gateway.py` — accept `metrics` kwarg, wrap `complete()` and `stream()` bodies.
- `backend/src/grimoire/extractor/service.py` — accept `metrics` kwarg, wrap `extract()` body.
- `backend/src/grimoire/state_store/store.py` — accept `metrics` kwarg, wrap `_txn` (writes) + `list_scenes` / `list_world_refs` / `list_pcs` (high-traffic reads).
- `backend/src/grimoire/scenes/manager.py` — accept `metrics` kwarg, wrap `on_advance_requested()` body.
- `backend/src/grimoire/time_engine/service.py` — accept `metrics` kwarg, wrap `advance()` body.
- `backend/src/grimoire/imagegen/service.py` — accept `metrics` kwarg, wrap `generate_sync()` and `queue_generation()` bodies.
- `backend/src/grimoire/main.py` — pass `observability.metrics()` into each producer constructor at lifespan startup.

**Backend — new tests:**
- `backend/tests/observability/test_metrics_measure.py`
- `backend/tests/observability/test_metrics_trend.py`
- `backend/tests/observability/test_metrics_known.py`
- `backend/tests/api/test_observability_metrics_routes.py`
- `backend/tests/<module>/test_metrics_wiring.py` for each of the 8 wired modules.

**Frontend — new:**
- `frontend/src/api/observability.ts`
- `frontend/src/routes/observability/index.tsx`
- `frontend/src/routes/observability/ObservabilityLayout.tsx`
- `frontend/src/routes/observability/PerformanceTab.tsx`
- `frontend/src/routes/observability/useObservabilityPolling.ts`
- `frontend/src/routes/observability/Sparkline.tsx`
- `frontend/src/api/__tests__/observability.test.ts`
- `frontend/src/routes/observability/__tests__/PerformanceTab.test.tsx`
- `frontend/src/routes/observability/__tests__/useObservabilityPolling.test.tsx`

**Frontend — modified:**
- `frontend/src/App.tsx` — add `/observability/*` route.
- `frontend/src/shell/AppShell.tsx` — add top-nav link.

---

## Task 1: `MetricsRegistry.measure()` context manager

**Files:**
- Modify: `backend/src/grimoire/observability/metrics.py`
- Test: `backend/tests/observability/test_metrics_measure.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/observability/test_metrics_measure.py`:

```python
"""Tests for MetricsRegistry.measure() context manager (#355)."""

from __future__ import annotations

import asyncio
import json

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def registry(db):
    return MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))


async def _rows(db):
    return await db.fetchall("SELECT module, metric, value, labels FROM metric_samples")


@pytest.mark.asyncio
async def test_measure_records_success_on_clean_exit(registry, db):
    async with registry.measure("orchestrator", "turn"):
        await asyncio.sleep(0.001)

    rows = await _rows(db)
    assert len(rows) == 1
    assert rows[0]["module"] == "orchestrator"
    assert rows[0]["metric"] == "turn"
    assert float(rows[0]["value"]) >= 0.5  # at least 0.5ms slept
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is True


@pytest.mark.asyncio
async def test_measure_records_failure_and_reraises(registry, db):
    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with registry.measure("llm_gateway", "complete"):
            raise _Boom("nope")

    rows = await _rows(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is False


@pytest.mark.asyncio
async def test_measure_records_failure_on_cancellation(registry, db):
    async def _cancelled():
        async with registry.measure("orchestrator", "turn"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _cancelled()

    rows = await _rows(db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is False


@pytest.mark.asyncio
async def test_measure_noop_when_disabled(db):
    registry = MetricsRegistry(db, config=MetricsConfig(enabled=False))
    async with registry.measure("orchestrator", "turn"):
        pass
    rows = await _rows(db)
    assert rows == []


@pytest.mark.asyncio
async def test_measure_swallows_record_failure(registry, db, monkeypatch, caplog):
    async def _broken(*_a, **_kw):
        raise RuntimeError("db dead")

    monkeypatch.setattr(registry, "record", _broken)

    # Caller still completes — measure() doesn't propagate the record failure.
    async with registry.measure("orchestrator", "turn"):
        pass
    assert "metrics.measure: record failed" in caplog.text


@pytest.mark.asyncio
async def test_measure_passes_labels_through(registry, db):
    async with registry.measure(
        "llm_gateway", "complete", labels={"provider": "anthropic", "model": "opus"}
    ):
        pass
    rows = await _rows(db)
    payload = json.loads(rows[0]["labels"])
    assert payload["labels"] == {"provider": "anthropic", "model": "opus"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/observability/test_metrics_measure.py -v`
Expected: FAIL — `AttributeError: 'MetricsRegistry' object has no attribute 'measure'`

- [ ] **Step 3: Implement `measure()` + `_NullMetrics` + protocol**

Replace the entire contents of `backend/src/grimoire/observability/metrics.py` with the following. (Original kept intact, with three additions: `time` and `contextlib` imports, the `MetricsRegistryProtocol` + `_NullMetrics`, and the new `measure` and `trend` methods on `MetricsRegistry`. `_HOT_PATHS` is expanded.) `trend` arrives in Task 2 — leave it out for now:

```python
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
from grimoire.storage import Database

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
    """Just enough surface so producers can accept either a real registry
    or :class:`_NullMetrics`. Keep narrow — adding methods here means
    every producer's kwarg type widens too."""

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
                logger.exception(
                    "metrics.measure: record failed for %s/%s", module, operation
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


__all__ = ["MetricsRegistry", "MetricsRegistryProtocol", "_NullMetrics"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/observability/test_metrics_measure.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/observability/metrics.py backend/tests/observability/test_metrics_measure.py
git commit -m "feat(observability): add MetricsRegistry.measure() context manager (#355)"
```

---

## Task 2: `MetricsRegistry.trend()` method

**Files:**
- Modify: `backend/src/grimoire/observability/metrics.py`
- Test: `backend/tests/observability/test_metrics_trend.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/observability/test_metrics_trend.py`:

```python
"""Tests for MetricsRegistry.trend() — time-bucketed aggregation (#355)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def registry(db):
    return MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0, sample_rate_cold_path=1.0)
    )


@pytest.mark.asyncio
async def test_trend_empty_store(registry):
    buckets = await registry.trend(
        "orchestrator", "turn", bucket="minute", window_seconds=600
    )
    assert buckets == []


@pytest.mark.asyncio
async def test_trend_two_minute_buckets(registry):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    earlier = now - timedelta(minutes=1)
    # Two samples in `earlier`, one in `now`.
    await registry.record(
        module="orchestrator", operation="turn",
        duration_ms=100.0, success=True, timestamp=earlier,
    )
    await registry.record(
        module="orchestrator", operation="turn",
        duration_ms=300.0, success=False, timestamp=earlier,
    )
    await registry.record(
        module="orchestrator", operation="turn",
        duration_ms=200.0, success=True, timestamp=now,
    )

    buckets = await registry.trend(
        "orchestrator", "turn", bucket="minute", window_seconds=180
    )
    # Three buckets: now-2min (empty), earlier, now. The window is 3 minutes
    # wide (now-3min..now), so any sample in `earlier` and `now` lands; the
    # empty buckets between get filled with zeros.
    assert len(buckets) >= 2
    populated = [b for b in buckets if b["count"] > 0]
    assert len(populated) == 2
    bucket_earlier = next(b for b in populated if b["bucket_start"] == earlier.isoformat())
    assert bucket_earlier["count"] == 2
    assert bucket_earlier["successes"] == 1
    assert bucket_earlier["failures"] == 1
    bucket_now = next(b for b in populated if b["bucket_start"] == now.isoformat())
    assert bucket_now["count"] == 1
    assert bucket_now["successes"] == 1
    assert bucket_now["p50_ms"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_trend_fills_empty_buckets_with_zeros(registry):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    far_back = now - timedelta(minutes=3)
    await registry.record(
        module="orchestrator", operation="turn",
        duration_ms=100.0, timestamp=far_back,
    )
    await registry.record(
        module="orchestrator", operation="turn",
        duration_ms=200.0, timestamp=now,
    )
    buckets = await registry.trend(
        "orchestrator", "turn", bucket="minute", window_seconds=240
    )
    # 4 buckets: far_back, far_back+1m, far_back+2m, now. The two middle
    # buckets must be present with zeros.
    zero_buckets = [b for b in buckets if b["count"] == 0]
    assert len(zero_buckets) >= 1
    for b in zero_buckets:
        assert b["successes"] == 0
        assert b["failures"] == 0
        assert b["p50_ms"] == 0.0


@pytest.mark.asyncio
async def test_trend_hour_bucket(registry):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    in_first_hour = now - timedelta(minutes=30)
    await registry.record(
        module="orchestrator", operation="turn", duration_ms=150.0, timestamp=in_first_hour
    )
    buckets = await registry.trend(
        "orchestrator", "turn", bucket="hour", window_seconds=7200
    )
    populated = [b for b in buckets if b["count"] > 0]
    assert len(populated) == 1
    # The bucket start should be the *hour* containing `in_first_hour`.
    expected = in_first_hour.replace(minute=0).isoformat()
    assert populated[0]["bucket_start"] == expected


@pytest.mark.asyncio
async def test_trend_rejects_invalid_bucket(registry):
    with pytest.raises(ValueError):
        await registry.trend("orchestrator", "turn", bucket="second", window_seconds=60)


@pytest.mark.asyncio
async def test_trend_rejects_out_of_range_window(registry):
    with pytest.raises(ValueError):
        await registry.trend("orchestrator", "turn", bucket="minute", window_seconds=0)
    with pytest.raises(ValueError):
        await registry.trend(
            "orchestrator", "turn", bucket="minute", window_seconds=30 * 86400 + 1
        )


@pytest.mark.asyncio
async def test_trend_rejects_too_many_buckets(registry):
    # 31 minutes is OK (31 buckets), but `bucket=minute` over 30 days is
    # capped (>5000 buckets).
    with pytest.raises(ValueError):
        await registry.trend(
            "orchestrator", "turn", bucket="minute", window_seconds=10 * 86400
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/observability/test_metrics_trend.py -v`
Expected: FAIL — `AttributeError: 'MetricsRegistry' object has no attribute 'trend'`

- [ ] **Step 3: Implement `trend()` on `MetricsRegistry`**

Add to `backend/src/grimoire/observability/metrics.py` — append the following method to `MetricsRegistry` (place it directly after `summary`):

```python
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
            raise ValueError(
                f"window_seconds must be in [1, {30 * 86400}], got {window_seconds}"
            )

        bucket_seconds = {"minute": 60, "hour": 3600, "day": 86400}[bucket]
        max_buckets = (window_seconds + bucket_seconds - 1) // bucket_seconds + 1
        if max_buckets > 5000:
            raise ValueError(
                f"requested {max_buckets} buckets (limit 5000) for bucket={bucket}"
            )

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

        # Group rows by truncated bucket-start.
        grouped: dict[datetime, list[tuple[float, bool]]] = {}
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["recorded_at"])
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            payload = {}
            if r["labels"]:
                try:
                    payload = json.loads(r["labels"])
                except (TypeError, ValueError):
                    payload = {}
            success = bool(payload.get("success", True))
            grouped.setdefault(_truncate(ts), []).append((float(r["value"]), success))

        if not grouped:
            return []

        # Build the full bucket axis from the earliest sample bucket up to
        # `now`'s bucket so callers get a continuous time series.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/observability/test_metrics_trend.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/observability/metrics.py backend/tests/observability/test_metrics_trend.py
git commit -m "feat(observability): add MetricsRegistry.trend() (#355)"
```

---

## Task 3: `/metrics/trend` and `/metrics/known` routes

**Files:**
- Modify: `backend/src/grimoire/api/observability.py`
- Test: `backend/tests/api/test_observability_metrics_routes.py`
- Test: `backend/tests/observability/test_metrics_known.py`

- [ ] **Step 1: Write the failing test files**

Create `backend/tests/observability/test_metrics_known.py`:

```python
"""Tests for MetricsRegistry.known_pairs() — pairs index (#355)."""

from __future__ import annotations

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def registry(db):
    return MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0, sample_rate_cold_path=1.0)
    )


@pytest.mark.asyncio
async def test_known_pairs_empty(registry):
    pairs = await registry.known_pairs()
    assert pairs == []


@pytest.mark.asyncio
async def test_known_pairs_returns_unique_pairs_sorted_by_recency(registry):
    await registry.record(module="orchestrator", operation="turn", duration_ms=10.0)
    await registry.record(module="llm_gateway", operation="complete", duration_ms=20.0)
    await registry.record(module="orchestrator", operation="turn", duration_ms=15.0)
    pairs = await registry.known_pairs()
    assert {(p["module"], p["operation"]) for p in pairs} == {
        ("orchestrator", "turn"),
        ("llm_gateway", "complete"),
    }
    # `last_recorded_at` is present on every entry.
    assert all("last_recorded_at" in p for p in pairs)
```

Create `backend/tests/api/test_observability_metrics_routes.py`:

```python
"""Integration tests for /api/observability/metrics/trend and /known (#355)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.main import create_app
from grimoire.observability.config import MetricsConfig
from grimoire.observability.service import ObservabilityService
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def app_with_obs(tmp_path):
    db = Database(tmp_path / "api.db")
    await db.connect()
    await apply_migrations(db)
    obs = ObservabilityService(db=db)
    # Force exhaustive sampling so the test fixture rows are recorded.
    obs.metrics_registry = type(obs.metrics_registry)(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0, sample_rate_cold_path=1.0)
    )
    container = ServiceContainer(db=db, observability=obs)
    app = create_app()
    app.state.container = container
    try:
        yield app, obs
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_trend_route_happy_path(app_with_obs):
    app, obs = app_with_obs
    await obs.metrics().record(module="orchestrator", operation="turn", duration_ms=150.0)
    with TestClient(app) as client:
        resp = client.get(
            "/api/observability/metrics/trend",
            params={
                "module": "orchestrator",
                "operation": "turn",
                "bucket": "minute",
                "window_seconds": 600,
            },
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, list)
    assert payload and payload[-1]["count"] >= 1


@pytest.mark.asyncio
async def test_trend_route_400_on_bad_bucket(app_with_obs):
    app, _ = app_with_obs
    with TestClient(app) as client:
        resp = client.get(
            "/api/observability/metrics/trend",
            params={
                "module": "orchestrator",
                "operation": "turn",
                "bucket": "second",
                "window_seconds": 60,
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_trend_route_400_on_bad_window(app_with_obs):
    app, _ = app_with_obs
    with TestClient(app) as client:
        resp = client.get(
            "/api/observability/metrics/trend",
            params={
                "module": "orchestrator",
                "operation": "turn",
                "bucket": "minute",
                "window_seconds": 0,
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_known_route_returns_pairs(app_with_obs):
    app, obs = app_with_obs
    await obs.metrics().record(module="orchestrator", operation="turn", duration_ms=10.0)
    await obs.metrics().record(module="llm_gateway", operation="complete", duration_ms=20.0)
    with TestClient(app) as client:
        resp = client.get("/api/observability/metrics/known")
    assert resp.status_code == 200
    payload = resp.json()
    pairs = {(p["module"], p["operation"]) for p in payload}
    assert pairs == {("orchestrator", "turn"), ("llm_gateway", "complete")}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```sh
cd backend && uv run pytest tests/observability/test_metrics_known.py tests/api/test_observability_metrics_routes.py -v
```
Expected: FAIL — `known_pairs` missing on `MetricsRegistry`; `/metrics/trend` and `/metrics/known` routes return 404.

- [ ] **Step 3: Implement `known_pairs()` on `MetricsRegistry`**

Append to `MetricsRegistry` (after `trend`):

```python
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
```

- [ ] **Step 4: Add the routes**

Modify `backend/src/grimoire/api/observability.py` — append after the existing `get_metrics_recent` handler (around line 134):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```sh
cd backend && uv run pytest tests/observability/test_metrics_known.py tests/api/test_observability_metrics_routes.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/observability/metrics.py backend/src/grimoire/api/observability.py backend/tests/observability/test_metrics_known.py backend/tests/api/test_observability_metrics_routes.py
git commit -m "feat(api): add /metrics/trend and /metrics/known routes (#355)"
```

---

## Task 4: Wire Orchestrator

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Modify: `backend/src/grimoire/main.py` (lifespan)
- Test: `backend/tests/orchestrator/test_metrics_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/orchestrator/test_metrics_wiring.py`:

```python
"""Verify the Orchestrator threads metrics through `advance()` (#355).

Doesn't retest measure() semantics — just that the producer wires it.
"""

from __future__ import annotations

import json

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "orch.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_orchestrator_advance_records_metric(db, monkeypatch):
    """Invoking OrchestratorService.advance() emits a metric row.

    We patch enough of the internals to make `advance()` return quickly
    without exercising scene/LLM/state-store integration — the test only
    cares that the `measure("orchestrator", "turn")` wrapper records.
    """
    from grimoire.orchestrator.service import OrchestratorService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    # Build an OrchestratorService whose deps are all no-op stubs. The
    # service exposes `advance(campaign_id, scene_id)`; we patch the body
    # to return early via the `_advance_inner` seam (added in Step 3).
    orch = OrchestratorService.__new__(OrchestratorService)
    orch._metrics = metrics  # injected dep
    # Bind only the wrapper; the production `advance` calls `_advance_inner`.
    from types import MethodType

    async def _stub_inner(self, campaign_id, scene_id):
        return None

    orch._advance_inner = MethodType(_stub_inner, orch)
    orch.advance = MethodType(OrchestratorService.advance, orch)

    await orch.advance("camp1", "scene1")  # type: ignore[arg-type]

    rows = await db.fetchall("SELECT module, metric, labels FROM metric_samples")
    assert any(
        r["module"] == "orchestrator" and r["metric"] == "turn"
        and json.loads(r["labels"])["success"] is True
        for r in rows
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator/test_metrics_wiring.py -v`
Expected: FAIL — `_advance_inner` does not exist; `_metrics` is not consulted.

- [ ] **Step 3: Refactor `OrchestratorService.advance()` to split body into `_advance_inner`**

In `backend/src/grimoire/orchestrator/service.py`:

- Import `MetricsRegistryProtocol` and `_NullMetrics` at the top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to the `__init__` kwargs and store as `self._metrics = metrics`.
- Rename the existing `advance(self, campaign_id, scene_id)` method body to a new private `_advance_inner(self, campaign_id, scene_id)` with the same body.
- Define a new thin `advance` that wraps it:

```python
    async def advance(self, campaign_id: CampaignId, scene_id: SceneId) -> AdvanceResult:
        async with self._metrics.measure("orchestrator", "turn"):
            return await self._advance_inner(campaign_id, scene_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/orchestrator/test_metrics_wiring.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Wire the registry in at lifespan**

In `backend/src/grimoire/main.py`, find the `OrchestratorService(...)` constructor (around line 501) and add `metrics=obs.metrics()` as a kwarg:

```python
container.orchestrator = OrchestratorService(
    event_bus=container.event_bus,
    scene_manager=container.scenes,
    llm_gateway=llm_gateway,
    context_builder=context_builder,
    extractor=extractor,
    state_store=container.state_store,
    mechanics=container.mechanics,
    world=container.world,
    continuity=container.continuity,
    transient_state=container.transient_state,
    ws_push=container.stream.push,
    metrics=obs.metrics(),  # ← new
)
```

- [ ] **Step 6: Run the orchestrator's existing test suite to confirm no regression**

Run: `cd backend && uv run pytest tests/orchestrator/ -v`
Expected: All existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/orchestrator/service.py backend/src/grimoire/main.py backend/tests/orchestrator/test_metrics_wiring.py
git commit -m "feat(orchestrator): record metrics for turn advance (#355)"
```

---

## Task 5: Wire Context Builder

**Files:**
- Modify: `backend/src/grimoire/context/builder.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/context/test_metrics_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/context/test_metrics_wiring.py`:

```python
"""Verify ContextBuilderService threads metrics through `build()` (#355)."""

from __future__ import annotations

import json
from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ctx.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_context_builder_build_records_metric(db):
    from grimoire.context.builder import ContextBuilderService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    svc = ContextBuilderService.__new__(ContextBuilderService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    svc._build_inner = MethodType(_inner, svc)
    svc.build = MethodType(ContextBuilderService.build, svc)

    await svc.build(None, None, None, None)  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric, labels FROM metric_samples "
        "WHERE module = 'context_builder' AND metric = 'build'"
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["labels"])["success"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_metrics_wiring.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `ContextBuilderService.build()` into `_build_inner` + wrapper**

In `backend/src/grimoire/context/builder.py`:

- Add at top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__` kwargs; store on `self._metrics`.
- Rename the existing `build(...)` body to `_build_inner(...)`; preserve the exact signature (positional + keyword args) so the wrapper can forward `*args, **kwargs`.
- Add the wrapper:

```python
    async def build(self, *args: Any, **kwargs: Any):
        async with self._metrics.measure("context_builder", "build"):
            return await self._build_inner(*args, **kwargs)
```

(Keep the type annotations on `_build_inner` so callers and IDEs still get help.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/context/test_metrics_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `ContextBuilderService(...)` constructor (around line 429) to pass `metrics=obs.metrics()`:

```python
container.extras["context_builder"] = ContextBuilderService(
    library=container.library,
    characters=container.characters,
    world=container.world,
    scenes=container.scenes,
    continuity=container.continuity,
    mechanics=container.mechanics,
    gateway=llm_gateway,
    state_store=container.state_store,
    transient_state=container.transient_state,
    metrics=obs.metrics(),  # ← new
)
```

- [ ] **Step 6: Run the existing context-builder tests to confirm no regression**

Run: `cd backend && uv run pytest tests/context/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/context/builder.py backend/src/grimoire/main.py backend/tests/context/test_metrics_wiring.py
git commit -m "feat(context): record metrics for context build (#355)"
```

---

## Task 6: Wire LLM Gateway

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/gateway.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/llm_gateway/test_metrics_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/llm_gateway/test_metrics_wiring.py`:

```python
"""Verify LLMGatewayService threads metrics through complete() and stream() (#355)."""

from __future__ import annotations

import json
from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "gw.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_complete_records_metric_with_labels(db):
    from grimoire.llm_gateway.gateway import LLMGatewayService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    gw = LLMGatewayService.__new__(LLMGatewayService)
    gw._metrics = metrics
    gw._metrics_labels = lambda task, request: {"provider": "fake", "model": "test"}

    async def _inner(self, *args, **kwargs):
        return None

    gw._complete_inner = MethodType(_inner, gw)
    gw.complete = MethodType(LLMGatewayService.complete, gw)

    await gw.complete("turn", object())  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT labels FROM metric_samples "
        "WHERE module = 'llm_gateway' AND metric = 'complete'"
    )
    assert len(rows) == 1
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is True
    assert payload["labels"] == {"provider": "fake", "model": "test"}


@pytest.mark.asyncio
async def test_stream_records_metric(db):
    from grimoire.llm_gateway.gateway import LLMGatewayService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    gw = LLMGatewayService.__new__(LLMGatewayService)
    gw._metrics = metrics
    gw._metrics_labels = lambda task, request: {"provider": "fake", "model": "test"}

    async def _inner_gen(self, *args, **kwargs):
        if False:
            yield  # pragma: no cover
        return

    gw._stream_inner = MethodType(_inner_gen, gw)
    gw.stream = MethodType(LLMGatewayService.stream, gw)

    async for _ in gw.stream("turn", object()):  # type: ignore[arg-type]
        pass

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples WHERE metric = 'stream'"
    )
    assert len(rows) == 1
    assert rows[0]["module"] == "llm_gateway"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/llm_gateway/test_metrics_wiring.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `complete()` and `stream()` in `gateway.py`**

In `backend/src/grimoire/llm_gateway/gateway.py`:

- Add at the top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__`; store on `self._metrics`.
- Add a small helper near the top of the class:

```python
    def _metrics_labels(self, task: str, request: Any) -> dict[str, Any]:
        # Best-effort: provider+model from the resolved route. Producers
        # should never fail recording over a missing label.
        provider = getattr(request, "provider_id", None) or getattr(request, "provider", None)
        model = getattr(request, "model", None)
        labels: dict[str, Any] = {"task": task}
        if provider:
            labels["provider"] = str(provider)
        if model:
            labels["model"] = str(model)
        return labels
```

- Rename the existing `complete(...)` body to `_complete_inner(...)` (same signature).
- Add the wrapper:

```python
    async def complete(self, task: str, request: Any, **kwargs: Any) -> Any:
        async with self._metrics.measure(
            "llm_gateway", "complete", labels=self._metrics_labels(task, request)
        ):
            return await self._complete_inner(task, request, **kwargs)
```

- Rename the existing `stream(...)` body to `_stream_inner(...)`.
- Add the wrapper (async generator — note `async with` outside the `async for`):

```python
    async def stream(self, task: str, request: Any, **kwargs: Any):
        async with self._metrics.measure(
            "llm_gateway", "stream", labels=self._metrics_labels(task, request)
        ):
            async for chunk in self._stream_inner(task, request, **kwargs):
                yield chunk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/llm_gateway/test_metrics_wiring.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `LLMGatewayService(...)` constructor (around line 356) to pass `metrics=obs.metrics()`:

```python
container.extras["llm_gateway"] = LLMGatewayService(
    plugins=container.plugins,
    db=db,
    config=settings.llm_gateway.to_gateway_config(),
    data_root=settings.data_root,
    event_bus=container.event_bus,
    health_monitor=obs.health_monitor,
    metrics=obs.metrics(),  # ← new
)
```

- [ ] **Step 6: Run existing gateway tests to confirm no regression**

Run: `cd backend && uv run pytest tests/llm_gateway/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/llm_gateway/gateway.py backend/src/grimoire/main.py backend/tests/llm_gateway/test_metrics_wiring.py
git commit -m "feat(llm_gateway): record metrics for complete and stream (#355)"
```

---

## Task 7: Wire Extractor

**Files:**
- Modify: `backend/src/grimoire/extractor/service.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/extractor/test_metrics_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/extractor/test_metrics_wiring.py`:

```python
"""Verify ExtractorService threads metrics through extract() (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ex.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_extract_records_metric(db):
    from grimoire.extractor.service import ExtractorService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    svc = ExtractorService.__new__(ExtractorService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    svc._extract_inner = MethodType(_inner, svc)
    svc.extract = MethodType(ExtractorService.extract, svc)

    await svc.extract()  # type: ignore[call-arg]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'extractor' AND metric = 'extract'"
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/extractor/test_metrics_wiring.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `ExtractorService.extract()`**

In `backend/src/grimoire/extractor/service.py`:

- Add at top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__`; store on `self._metrics`.
- Rename the existing `extract(...)` body to `_extract_inner(...)`.
- Add wrapper:

```python
    async def extract(self, *args: Any, **kwargs: Any):
        async with self._metrics.measure("extractor", "extract"):
            return await self._extract_inner(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/extractor/test_metrics_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `ExtractorService(...)` constructor (around line 411):

```python
container.extras["extractor"] = ExtractorService(
    gateway=llm_gateway,
    metrics=obs.metrics(),  # ← new
)
```

- [ ] **Step 6: Run existing extractor tests**

Run: `cd backend && uv run pytest tests/extractor/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/extractor/service.py backend/src/grimoire/main.py backend/tests/extractor/test_metrics_wiring.py
git commit -m "feat(extractor): record metrics for extract (#355)"
```

---

## Task 8: Wire State Store

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/state_store/test_metrics_wiring.py`

State Store has many read methods and a single transaction context manager for writes. We instrument:
- **writes** at the `_txn` async context manager (every write goes through it),
- **queries** at three high-traffic reads called from the orchestrator/context-builder hot paths: `list_scenes`, `list_world_refs`, `list_pcs`.

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/state_store/test_metrics_wiring.py`:

```python
"""Verify StateStore threads metrics through writes and reads (#355)."""

from __future__ import annotations

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ss.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def metrics(db):
    return MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )


@pytest.mark.asyncio
async def test_txn_records_state_store_write_metric(db, metrics, tmp_path):
    store = StateStore(db=db, data_root=tmp_path, metrics=metrics)
    async with store._txn() as conn:
        await conn.execute(
            "INSERT INTO library_index (id, kind, world_id, path, name) "
            "VALUES ('a','character','w','x','y')"
        )
    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'state_store' AND metric = 'write'"
    )
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_list_scenes_records_query_metric(db, metrics, tmp_path):
    store = StateStore(db=db, data_root=tmp_path, metrics=metrics)
    await store.list_scenes(campaign_id="c1")
    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'state_store' AND metric = 'query'"
    )
    assert len(rows) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/state_store/test_metrics_wiring.py -v`
Expected: FAIL — `StateStore.__init__` does not accept `metrics`.

- [ ] **Step 3: Add `metrics` kwarg and wrap `_txn` + three reads**

In `backend/src/grimoire/state_store/store.py`:

- Add at top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__`; store on `self._metrics`.
- Modify `_txn` (the `@asynccontextmanager` at line ~158). Currently it wraps connection acquisition + commit/rollback. Wrap the body with `measure`:

```python
    @asynccontextmanager
    async def _txn(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._metrics.measure("state_store", "write"):
            # existing body of _txn unchanged
            ...
```

(Move the existing body verbatim inside the `async with`. Keep `aiosqlite.Connection` yield, commit/rollback handling, all unchanged.)

- For the three reads (`list_scenes`, `list_world_refs`, `list_pcs`), refactor each into `_<name>_inner` + a wrapper that wraps `_inner` in `self._metrics.measure("state_store", "query")`. Example for `list_scenes`:

```python
    async def list_scenes(self, campaign_id: str, branch_id: str | None = None) -> list[dict]:
        async with self._metrics.measure("state_store", "query"):
            return await self._list_scenes_inner(campaign_id, branch_id)

    async def _list_scenes_inner(self, campaign_id: str, branch_id: str | None = None) -> list[dict]:
        # original body of list_scenes, unchanged
        ...
```

Repeat the same pattern for `list_world_refs` and `list_pcs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/state_store/test_metrics_wiring.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `StateStore(...)` constructor (around line 196):

```python
if container.state_store is None:
    container.state_store = StateStore(
        db=db, data_root=data_root, metrics=obs.metrics()
    )
```

Note: this construction lives **before** `ObservabilityService` is built (line 316). Move the StateStore construction to after the ObservabilityService construction, OR — preferred — keep StateStore construction where it is, and patch `state_store._metrics = obs.metrics()` after `obs` is constructed:

```python
if container.observability is None:
    obs = ObservabilityService(db=db, event_bus=container.event_bus)
    container.observability = obs
    # Backfill the state_store with the now-available metrics registry.
    if container.state_store is not None:
        container.state_store._metrics = obs.metrics()
else:
    obs = container.observability
```

Use the post-hoc patch — moving the StateStore construction risks other ordering bugs.

- [ ] **Step 6: Run existing state-store tests**

Run: `cd backend && uv run pytest tests/state_store/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/state_store/store.py backend/src/grimoire/main.py backend/tests/state_store/test_metrics_wiring.py
git commit -m "feat(state_store): record metrics for txn writes and list reads (#355)"
```

---

## Task 9: Wire Scene Manager

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/scenes/test_metrics_wiring.py`

The "hot path" for SceneManager is `on_advance_requested()` — called once per turn by the orchestrator. We instrument that.

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/scenes/test_metrics_wiring.py`:

```python
"""Verify SceneManager threads metrics through on_advance_requested() (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "sc.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scene_manager_advance_records_metric(db):
    from grimoire.scenes.manager import SceneManager

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    sm = SceneManager.__new__(SceneManager)
    sm._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    sm._on_advance_requested_inner = MethodType(_inner, sm)
    sm.on_advance_requested = MethodType(SceneManager.on_advance_requested, sm)

    await sm.on_advance_requested("scene1")

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'scene_manager' AND metric = 'scene_resolve'"
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenes/test_metrics_wiring.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `SceneManager.on_advance_requested()`**

In `backend/src/grimoire/scenes/manager.py`:

- Add at top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__`; store on `self._metrics`.
- Rename the existing `on_advance_requested(...)` body to `_on_advance_requested_inner(...)`.
- Add wrapper:

```python
    async def on_advance_requested(self, scene_id: str) -> AdvanceResult:
        async with self._metrics.measure("scene_manager", "scene_resolve"):
            return await self._on_advance_requested_inner(scene_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_metrics_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `SceneManager(...)` constructor (around line 248). SceneManager is constructed before `obs`; use the same post-hoc patch pattern as State Store:

```python
if container.observability is None:
    obs = ObservabilityService(db=db, event_bus=container.event_bus)
    container.observability = obs
    # Backfill the producers constructed before the observability service.
    if container.state_store is not None:
        container.state_store._metrics = obs.metrics()
    if container.scenes is not None:
        container.scenes._metrics = obs.metrics()
else:
    obs = container.observability
```

- [ ] **Step 6: Run existing scene tests**

Run: `cd backend && uv run pytest tests/scenes/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/src/grimoire/main.py backend/tests/scenes/test_metrics_wiring.py
git commit -m "feat(scenes): record metrics for scene resolve (#355)"
```

---

## Task 10: Wire Time Engine

**Files:**
- Modify: `backend/src/grimoire/time_engine/service.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/time_engine/test_metrics_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/time_engine/test_metrics_wiring.py`:

```python
"""Verify TimeEngineService threads metrics through advance() (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "te.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_time_engine_advance_records_metric(db):
    from grimoire.time_engine.service import TimeEngineService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    te = TimeEngineService.__new__(TimeEngineService)
    te._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    te._advance_inner = MethodType(_inner, te)
    te.advance = MethodType(TimeEngineService.advance, te)

    await te.advance()  # type: ignore[call-arg]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'time_engine' AND metric = 'advance'"
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/time_engine/test_metrics_wiring.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `TimeEngineService.advance()`**

In `backend/src/grimoire/time_engine/service.py`:

- Add at top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__`; store on `self._metrics`.
- Rename the existing `advance(...)` body (line ~441) to `_advance_inner(...)`.
- Add wrapper:

```python
    async def advance(self, *args: Any, **kwargs: Any):
        async with self._metrics.measure("time_engine", "advance"):
            return await self._advance_inner(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/time_engine/test_metrics_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `TimeEngineService(...)` constructor (around line 444):

```python
container.time_engine = TimeEngineService(
    store=container.state_store,
    world=container.world,
    characters=container.characters,
    mechanics=container.mechanics,
    continuity=container.continuity,
    event_bus=container.event_bus,
    metrics=obs.metrics(),  # ← new
)
```

- [ ] **Step 6: Run existing time-engine tests**

Run: `cd backend && uv run pytest tests/time_engine/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/time_engine/service.py backend/src/grimoire/main.py backend/tests/time_engine/test_metrics_wiring.py
git commit -m "feat(time_engine): record metrics for advance (#355)"
```

---

## Task 11: Wire ImageGen

**Files:**
- Modify: `backend/src/grimoire/imagegen/service.py`
- Modify: `backend/src/grimoire/main.py`
- Test: `backend/tests/imagegen/test_metrics_wiring.py`

ImageGen has two entry points: `generate_sync()` (used by EPUB cover gen, line ~561) and `queue_generation()` (used by the play loop, line ~385). Both wrap with the same `imagegen / generate` operation but the labels can vary by backend.

- [ ] **Step 1: Write the failing wiring test**

Create `backend/tests/imagegen/test_metrics_wiring.py`:

```python
"""Verify ImageGenService threads metrics through both entry points (#355)."""

from __future__ import annotations

from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ig.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_generate_sync_records_metric(db):
    from grimoire.imagegen.service import ImageGenService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    svc = ImageGenService.__new__(ImageGenService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return None

    svc._generate_sync_inner = MethodType(_inner, svc)
    svc.generate_sync = MethodType(ImageGenService.generate_sync, svc)

    await svc.generate_sync("c1", object())  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'imagegen' AND metric = 'generate'"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_queue_generation_records_metric(db):
    from grimoire.imagegen.service import ImageGenService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    svc = ImageGenService.__new__(ImageGenService)
    svc._metrics = metrics

    async def _inner(self, *args, **kwargs):
        return "job-id"

    svc._queue_generation_inner = MethodType(_inner, svc)
    svc.queue_generation = MethodType(ImageGenService.queue_generation, svc)

    await svc.queue_generation("c1", object())  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT module, metric FROM metric_samples "
        "WHERE module = 'imagegen' AND metric = 'generate'"
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/imagegen/test_metrics_wiring.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `generate_sync()` and `queue_generation()`**

In `backend/src/grimoire/imagegen/service.py`:

- Add at top:

```python
from grimoire.observability.metrics import MetricsRegistryProtocol, _NullMetrics
```

- Add `metrics: MetricsRegistryProtocol = _NullMetrics()` to `__init__`; store on `self._metrics`.
- Rename existing `generate_sync(...)` body to `_generate_sync_inner(...)`. Wrapper:

```python
    async def generate_sync(self, *args: Any, **kwargs: Any):
        async with self._metrics.measure("imagegen", "generate"):
            return await self._generate_sync_inner(*args, **kwargs)
```

- Rename existing `queue_generation(...)` body to `_queue_generation_inner(...)`. Wrapper:

```python
    async def queue_generation(self, *args: Any, **kwargs: Any):
        async with self._metrics.measure("imagegen", "generate"):
            return await self._queue_generation_inner(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/imagegen/test_metrics_wiring.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire at lifespan**

In `backend/src/grimoire/main.py`, modify the `ImageGenService(...)` constructor (around line 289). ImageGen is constructed before `obs`; extend the post-hoc patch:

```python
if container.observability is None:
    obs = ObservabilityService(db=db, event_bus=container.event_bus)
    container.observability = obs
    if container.state_store is not None:
        container.state_store._metrics = obs.metrics()
    if container.scenes is not None:
        container.scenes._metrics = obs.metrics()
    if container.imagegen is not None:
        container.imagegen._metrics = obs.metrics()
else:
    obs = container.observability
```

- [ ] **Step 6: Run existing imagegen tests**

Run: `cd backend && uv run pytest tests/imagegen/ -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/imagegen/service.py backend/src/grimoire/main.py backend/tests/imagegen/test_metrics_wiring.py
git commit -m "feat(imagegen): record metrics for generate (#355)"
```

---

## Task 12: Frontend — observability API client

**Files:**
- Create: `frontend/src/api/observability.ts`
- Test: `frontend/src/api/__tests__/observability.test.ts`

- [ ] **Step 1: Write the failing test file**

Create `frontend/src/api/__tests__/observability.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { observabilityApi } from "../observability";

const realFetch = global.fetch;

describe("observabilityApi", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  afterEach(() => {
    global.fetch = realFetch;
  });

  it("getMetricsKnown calls /api/observability/metrics/known", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => [{ module: "orchestrator", operation: "turn", last_recorded_at: "x" }],
    });
    const result = await observabilityApi.getMetricsKnown();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/observability/metrics/known",
      expect.any(Object),
    );
    expect(result).toEqual([
      { module: "orchestrator", operation: "turn", last_recorded_at: "x" },
    ]);
  });

  it("getMetricsSummary forwards module + operation + windowSeconds", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ count: 1, p50_ms: 10 }),
    });
    await observabilityApi.getMetricsSummary("orchestrator", "turn", 3600);
    const url = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/api/observability/metrics/summary");
    expect(url).toContain("module=orchestrator");
    expect(url).toContain("operation=turn");
    expect(url).toContain("window_seconds=3600");
  });

  it("getMetricsTrend forwards bucket and window_seconds", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    await observabilityApi.getMetricsTrend("llm_gateway", "complete", "minute", 600);
    const url = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("bucket=minute");
    expect(url).toContain("window_seconds=600");
  });

  it("throws when response is not ok", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => "boom",
    });
    await expect(observabilityApi.getMetricsKnown()).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm exec vitest run src/api/__tests__/observability.test.ts`
Expected: FAIL — file `../observability` not found.

- [ ] **Step 3: Implement the API client**

Create `frontend/src/api/observability.ts`:

```ts
export interface MetricsKnownPair {
  module: string;
  operation: string;
  last_recorded_at: string | null;
}

export interface MetricsSummary {
  count: number;
  successes: number;
  failures: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
}

export interface MetricsTrendBucket {
  bucket_start: string;
  count: number;
  successes: number;
  failures: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export type TrendBucketSize = "minute" | "hour" | "day";

async function jsonGet<T>(url: string): Promise<T> {
  const resp = await fetch(url, { credentials: "same-origin" });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`GET ${url} failed: ${resp.status} ${detail}`);
  }
  return (await resp.json()) as T;
}

export const observabilityApi = {
  async getMetricsKnown(): Promise<MetricsKnownPair[]> {
    return jsonGet("/api/observability/metrics/known");
  },

  async getMetricsSummary(
    module: string,
    operation: string,
    windowSeconds?: number,
  ): Promise<MetricsSummary> {
    const params = new URLSearchParams({ module, operation });
    if (windowSeconds !== undefined) params.set("window_seconds", String(windowSeconds));
    return jsonGet(`/api/observability/metrics/summary?${params.toString()}`);
  },

  async getMetricsTrend(
    module: string,
    operation: string,
    bucket: TrendBucketSize,
    windowSeconds: number,
  ): Promise<MetricsTrendBucket[]> {
    const params = new URLSearchParams({
      module,
      operation,
      bucket,
      window_seconds: String(windowSeconds),
    });
    return jsonGet(`/api/observability/metrics/trend?${params.toString()}`);
  },
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && pnpm exec vitest run src/api/__tests__/observability.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/observability.ts frontend/src/api/__tests__/observability.test.ts
git commit -m "feat(frontend): add observability API client (#355)"
```

---

## Task 13: Frontend — `useObservabilityPolling` hook

**Files:**
- Create: `frontend/src/routes/observability/useObservabilityPolling.ts`
- Test: `frontend/src/routes/observability/__tests__/useObservabilityPolling.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/observability/__tests__/useObservabilityPolling.test.tsx`:

```tsx
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useObservabilityPolling } from "../useObservabilityPolling";

describe("useObservabilityPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("calls the callback immediately and on every interval while visible", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useObservabilityPolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(cb).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it("pauses polling when the document is hidden", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useObservabilityPolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("resumes when visibility returns", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useObservabilityPolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(cb).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(cb).toHaveBeenCalledTimes(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm exec vitest run src/routes/observability/__tests__/useObservabilityPolling.test.tsx`
Expected: FAIL — file not found.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/routes/observability/useObservabilityPolling.ts`:

```ts
import { useEffect, useRef } from "react";

/**
 * Poll a callback on a fixed interval while the document is visible.
 * Pauses on `visibilitychange` to `hidden`, fires immediately on resume.
 *
 * - The callback is called once at mount.
 * - Errors from the callback are swallowed (the caller is expected to
 *   handle its own failure UX; we don't want a transient fetch error to
 *   tear down the polling loop).
 */
export function useObservabilityPolling(
  callback: () => Promise<void>,
  intervalMs: number,
): void {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const fire = async () => {
      if (cancelled) return;
      try {
        await cbRef.current();
      } catch {
        // intentional — see jsdoc
      }
    };

    const start = () => {
      if (timer !== null) return;
      void fire();
      timer = setInterval(() => void fire(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        start();
      } else {
        stop();
      }
    };

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && pnpm exec vitest run src/routes/observability/__tests__/useObservabilityPolling.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/observability/useObservabilityPolling.ts frontend/src/routes/observability/__tests__/useObservabilityPolling.test.tsx
git commit -m "feat(frontend): add useObservabilityPolling hook (#355)"
```

---

## Task 14: Frontend — `Sparkline` component

**Files:**
- Create: `frontend/src/routes/observability/Sparkline.tsx`

Tiny presentational component used by `PerformanceTab`. No separate test file — coverage comes through `PerformanceTab.test.tsx`.

- [ ] **Step 1: Create the component**

```tsx
interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  ariaLabel: string;
}

export function Sparkline({ values, width = 120, height = 24, ariaLabel }: SparklineProps) {
  if (values.length === 0) {
    return (
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        className="observability-sparkline empty"
      />
    );
  }
  const max = Math.max(...values, 1);
  const stepX = values.length > 1 ? width / (values.length - 1) : 0;
  const points = values
    .map((v, i) => `${(i * stepX).toFixed(2)},${(height - (v / max) * height).toFixed(2)}`)
    .join(" ");
  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={ariaLabel}
      className="observability-sparkline"
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        points={points}
      />
    </svg>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/routes/observability/Sparkline.tsx
git commit -m "feat(frontend): add Sparkline component for Performance tab (#355)"
```

---

## Task 15: Frontend — `PerformanceTab` view

**Files:**
- Create: `frontend/src/routes/observability/PerformanceTab.tsx`
- Test: `frontend/src/routes/observability/__tests__/PerformanceTab.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/observability/__tests__/PerformanceTab.test.tsx`:

```tsx
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as observabilityModule from "../../../api/observability";
import { PerformanceTab } from "../PerformanceTab";

describe("PerformanceTab", () => {
  const getMetricsKnown = vi.spyOn(observabilityModule.observabilityApi, "getMetricsKnown");
  const getMetricsSummary = vi.spyOn(observabilityModule.observabilityApi, "getMetricsSummary");
  const getMetricsTrend = vi.spyOn(observabilityModule.observabilityApi, "getMetricsTrend");

  beforeEach(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    getMetricsKnown.mockResolvedValue([
      { module: "orchestrator", operation: "turn", last_recorded_at: "2026-05-20T00:00:00Z" },
    ]);
    getMetricsSummary.mockResolvedValue({
      count: 12,
      successes: 11,
      failures: 1,
      p50_ms: 100,
      p95_ms: 200,
      p99_ms: 250,
      max_ms: 300,
    });
    getMetricsTrend.mockResolvedValue([
      { bucket_start: "2026-05-20T00:00:00Z", count: 1, successes: 1, failures: 0, p50_ms: 100, p95_ms: 100, p99_ms: 100 },
    ]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders a row for each known (module, operation) pair", async () => {
    render(<PerformanceTab />);
    expect(await screen.findByText(/orchestrator/i)).toBeInTheDocument();
    expect(screen.getByText(/turn/i)).toBeInTheDocument();
  });

  it("displays summary counts and percentiles", async () => {
    render(<PerformanceTab />);
    await screen.findByText(/orchestrator/i);
    expect(await screen.findByText(/count\s*12/i)).toBeInTheDocument();
    expect(screen.getByText(/p50\s*100ms/i)).toBeInTheDocument();
    expect(screen.getByText(/p95\s*200ms/i)).toBeInTheDocument();
  });

  it("re-fetches summary and trend when the bucket selector changes", async () => {
    render(<PerformanceTab />);
    await screen.findByText(/orchestrator/i);
    getMetricsTrend.mockClear();
    const bucketSelect = screen.getByLabelText(/bucket/i);
    await act(async () => {
      await userEvent.selectOptions(bucketSelect, "hour");
    });
    expect(getMetricsTrend).toHaveBeenCalledWith("orchestrator", "turn", "hour", expect.any(Number));
  });

  it("shows an empty-state banner when /metrics/known errors", async () => {
    getMetricsKnown.mockRejectedValue(new Error("boom"));
    render(<PerformanceTab />);
    expect(await screen.findByText(/metrics unavailable/i)).toBeInTheDocument();
  });

  it("shows a failure count when summaries report failures", async () => {
    render(<PerformanceTab />);
    await screen.findByText(/orchestrator/i);
    expect(await screen.findByText(/1 failed/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm exec vitest run src/routes/observability/__tests__/PerformanceTab.test.tsx`
Expected: FAIL — file `../PerformanceTab` not found.

- [ ] **Step 3: Implement `PerformanceTab`**

Create `frontend/src/routes/observability/PerformanceTab.tsx`:

```tsx
import { useEffect, useState } from "react";

import {
  observabilityApi,
  type MetricsKnownPair,
  type MetricsSummary,
  type MetricsTrendBucket,
  type TrendBucketSize,
} from "../../api/observability";
import { Sparkline } from "./Sparkline";
import { useObservabilityPolling } from "./useObservabilityPolling";

const WINDOW_OPTIONS: { label: string; seconds: number }[] = [
  { label: "last 1h", seconds: 3600 },
  { label: "last 6h", seconds: 21600 },
  { label: "last 24h", seconds: 86400 },
];

const BUCKETS: TrendBucketSize[] = ["minute", "hour", "day"];

const POLL_INTERVAL_MS = 10_000;

type PairKey = string;

function keyOf(p: MetricsKnownPair): PairKey {
  return `${p.module}/${p.operation}`;
}

function formatMs(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${value.toFixed(0)}ms`;
}

export function PerformanceTab() {
  const [windowSeconds, setWindowSeconds] = useState(WINDOW_OPTIONS[0].seconds);
  const [bucket, setBucket] = useState<TrendBucketSize>("minute");
  const [pairs, setPairs] = useState<MetricsKnownPair[]>([]);
  const [summaries, setSummaries] = useState<Record<PairKey, MetricsSummary>>({});
  const [trends, setTrends] = useState<Record<PairKey, MetricsTrendBucket[]>>({});
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const known = await observabilityApi.getMetricsKnown();
      setPairs(known);
      setError(null);
      const summaryEntries = await Promise.all(
        known.map(async (p) => [
          keyOf(p),
          await observabilityApi.getMetricsSummary(p.module, p.operation, windowSeconds),
        ] as const),
      );
      const trendEntries = await Promise.all(
        known.map(async (p) => [
          keyOf(p),
          await observabilityApi.getMetricsTrend(p.module, p.operation, bucket, windowSeconds),
        ] as const),
      );
      setSummaries(Object.fromEntries(summaryEntries));
      setTrends(Object.fromEntries(trendEntries));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowSeconds, bucket]);

  useObservabilityPolling(refresh, POLL_INTERVAL_MS);

  if (error !== null) {
    return (
      <div className="observability-performance error" role="alert">
        Metrics unavailable: {error}
      </div>
    );
  }

  return (
    <div className="observability-performance">
      <header className="observability-controls">
        <label>
          Window:&nbsp;
          <select
            value={windowSeconds}
            onChange={(e) => setWindowSeconds(Number(e.target.value))}
          >
            {WINDOW_OPTIONS.map((opt) => (
              <option key={opt.seconds} value={opt.seconds}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Bucket:&nbsp;
          <select
            value={bucket}
            onChange={(e) => setBucket(e.target.value as TrendBucketSize)}
          >
            {BUCKETS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void refresh()}>
          Refresh
        </button>
      </header>

      {pairs.length === 0 ? (
        <p className="observability-empty">No metrics recorded yet.</p>
      ) : (
        <ul className="observability-pair-list">
          {pairs.map((p) => {
            const k = keyOf(p);
            const s = summaries[k];
            const t = trends[k] ?? [];
            return (
              <li key={k} className="observability-pair">
                <header>
                  <strong>{p.module}</strong> / {p.operation}
                </header>
                {s && (
                  <p className="observability-summary">
                    count {s.count}{" "}
                    {s.failures > 0 && (
                      <span className="failed">({s.failures} failed)</span>
                    )}{" "}
                    p50 {formatMs(s.p50_ms)} p95 {formatMs(s.p95_ms)} p99 {formatMs(s.p99_ms)}
                  </p>
                )}
                <Sparkline
                  values={t.map((b) => b.p95_ms)}
                  ariaLabel={`p95 trend for ${p.module}/${p.operation}`}
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && pnpm exec vitest run src/routes/observability/__tests__/PerformanceTab.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/observability/PerformanceTab.tsx frontend/src/routes/observability/__tests__/PerformanceTab.test.tsx
git commit -m "feat(frontend): add Performance tab view (#355)"
```

---

## Task 16: Frontend — `ObservabilityLayout`, route index, and `App.tsx` wiring

**Files:**
- Create: `frontend/src/routes/observability/ObservabilityLayout.tsx`
- Create: `frontend/src/routes/observability/index.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/shell/AppShell.tsx`

- [ ] **Step 1: Create the layout**

Create `frontend/src/routes/observability/ObservabilityLayout.tsx`:

```tsx
import { NavLink, Outlet } from "react-router-dom";

const tabs = [{ to: "performance", label: "Performance" }];

export function ObservabilityLayout() {
  return (
    <section className="route observability-view" aria-labelledby="observability-heading">
      <header className="observability-header">
        <h2 id="observability-heading">Observability</h2>
        <nav className="observability-tabs" aria-label="Observability sections">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                isActive ? "observability-tab active" : "observability-tab"
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <Outlet />
    </section>
  );
}
```

- [ ] **Step 2: Create the route index**

Create `frontend/src/routes/observability/index.tsx`:

```tsx
import { Navigate, Route, Routes } from "react-router-dom";

import { ObservabilityLayout } from "./ObservabilityLayout";
import { PerformanceTab } from "./PerformanceTab";

export function ObservabilityRoutes() {
  return (
    <Routes>
      <Route element={<ObservabilityLayout />}>
        <Route index element={<Navigate to="performance" replace />} />
        <Route path="performance" element={<PerformanceTab />} />
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 3: Mount the route in `App.tsx`**

Open `frontend/src/App.tsx`. Add the import near the other route imports:

```tsx
import { ObservabilityRoutes } from "./routes/observability";
```

Add the route inside the `<Routes>` block, after the campaigns block (so navigation appears in source order):

```tsx
<Route path="observability/*" element={<ObservabilityRoutes />} />
```

- [ ] **Step 4: Add nav link in `AppShell.tsx`**

Open `frontend/src/shell/AppShell.tsx`. Find the existing top-nav rendering — it'll be a list of `NavLink` entries for Home / Library / Campaigns. Add an `Observability` entry immediately after `Campaigns`:

```tsx
<NavLink to="/observability">Observability</NavLink>
```

Match the surrounding `className`/`aria-*` patterns. The Sparkline + Performance components don't need additional CSS rules to render; if `frontend/src/styles/` has the existing tab/header SASS, scope new selectors there to keep the navigation visually consistent. (Skip restyling if the page renders acceptably with default styles — the test suite does not check styling.)

- [ ] **Step 5: Run the full frontend test suite to confirm no regression**

Run: `cd frontend && pnpm exec vitest run`
Expected: All tests pass (Performance tab tests + pre-existing tests). If any pre-existing test breaks because of the new nav link, update the assertion in the relevant test to allow it.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/observability/ObservabilityLayout.tsx frontend/src/routes/observability/index.tsx frontend/src/App.tsx frontend/src/shell/AppShell.tsx
git commit -m "feat(frontend): mount /observability route with Performance tab (#355)"
```

---

## Task 17: Final integration verification

**Files:**
- None modified — verification only.

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && uv run pytest -x`
Expected: All tests pass.

- [ ] **Step 2: Run the full frontend test suite**

Run: `cd frontend && pnpm exec vitest run`
Expected: All tests pass.

- [ ] **Step 3: Boot the app and exercise the Performance tab manually**

In one shell: `scripts/run.sh --no-browser` (or the platform equivalent on Windows).
In a browser, navigate to `http://localhost:5173/observability/performance`. Confirm:
- The page renders with the empty-state banner ("No metrics recorded yet.") on a clean install.
- After running a turn (or any wired action), at least one row appears with a sparkline.
- Switching the Window and Bucket selectors triggers a re-fetch (network tab will show the request).
- Hiding the tab (switching browser tabs) stops polling; returning resumes it.

If any of the above fails, file the discrepancy as a follow-up task — do NOT mark the issue closed.

- [ ] **Step 4: Open the pull request**

```bash
gh pr create --title "Observability: performance metrics tab (#355)" --body "$(cat <<'EOF'
## Summary

Closes #355.

- Adds `MetricsRegistry.measure(...)` async context manager so producers can wrap their hot path in one line; an existing `record()` call still happens inside, sample-rate semantics unchanged.
- Wires `measure()` into all 8 modules called out by the issue: Orchestrator (`turn`), Context Builder (`build`), LLM Gateway (`complete`, `stream`), Extractor (`extract`), State Store (`_txn` writes + three high-traffic reads), Scene Manager (`scene_resolve`), Time Engine (`advance`), ImageGen (`generate`).
- Adds `GET /api/observability/metrics/trend?module&operation&bucket&window_seconds` returning a continuous bucketed series, and `GET /api/observability/metrics/known` returning every recorded `(module, operation)` pair.
- Adds a new top-level `/observability` route in the frontend with a Performance tab — summary stats + sparkline trend per pair, 10s visibility-gated polling.

Per the design spec, the Worlds-panel framing was reinterpreted as a top-level Observability route since metrics are global (per module/operation), not per-world.

## Test plan
- [ ] backend: `cd backend && uv run pytest` — green
- [ ] frontend: `cd frontend && pnpm exec vitest run` — green
- [ ] manual: boot app, run a turn, open `/observability/performance` and confirm the orchestrator/turn row populates with a sparkline
- [ ] manual: switch the Window/Bucket selectors and confirm re-fetch
- [ ] manual: hide the browser tab, confirm polling pauses; return, confirm it resumes

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Final commit / push**

Push the branch:

```bash
git push -u origin issue-355
```

Then mark this task complete. The PR URL printed by `gh pr create` is the deliverable.
