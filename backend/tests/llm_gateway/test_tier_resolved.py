"""Verify the gateway emits tier_resolved on every completion."""

from __future__ import annotations

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.llm_gateway.tiers import Tier


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def bus(events: list[Event]) -> EventBus:
    b = EventBus()

    async def _collect(event: Event) -> None:
        events.append(event)

    b.subscribe("tier_resolved", _collect)
    return b


async def test_emit_tier_resolved_reports_source(db, plugins, bus, events, tmp_path) -> None:
    from grimoire.llm_gateway.config import GatewayConfig
    from grimoire.types.llm import RetryPolicy, TimeoutPolicy

    cfg = GatewayConfig(
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
    )
    gw = LLMGatewayService(plugins, db, cfg, data_root=tmp_path, event_bus=bus)
    gw._router.set_route("main", "fake.model")
    gw._router.set_tier_route("camp-1", Tier.HEAVY, "tier.model")

    await gw._emit_tier_resolved("main", "camp-1")

    assert len(events) == 1
    p = events[0].payload
    assert p["task"] == "main"
    assert p["tier"] == "heavy"
    assert p["source"] == "tier"
    assert p["route"] == "tier.model"
    assert p["campaign_id"] == "camp-1"


async def test_emit_tier_resolved_default_source(db, plugins, bus, events, tmp_path) -> None:
    from grimoire.llm_gateway.config import GatewayConfig
    from grimoire.types.llm import RetryPolicy, TimeoutPolicy

    cfg = GatewayConfig(
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
    )
    gw = LLMGatewayService(plugins, db, cfg, data_root=tmp_path, event_bus=bus)
    gw._router.set_route("main", "default.model")

    await gw._emit_tier_resolved("main", "camp-1")

    assert len(events) == 1
    p = events[0].payload
    assert p["source"] == "default"
    assert p["route"] == "default.model"


async def test_emit_tier_resolved_no_bus_no_crash(db, plugins, tmp_path) -> None:
    from grimoire.llm_gateway.config import GatewayConfig
    from grimoire.types.llm import RetryPolicy, TimeoutPolicy

    cfg = GatewayConfig(
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
    )
    gw = LLMGatewayService(plugins, db, cfg, data_root=tmp_path)
    gw._router.set_route("main", "fake.model")
    # Should not raise even without an event bus
    await gw._emit_tier_resolved("main", None)
