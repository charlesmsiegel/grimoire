"""Tests for ``HealthMonitorService``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from grimoire.observability.health import HealthMonitorService
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.observability import HealthTarget


def _status(target_id: str, level: HealthLevel = HealthLevel.HEALTHY) -> HealthStatus:
    return HealthStatus(
        level=level,
        target_id=target_id,
        message="probed",
        checked_at=datetime.now(UTC).isoformat(),
    )


async def test_probe_writes_latest(db) -> None:
    monitor = HealthMonitorService(db)
    target = HealthTarget(id="prov-1", kind="llm_provider")

    async def probe() -> HealthStatus:
        return _status("prov-1")

    monitor.register(target, probe)
    status = await monitor.probe(target)
    assert status.level == HealthLevel.HEALTHY
    assert monitor.latest()["prov-1"].level == HealthLevel.HEALTHY


async def test_probe_records_exception_as_unhealthy(db) -> None:
    monitor = HealthMonitorService(db)
    target = HealthTarget(id="prov-bad", kind="llm_provider")

    async def probe() -> HealthStatus:
        raise RuntimeError("connection refused")

    monitor.register(target, probe)
    status = await monitor.probe(target)
    assert status.level == HealthLevel.UNHEALTHY
    assert "connection refused" in status.message


async def test_unregistered_target_returns_unconfigured(db) -> None:
    monitor = HealthMonitorService(db)
    status = await monitor.probe(HealthTarget(id="ghost", kind="llm_provider"))
    assert status.level == HealthLevel.UNCONFIGURED


async def test_probe_all_runs_every_target(db) -> None:
    monitor = HealthMonitorService(db)
    for i in range(3):
        target = HealthTarget(id=f"t-{i}", kind="llm_provider")

        async def probe(_id=target.id) -> HealthStatus:
            return _status(_id)

        monitor.register(target, probe)

    results = await monitor.probe_all()
    assert {r.target_id for r in results} == {"t-0", "t-1", "t-2"}
    assert all(r.level == HealthLevel.HEALTHY for r in results)


async def test_subscriber_notified_on_probe(db) -> None:
    monitor = HealthMonitorService(db)
    target = HealthTarget(id="prov-1", kind="llm_provider")

    async def probe() -> HealthStatus:
        return _status("prov-1")

    monitor.register(target, probe)

    notified: list[HealthStatus] = []

    async def handler(status: HealthStatus) -> None:
        notified.append(status)

    sub_id = monitor.subscribe(handler)
    await monitor.probe(target)
    assert len(notified) == 1
    monitor.unsubscribe(sub_id)
    await monitor.probe(target)
    assert len(notified) == 1  # unsubscribed


async def test_register_probeable_uses_health_check(db) -> None:
    monitor = HealthMonitorService(db)
    target = HealthTarget(id="prov-1", kind="llm_provider")

    class FakeProvider:
        id = "prov-1"

        async def health_check(self) -> HealthStatus:
            return _status("prov-1", HealthLevel.DEGRADED)

    monitor.register_probeable(target, FakeProvider())
    status = await monitor.probe(target)
    assert status.level == HealthLevel.DEGRADED


async def test_load_latest_restores_in_memory_view(db) -> None:
    monitor = HealthMonitorService(db)
    target = HealthTarget(id="prov-x", kind="llm_provider")
    monitor.register(target, lambda: _async_status("prov-x"))
    await monitor.probe(target)
    monitor2 = HealthMonitorService(db)
    await monitor2.load_latest()
    assert monitor2.latest()["prov-x"].level == HealthLevel.HEALTHY


async def _async_status(target_id: str) -> HealthStatus:
    await asyncio.sleep(0)
    return _status(target_id)
