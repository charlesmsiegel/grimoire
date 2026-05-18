"""§1 Orchestrator fan-out: ImageGen subscribes to turn_complete and
calls queue_generation based on the per-campaign TriggerConfig."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.imagegen import TriggerConfig
from grimoire.imagegen.integration import ImageGenIntegration


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


async def test_fires_queue_generation_on_turn_complete_per_post(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="per_post")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(
            Event(
                type="turn_complete",
                payload={"turn_id": "t1", "campaign_id": "camp-1", "scene_id": "scene-1"},
            )
        )
    finally:
        integ.stop()
    svc.queue_generation.assert_awaited_once_with(
        campaign_id="camp-1", scene_id="scene-1", post_id=None
    )


async def test_manual_only_mode_does_not_queue(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="manual_only")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(
            Event(
                type="turn_complete",
                payload={"campaign_id": "camp-1", "scene_id": "scene-1"},
            )
        )
    finally:
        integ.stop()
    svc.queue_generation.assert_not_awaited()


async def test_scene_started_sets_on_scene_open_flag(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(
        mode="per_scene", on_scene_open=True
    )
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(
            Event(
                type="scene_started",
                payload={"campaign_id": "camp-1", "scene_id": "scene-2"},
            )
        )
        await bus.emit(
            Event(
                type="turn_complete",
                payload={
                    "campaign_id": "camp-1",
                    "scene_id": "scene-2",
                    "turn_id": "t9",
                },
            )
        )
    finally:
        integ.stop()
    svc.queue_generation.assert_awaited_once()


async def test_turn_complete_without_scene_open_flag_skips(bus) -> None:
    svc = AsyncMock()
    # per_scene with on_scene_open=True but no scene_started latch — should NOT queue
    svc.get_trigger_config.return_value = TriggerConfig(
        mode="per_scene", on_scene_open=True
    )
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(
            Event(
                type="turn_complete",
                payload={"campaign_id": "camp-1", "scene_id": "scene-1"},
            )
        )
    finally:
        integ.stop()
    svc.queue_generation.assert_not_awaited()


async def test_queue_generation_errors_swallowed(bus, caplog) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="per_post")
    svc.queue_generation.side_effect = KeyError("no backend")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    try:
        await bus.emit(
            Event(
                type="turn_complete",
                payload={"campaign_id": "camp-1", "scene_id": "scene-1"},
            )
        )
    finally:
        integ.stop()
    assert any("imagegen fan-out failed queuing job" in r.message for r in caplog.records)


async def test_stop_unsubscribes_handlers(bus) -> None:
    svc = AsyncMock()
    svc.get_trigger_config.return_value = TriggerConfig(mode="per_post")
    integ = ImageGenIntegration(svc, bus)
    integ.start()
    integ.stop()
    await bus.emit(
        Event(
            type="turn_complete",
            payload={"campaign_id": "camp-1", "scene_id": "scene-1"},
        )
    )
    svc.queue_generation.assert_not_awaited()
