"""Smoke tests for the ``ObservabilityService`` facade."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.observability.service import ObservabilityService
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import CompletionRequest, LLMCallRecord
from grimoire.types.observability import (
    ErrorRecord,
    HealthTarget,
    LogEvent,
    LogLevel,
    LogQuery,
    ReplayOptions,
    TurnAudit,
)


class _Gateway:
    async def complete(
        self, task: str, request: CompletionRequest, campaign_id: str | None = None
    ) -> Any:
        class _R:
            text = "ok"

        return _R()


async def test_facade_records_and_lists_audits(db) -> None:
    service = ObservabilityService(db=db)
    await service.record_turn_audit(
        TurnAudit(
            turn_id="t",
            campaign_id="c",
            started_at=datetime.now(UTC),
            response_text="hi",
        )
    )
    audit = await service.get_turn_audit("t")
    assert audit.response_text == "hi"
    assert (await service.list_turn_audits("c"))[0].turn_id == "t"


async def test_facade_exposes_costs_metrics_and_health(db) -> None:
    service = ObservabilityService(db=db)
    await service.costs().record(
        LLMCallRecord(
            id="r",
            task="primary",
            provider_id="p",
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.5,
            latency_ms=10,
            finish_reason="stop",
            campaign_id="c",
        )
    )
    total = await service.costs().total(campaign_id="c")
    assert total.total_usd == 0.5

    await service.metrics().record(
        module="orchestrator", operation="turn", duration_ms=12.0, force=True
    )
    summary = await service.metrics().summary("orchestrator", "turn", window_seconds=600)
    assert summary["count"] == 1.0

    service.health().register(
        HealthTarget(id="prov", kind="llm_provider"),
        _healthy,
    )
    await service.health().probe(HealthTarget(id="prov", kind="llm_provider"))
    assert service.health().latest()["prov"].level == HealthLevel.HEALTHY


async def test_facade_log_and_error_apis(db) -> None:
    service = ObservabilityService(db=db)
    await service.log(
        LogEvent(
            timestamp=datetime.now(UTC),
            level=LogLevel.INFO,
            module="ext",
            operation="run",
            payload={"k": "v"},
        )
    )
    events = await service.query_log(LogQuery(modules=["ext"]))
    assert len(events) == 1
    await service.record_error(
        ErrorRecord(
            timestamp=datetime.now(UTC),
            module="ext",
            operation="run",
            error_kind="boom",
            message="kaboom",
        )
    )
    recent = await service.recent_errors()
    assert recent and recent[0].error_kind == "boom"


async def test_replay_requires_gateway(db) -> None:
    service = ObservabilityService(db=db)
    try:
        await service.replay_turn("t", ReplayOptions())
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


async def test_start_subscribes_auditor_when_event_bus_passed(db) -> None:
    bus = EventBus()
    service = ObservabilityService(db=db, event_bus=bus, llm_gateway=_Gateway())
    await service.start()
    try:
        await bus.emit(
            Event(
                type="turn_started",
                payload={
                    "turn_id": "t_e",
                    "campaign_id": "c_e",
                    "scene_id": "s_e",
                },
            )
        )
        await bus.emit(
            Event(
                type="turn_complete",
                payload={"turn_id": "t_e", "campaign_id": "c_e", "scene_id": "s_e"},
            )
        )
        assert await service.audit_store.get("t_e") is not None
    finally:
        await service.shutdown()


async def test_record_error_fires_error_reported_event(db) -> None:
    """§16: plugin hook for external error reporters."""
    bus = EventBus()
    service = ObservabilityService(db=db, event_bus=bus)
    seen: list[Event] = []
    bus.subscribe("error_reported", lambda e: seen.append(e) or None)
    await service.record_error(
        ErrorRecord(
            timestamp=datetime.now(UTC),
            module="orchestrator",
            operation="turn",
            error_kind="boom",
            message="splat",
            user_visible=True,
        )
    )
    assert len(seen) == 1
    assert seen[0].payload["module"] == "orchestrator"
    assert seen[0].payload["error_kind"] == "boom"


async def test_llm_response_received_records_cost(db) -> None:
    """The service's start() subscriber should drop a cost_records row
    for each llm_response_received event."""
    bus = EventBus()
    service = ObservabilityService(db=db, event_bus=bus)
    await service.start()
    try:
        await bus.emit(
            Event(
                type="llm_response_received",
                payload={
                    "task": "main",
                    "provider": "anthropic",
                    "model": "claude-3",
                    "campaign_id": "c1",
                    "turn_id": "t1",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "cost_estimate_usd": 0.05,
                    "latency_ms": 100,
                    "finish_reason": "stop",
                },
            )
        )
        total = await service.costs_tracker.total(campaign_id="c1")
        assert total.total_usd == 0.05
    finally:
        await service.shutdown()


async def _healthy() -> HealthStatus:
    return HealthStatus(
        level=HealthLevel.HEALTHY,
        target_id="prov",
        checked_at=datetime.now(UTC).isoformat(),
    )


async def test_start_republishes_health_probes_as_bus_events(db) -> None:
    """§12: Frontend Health panel needs each probe result to flow onto the
    event bus so ``StreamManager`` can fan it out to live UIs."""
    bus = EventBus()
    service = ObservabilityService(db=db, event_bus=bus)
    seen: list[Event] = []
    bus.subscribe("health_status_changed", lambda e: seen.append(e) or None)
    await service.start()
    try:
        service.health().register(HealthTarget(id="prov", kind="llm_provider"), _healthy)
        await service.health().probe(HealthTarget(id="prov", kind="llm_provider"))
        assert len(seen) == 1
        payload = seen[0].payload
        assert payload["target_id"] == "prov"
        assert payload["level"] == HealthLevel.HEALTHY.value
        assert "checked_at" in payload
    finally:
        await service.shutdown()


async def test_shutdown_unsubscribes_health_handler(db) -> None:
    """Shutdown must drop the bus-bridge handler so a re-started service
    doesn't double-emit on the next probe."""
    bus = EventBus()
    service = ObservabilityService(db=db, event_bus=bus)
    seen: list[Event] = []
    bus.subscribe("health_status_changed", lambda e: seen.append(e) or None)
    await service.start()
    service.health().register(HealthTarget(id="prov", kind="llm_provider"), _healthy)
    await service.health().probe(HealthTarget(id="prov", kind="llm_provider"))
    assert len(seen) == 1
    await service.shutdown()
    # Probe again — the unsubscribed bridge must not republish.
    await service.health().probe(HealthTarget(id="prov", kind="llm_provider"))
    assert len(seen) == 1
