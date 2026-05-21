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
