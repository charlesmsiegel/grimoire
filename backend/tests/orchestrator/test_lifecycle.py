"""Tests for orchestrator cancellation, timeout, and heartbeat (§2/§3/§4)."""

from __future__ import annotations

import asyncio

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator import (
    OrchestratorConfig,
    OrchestratorService,
    TurnTimeoutError,
)
from grimoire.orchestrator.config import HeartbeatConfig
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneInit

from .conftest import (
    FakeContextBuilder,
    FakeExtractor,
    FakeGateway,
    FakeStateStore,
    WSCollector,
)


def _build_orch(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector | None,
    config: OrchestratorConfig,
) -> OrchestratorService:
    return OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        ws_push=ws,
        config=config,
    )


async def _seed(scene_manager, fake_store):
    fake_store.db.campaigns.add("c1")
    fake_store.db.pcs["c1"] = {"alistair"}
    return await scene_manager.start_scene(
        SceneInit(
            campaign_id="c1",
            title="Opening",
            present_pc_refs=["alistair"],
            present_character_refs=["alistair"],
        )
    )


async def test_cancel_turn_during_stream_aborts(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    scene = await _seed(scene_manager, fake_store)
    fake_gateway.chunks = ["Once ", "upon ", "a ", "time"]
    fake_gateway.chunk_delay = 0.02

    cancelled: list[Event] = []
    completed: list[Event] = []
    event_bus.subscribe("turn_cancelled", cancelled.append)
    event_bus.subscribe("turn_complete", completed.append)

    config = OrchestratorConfig(heartbeat=HeartbeatConfig(enabled=False))
    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
        ws,
        config,
    )

    submit_task = asyncio.create_task(orch.submit_post("c1", "alistair", "I bow."))
    # Give the turn time to start streaming.
    for _ in range(40):
        await asyncio.sleep(0.005)
        status = await orch.turn_in_progress("c1")
        if status is not None and status.stage == "streaming":
            break
    assert status is not None
    ok = await orch.cancel_turn("c1", status.turn_id)
    assert ok is True

    await submit_task

    assert len(cancelled) == 1
    assert completed == []
    # Player post was rolled back: scene has zero posts.
    posts = await scene_manager.get_posts(scene.id)
    assert posts == []
    # Extractor was never called (we cancelled before extract stage).
    assert fake_extractor.seen == []


async def test_turn_timeout_emits_turn_timed_out(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    scene = await _seed(scene_manager, fake_store)
    fake_gateway.chunks = ["a", "b", "c"]
    fake_gateway.chunk_delay = 0.5  # well past the timeout

    timed_out: list[Event] = []
    event_bus.subscribe("turn_timed_out", timed_out.append)

    config = OrchestratorConfig(
        turn_timeout_seconds=0.1,
        heartbeat=HeartbeatConfig(enabled=False),
    )
    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
        ws,
        config,
    )

    with pytest.raises(TurnTimeoutError):
        await orch.submit_post("c1", "alistair", "I bow.")

    assert len(timed_out) == 1
    posts = await scene_manager.get_posts(scene.id)
    assert posts == []


async def test_heartbeat_during_stream(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    await _seed(scene_manager, fake_store)
    fake_gateway.chunks = ["a", "b", "c", "d"]
    fake_gateway.chunk_delay = 0.04

    config = OrchestratorConfig(
        heartbeat=HeartbeatConfig(enabled=True, interval_seconds=0.02),
    )
    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
        ws,
        config,
    )

    await orch.submit_post("c1", "alistair", "I bow.")

    heartbeats = [m for _, m in ws.messages if m.get("type") == "heartbeat"]
    assert len(heartbeats) >= 1
    assert all("turn_id" in m for m in heartbeats)
