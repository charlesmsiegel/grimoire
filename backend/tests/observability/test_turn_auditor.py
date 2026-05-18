"""Tests for ``TurnAuditor`` (event bus subscriber)."""

from __future__ import annotations

from grimoire.event_bus import Event, EventBus
from grimoire.observability.audit import AuditStore
from grimoire.observability.turn_auditor import TurnAuditor


async def test_assembles_audit_from_orchestrator_events(db) -> None:
    bus = EventBus()
    store = AuditStore(db)
    auditor = TurnAuditor(event_bus=bus, audit_store=store)
    auditor.start()

    await bus.emit(
        Event(
            type="turn_started",
            payload={
                "turn_id": "t_1",
                "campaign_id": "c_1",
                "scene_id": "s_1",
                "branch_id": "c_1:main",
                "player_input": "I shout at the door.",
            },
        )
    )
    await bus.emit(
        Event(
            type="context_built",
            payload={
                "turn_id": "t_1",
                "campaign_id": "c_1",
                "scene_id": "s_1",
                "budget_used": {"spotlight": 200, "background": 100},
                "messages_hash": "h-1",
            },
        )
    )
    await bus.emit(
        Event(
            type="model_response_received",
            payload={
                "turn_id": "t_1",
                "campaign_id": "c_1",
                "scene_id": "s_1",
                "llm_provider": "anthropic",
                "llm_model": "claude-3-haiku",
                "llm_prompt_tokens": 100,
                "llm_completion_tokens": 50,
                "llm_cost_usd": 0.001,
                "llm_latency_ms": 250,
                "llm_finish_reason": "end_turn",
                "llm_retries": 0,
                "response_text": "The door rattles.",
            },
        )
    )
    await bus.emit(
        Event(
            type="turn_complete",
            payload={
                "turn_id": "t_1",
                "campaign_id": "c_1",
                "scene_id": "s_1",
            },
        )
    )

    audit = await store.get("t_1")
    assert audit is not None
    assert audit.player_input == "I shout at the door."
    assert audit.response_text == "The door rattles."
    assert audit.llm_provider == "anthropic"
    assert audit.llm_cost_usd == 0.001
    assert audit.context_messages_hash == "h-1"
    assert audit.duration_ms is not None and audit.duration_ms >= 0


async def test_can_be_disabled_via_config(db) -> None:
    from grimoire.observability.config import AuditConfig

    bus = EventBus()
    store = AuditStore(db)
    auditor = TurnAuditor(
        event_bus=bus,
        audit_store=store,
        config=AuditConfig(enabled=False),
    )
    auditor.start()

    await bus.emit(Event(type="turn_started", payload={"turn_id": "t", "campaign_id": "c"}))
    await bus.emit(Event(type="turn_complete", payload={"turn_id": "t", "campaign_id": "c"}))
    assert await store.get("t") is None


async def test_turn_audit_fragment_event_merges_extra_fields(db) -> None:
    bus = EventBus()
    store = AuditStore(db)
    auditor = TurnAuditor(event_bus=bus, audit_store=store)
    auditor.start()

    await bus.emit(
        Event(
            type="turn_started",
            payload={
                "turn_id": "t_2",
                "campaign_id": "c_1",
                "scene_id": "s_1",
                "branch_id": "c_1:main",
            },
        )
    )
    await bus.emit(
        Event(
            type="turn_audit_fragment",
            payload={
                "turn_id": "t_2",
                "options": {"replay_of": "t_1"},
            },
        )
    )
    await bus.emit(
        Event(
            type="turn_complete",
            payload={"turn_id": "t_2", "campaign_id": "c_1", "scene_id": "s_1"},
        )
    )
    audit = await store.get("t_2")
    assert audit is not None
    assert audit.options.get("replay_of") == "t_1"


async def test_llm_response_received_event_merged_with_translated_keys(db) -> None:
    """Gateway emits ``llm_response_received`` with unprefixed keys; the
    auditor should translate them into the ``llm_*`` audit fields."""
    bus = EventBus()
    store = AuditStore(db)
    auditor = TurnAuditor(event_bus=bus, audit_store=store)
    auditor.start()

    await bus.emit(
        Event(
            type="turn_started",
            payload={
                "turn_id": "t_llm",
                "campaign_id": "c_1",
                "scene_id": "s_1",
                "branch_id": "c_1:main",
            },
        )
    )
    await bus.emit(
        Event(
            type="llm_response_received",
            payload={
                "turn_id": "t_llm",
                "campaign_id": "c_1",
                "provider": "anthropic",
                "model": "claude-3-haiku",
                "usage": {"input_tokens": 120, "output_tokens": 60},
                "cost_estimate_usd": 0.0042,
                "latency_ms": 510,
                "retries": 1,
                "finish_reason": "end_turn",
                "params": {"temperature": 0.7, "max_tokens": 2048, "seed": 99},
            },
        )
    )
    await bus.emit(
        Event(
            type="turn_complete",
            payload={"turn_id": "t_llm", "campaign_id": "c_1", "scene_id": "s_1"},
        )
    )
    audit = await store.get("t_llm")
    assert audit is not None
    assert audit.llm_provider == "anthropic"
    assert audit.llm_model == "claude-3-haiku"
    assert audit.llm_prompt_tokens == 120
    assert audit.llm_completion_tokens == 60
    assert audit.llm_cost_usd == 0.0042
    assert audit.llm_latency_ms == 510
    assert audit.llm_retries == 1
    assert audit.llm_finish_reason == "end_turn"
    assert audit.llm_params == {"temperature": 0.7, "max_tokens": 2048, "seed": 99}


async def test_assembled_messages_round_trip(db) -> None:
    """`context_built` event carries the verbatim message list; the
    auditor stores it on `audit.assembled_messages`."""
    bus = EventBus()
    store = AuditStore(db)
    auditor = TurnAuditor(event_bus=bus, audit_store=store)
    auditor.start()

    messages_payload = [
        {"role": "system", "content": "you are a narrator", "metadata": {}, "name": None},
        {"role": "user", "content": "I open the door.", "metadata": {}, "name": None},
    ]
    await bus.emit(
        Event(
            type="turn_started",
            payload={"turn_id": "t_msgs", "campaign_id": "c_1", "scene_id": "s_1"},
        )
    )
    await bus.emit(
        Event(
            type="context_built",
            payload={
                "turn_id": "t_msgs",
                "campaign_id": "c_1",
                "scene_id": "s_1",
                "budget_used": {"spotlight": 50},
                "messages_hash": "hh",
                "assembled_messages": messages_payload,
            },
        )
    )
    await bus.emit(
        Event(
            type="turn_complete",
            payload={"turn_id": "t_msgs", "campaign_id": "c_1", "scene_id": "s_1"},
        )
    )
    audit = await store.get("t_msgs")
    assert audit is not None
    assert audit.assembled_messages == messages_payload


async def test_stop_unsubscribes_handlers(db) -> None:
    bus = EventBus()
    store = AuditStore(db)
    auditor = TurnAuditor(event_bus=bus, audit_store=store)
    auditor.start()
    assert bus.subscriber_count() > 0
    auditor.stop()
    assert bus.subscriber_count() == 0
