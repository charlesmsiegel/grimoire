"""Tests for ``MetricsRegistry`` (rolling-window + sampling)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry


async def test_records_a_sample(db) -> None:
    registry = MetricsRegistry(
        db,
        config=MetricsConfig(sample_rate_hot_path=1.0, sample_rate_cold_path=1.0),
    )
    await registry.record(module="orchestrator", operation="turn", duration_ms=42.0)
    summary = await registry.summary("orchestrator", "turn")
    assert summary["count"] == 1.0
    assert summary["p50_ms"] == 42.0


async def test_summary_returns_percentiles(db) -> None:
    registry = MetricsRegistry(db, config=MetricsConfig(sample_rate_hot_path=1.0))
    for v in (10, 20, 30, 40, 100):
        await registry.record(module="extractor", operation="extract", duration_ms=float(v))
    summary = await registry.summary("extractor", "extract", window_seconds=3600)
    assert summary["count"] == 5.0
    # p50 = 30, p95 = 88 (interp), p99 = 97.6
    assert summary["p50_ms"] == 30.0
    assert 80.0 < summary["p95_ms"] <= 100.0
    assert summary["max_ms"] == 100.0


async def test_hot_path_sampling_drops_calls(db) -> None:
    # Force the rng to a deterministic stream so we know which records land
    rng = random.Random(0)
    registry = MetricsRegistry(
        db,
        config=MetricsConfig(sample_rate_hot_path=0.0, sample_rate_cold_path=1.0),
        rng=rng,
    )
    for _ in range(5):
        await registry.record(module="orchestrator", operation="turn", duration_ms=1.0)
    summary = await registry.summary("orchestrator", "turn", window_seconds=3600)
    assert summary["count"] == 0.0

    for _ in range(5):
        await registry.record(module="export", operation="render", duration_ms=2.0)
    cold = await registry.summary("export", "render", window_seconds=3600)
    assert cold["count"] == 5.0


async def test_force_overrides_sampling(db) -> None:
    registry = MetricsRegistry(
        db,
        config=MetricsConfig(sample_rate_hot_path=0.0, sample_rate_cold_path=0.0),
    )
    await registry.record(module="orchestrator", operation="turn", duration_ms=5.0, force=True)
    summary = await registry.summary("orchestrator", "turn", window_seconds=3600)
    assert summary["count"] == 1.0


async def test_summary_excludes_samples_outside_window(db) -> None:
    registry = MetricsRegistry(
        db, config=MetricsConfig(sample_rate_hot_path=1.0, sample_rate_cold_path=1.0)
    )
    now = datetime.now(UTC)
    await registry.record(
        module="time_engine",
        operation="tick",
        duration_ms=10.0,
        timestamp=now - timedelta(hours=2),
    )
    await registry.record(
        module="time_engine",
        operation="tick",
        duration_ms=20.0,
        timestamp=now,
    )
    summary = await registry.summary("time_engine", "tick", window_seconds=600)
    assert summary["count"] == 1.0
    assert summary["p50_ms"] == 20.0


async def test_failure_label_tracked_in_summary(db) -> None:
    registry = MetricsRegistry(
        db, config=MetricsConfig(sample_rate_cold_path=1.0, sample_rate_hot_path=1.0)
    )
    await registry.record(module="llm_gateway", operation="complete", duration_ms=1, success=True)
    await registry.record(module="llm_gateway", operation="complete", duration_ms=2, success=False)
    summary = await registry.summary("llm_gateway", "complete", window_seconds=3600)
    assert summary["successes"] == 1.0
    assert summary["failures"] == 1.0
