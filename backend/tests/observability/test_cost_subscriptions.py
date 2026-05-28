"""Integration tests for ObservabilityService's cost-event subscriptions.

These exercise the cross-module wiring: an event is emitted on the bus and a
row appears in ``cost_records`` via the observability handler. Unit tests in
``test_costs.py`` cover ``CostTrackerService.record()`` in isolation; these
verify the full subscribe → handler → DB write path.
"""

from __future__ import annotations

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.observability.service import ObservabilityService


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def service(db, event_bus) -> ObservabilityService:
    svc = ObservabilityService(db=db, event_bus=event_bus)
    await svc.start()
    try:
        yield svc
    finally:
        await svc.shutdown()


async def test_llm_response_event_writes_cost_row(db, event_bus, service) -> None:
    await event_bus.emit(
        Event(
            type="llm_response_received",
            payload={
                "task": "primary",
                "provider": "anthropic",
                "model": "claude-opus-4-7",
                "campaign_id": "c1",
                "turn_id": "t1",
                "usage": {"input_tokens": 800, "output_tokens": 350},
                "cost_estimate_usd": 0.0125,
                "latency_ms": 200,
                "finish_reason": "stop",
            },
        )
    )
    rows = await db.fetchall(
        "SELECT task, model, cost_usd, input_tokens, output_tokens FROM cost_records"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["task"] == "primary"
    assert row["model"] == "claude-opus-4-7"
    assert row["cost_usd"] == pytest.approx(0.0125)
    assert row["input_tokens"] == 800
    assert row["output_tokens"] == 350


async def test_embedding_response_event_writes_cost_row(db, event_bus, service) -> None:
    """End-to-end: gateway-style embedding event → observability handler → DB row."""
    await event_bus.emit(
        Event(
            type="embedding_response_received",
            payload={
                "task": "embedding",
                "provider": "openai",
                "model": "text-embedding-3-small",
                "campaign_id": "c1",
                "turn_id": "t_embed",
                "usage": {"input_tokens": 420, "output_tokens": 0, "total_tokens": 420},
                "cost_estimate_usd": 0.0000084,
                "latency_ms": 50,
                "finish_reason": "complete",
            },
        )
    )
    rows = await db.fetchall(
        "SELECT task, model, cost_usd, input_tokens FROM cost_records WHERE turn_id = ?",
        ("t_embed",),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["task"] == "embedding"
    assert row["model"] == "text-embedding-3-small"
    assert row["cost_usd"] == pytest.approx(0.0000084)
    assert row["input_tokens"] == 420


async def test_image_ready_event_writes_cost_row(db, event_bus, service) -> None:
    """End-to-end: imagegen IMAGE_READY event → observability handler → DB row."""
    await event_bus.emit(
        Event(
            type="image_ready",
            payload={
                "image_id": "img_1",
                "campaign_id": "c1",
                "cached": False,
                "cost_usd": 0.04,
                "model": "dall-e-3",
                "backend": "openai",
            },
        )
    )
    rows = await db.fetchall(
        "SELECT task, model, cost_usd FROM cost_records WHERE task = 'imagegen'"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "dall-e-3"
    assert row["cost_usd"] == pytest.approx(0.04)


async def test_image_ready_cached_event_skips_cost_row(db, event_bus, service) -> None:
    """Cached image generations have zero incremental cost and must not write a row."""
    await event_bus.emit(
        Event(
            type="image_ready",
            payload={
                "image_id": "img_cached",
                "campaign_id": "c1",
                "cached": True,
                "cost_usd": 0.0,
                "model": "",
                "backend": "",
            },
        )
    )
    rows = await db.fetchall("SELECT id FROM cost_records WHERE task = 'imagegen'")
    assert rows == []
