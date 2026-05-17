"""Tests for §3: Gateway health-monitor integration.

Covers:
- register_with_health_monitor is a no-op when health_monitor is None.
- After registration, the monitor has one HealthTarget per LLM + embedding
  provider with the correct ``kind`` value.
- First probe emits provider_health_changed with old_level=None.
- Second probe at same level emits nothing (idempotent).
- Level transition (HEALTHY → UNHEALTHY) emits an event with correct old/new
  level strings.
- Health transition does NOT mutate router state.
- event_bus=None: registration works; level tracking happens; no errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.observability.health import HealthMonitorService
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from tests.llm_gateway.conftest import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakePlugins,
)

# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "health_int.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def plugins() -> FakePlugins:
    return FakePlugins()


def _gw(
    plugins: FakePlugins,
    db: Database,
    *,
    health_monitor: HealthMonitorService | None = None,
    event_bus: EventBus | None = None,
) -> LLMGatewayService:
    from grimoire.llm_gateway.config import (
        EmbeddingCacheConfig,
        GatewayConfig,
        ObservabilityConfig,
        RetryConfig,
        TimeoutConfig,
    )

    config = GatewayConfig(
        default_routes={"main": "llm-a.model-x"},
        retry=RetryConfig(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutConfig(total_seconds=5.0, first_token_seconds=2.0),
        embedding_cache=EmbeddingCacheConfig(enabled=False, max_entries=100),
        observability=ObservabilityConfig(log_all_requests=False),
    )
    return LLMGatewayService(
        plugins,
        db,
        config,
        health_monitor=health_monitor,
        event_bus=event_bus,
    )


class EventCollector:
    """Collects all events from an EventBus."""

    def __init__(self, bus: EventBus) -> None:
        self._events: list[Event] = []
        bus.subscribe("*", self._on_event)

    async def _on_event(self, event: Event) -> None:
        self._events.append(event)

    def of_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.type == event_type]

    def clear(self) -> None:
        self._events.clear()


# --------------------------------------------------------------------------- #
# §3 tests
# --------------------------------------------------------------------------- #


async def test_no_op_when_health_monitor_is_none(plugins: FakePlugins, db: Database) -> None:
    """register_with_health_monitor is a no-op when health_monitor=None."""
    plugins.add_llm(FakeLLMProvider(id="llm-a"))
    gw = _gw(plugins, db)  # health_monitor defaults to None
    # Should not raise and should be a no-op.
    await gw.register_with_health_monitor()


async def test_targets_registered_for_all_providers(plugins: FakePlugins, db: Database) -> None:
    """After registration, one HealthTarget per LLM + embedding provider."""
    plugins.add_llm(FakeLLMProvider(id="llm-a"))
    plugins.add_llm(FakeLLMProvider(id="llm-b"))
    plugins.add_embedding(FakeEmbeddingProvider(id="emb-1"))

    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor)
    await gw.register_with_health_monitor()

    targets = {t.id: t for t in monitor.targets()}
    assert set(targets) == {"llm-a", "llm-b", "emb-1"}
    assert targets["llm-a"].kind == "llm_provider"
    assert targets["llm-b"].kind == "llm_provider"
    assert targets["emb-1"].kind == "embedding_provider"


async def test_first_probe_emits_provider_health_changed(
    plugins: FakePlugins, db: Database
) -> None:
    """First probe emits provider_health_changed with old_level=None."""
    plugins.add_llm(FakeLLMProvider(id="llm-a"))

    bus = EventBus()
    collector = EventCollector(bus)
    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor, event_bus=bus)
    await gw.register_with_health_monitor()

    await monitor.probe_all()

    events = collector.of_type("provider_health_changed")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["target_id"] == "llm-a"
    assert payload["kind"] == "llm_provider"
    assert payload["old_level"] is None
    assert payload["new_level"] == "healthy"
    assert "checked_at" in payload


async def test_second_probe_same_level_no_event(plugins: FakePlugins, db: Database) -> None:
    """Consecutive probe at the same level emits no additional event."""
    plugins.add_llm(FakeLLMProvider(id="llm-a"))

    bus = EventBus()
    collector = EventCollector(bus)
    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor, event_bus=bus)
    await gw.register_with_health_monitor()

    await monitor.probe_all()
    collector.clear()

    # Second probe at same level — should not emit another event.
    await monitor.probe_all()

    events = collector.of_type("provider_health_changed")
    assert events == []


async def test_level_transition_emits_event(plugins: FakePlugins, db: Database) -> None:
    """HEALTHY → UNHEALTHY transition emits an event with correct old/new levels."""

    @dataclass
    class FlippingProvider:
        id: str = "llm-flip"
        name: str = "flipping"
        _call_count: int = field(default=0, repr=False)

        async def health_check(self) -> HealthStatus:
            self._call_count += 1
            if self._call_count == 1:
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
            return HealthStatus(level=HealthLevel.UNHEALTHY, target_id=self.id, message="down")

    plugins.add_llm(FlippingProvider())

    bus = EventBus()
    collector = EventCollector(bus)
    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor, event_bus=bus)
    await gw.register_with_health_monitor()

    # First probe: HEALTHY (old_level=None)
    await monitor.probe_all()
    collector.clear()

    # Second probe: UNHEALTHY (level changed)
    await monitor.probe_all()

    events = collector.of_type("provider_health_changed")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["old_level"] == "healthy"
    assert payload["new_level"] == "unhealthy"
    assert payload["target_id"] == "llm-flip"


async def test_health_transition_does_not_change_router(plugins: FakePlugins, db: Database) -> None:
    """An UNHEALTHY probe must NOT mutate the gateway's route table."""

    @dataclass
    class SickProvider:
        id: str = "llm-sick"
        name: str = "sick"

        async def health_check(self) -> HealthStatus:
            return HealthStatus(level=HealthLevel.UNHEALTHY, target_id=self.id, message="down")

    plugins.add_llm(SickProvider())

    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor)
    await gw.register_with_health_monitor()

    routes_before = await gw.list_routes()
    await monitor.probe_all()
    routes_after = await gw.list_routes()

    assert routes_before == routes_after


async def test_event_bus_none_no_error(plugins: FakePlugins, db: Database) -> None:
    """With event_bus=None registration succeeds and probe_all does not raise."""
    plugins.add_llm(FakeLLMProvider(id="llm-a"))

    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor, event_bus=None)
    await gw.register_with_health_monitor()

    # Should not raise even though there's no bus.
    await monitor.probe_all()

    # Level tracking should still work — levels dict should be populated.
    assert "llm-a" in gw._provider_health_levels
    assert gw._provider_health_levels["llm-a"] == HealthLevel.HEALTHY


async def test_multiple_providers_all_tracked(plugins: FakePlugins, db: Database) -> None:
    """All LLM and embedding providers get independent level tracking."""
    plugins.add_llm(FakeLLMProvider(id="llm-a"))
    plugins.add_llm(FakeLLMProvider(id="llm-b"))
    plugins.add_embedding(FakeEmbeddingProvider(id="emb-1"))

    bus = EventBus()
    collector = EventCollector(bus)
    monitor = HealthMonitorService(db)
    gw = _gw(plugins, db, health_monitor=monitor, event_bus=bus)
    await gw.register_with_health_monitor()

    await monitor.probe_all()

    events = collector.of_type("provider_health_changed")
    target_ids = {e.payload["target_id"] for e in events}
    assert target_ids == {"llm-a", "llm-b", "emb-1"}
    # All first probes should have old_level=None.
    for event in events:
        assert event.payload["old_level"] is None
