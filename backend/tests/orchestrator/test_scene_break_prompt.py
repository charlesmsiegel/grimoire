"""Tests for §5: medium-confidence scene-break interactive prompt.

Spec source: ``docs/superpowers/specs/2026-05-18-orchestrator-COMPLETED.md`` §5.
The orchestrator wraps ``_maybe_break_scene`` in a wait for the user's choice
when the boundary detector returns a confidence in the
``[prompt_threshold, auto_threshold)`` band. The frontend resolves the prompt
via :meth:`OrchestratorService.resolve_scene_break`; a timeout defaults to
``"continue"``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator import OrchestratorConfig, OrchestratorService
from grimoire.orchestrator.config import SceneBreakConfig
from grimoire.orchestrator.service import _ActiveTurn
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneBreakDecision, SceneInit

from .conftest import (
    FakeContextBuilder,
    FakeExtractor,
    FakeGateway,
    FakeStateStore,
    WSCollector,
)


async def _setup_scene(
    scene_manager: SceneManager,
    fake_store: FakeStateStore,
) -> str:
    fake_store.db.campaigns.add("c1")
    fake_store.db.pcs["c1"] = {"alistair"}
    scene = await scene_manager.start_scene(
        SceneInit(
            campaign_id="c1",
            title="Opening",
            present_pc_refs=["alistair"],
            present_character_refs=["alistair"],
            location_ref="library:worlds/wod/locations/camden",
        )
    )
    return scene.id


def _build_orchestrator(
    *,
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
    config: OrchestratorConfig | None = None,
) -> OrchestratorService:
    return OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        ws_push=ws,
        config=config or OrchestratorConfig(),
    )


def _make_active_turn(
    campaign_id: str = "c1",
    scene_id: str = "s1",
    turn_id: str = "t1",
) -> _ActiveTurn:
    return _ActiveTurn(
        turn_id=turn_id,  # type: ignore[arg-type]
        campaign_id=campaign_id,  # type: ignore[arg-type]
        scene_id=scene_id,  # type: ignore[arg-type]
        started_at=datetime.now(UTC),
    )


async def test_low_confidence_does_not_emit_or_wait(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_id = await _setup_scene(scene_manager, fake_store)
    orch = _build_orchestrator(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )

    async def low_confidence(*args: Any, **kwargs: Any) -> SceneBreakDecision:
        return SceneBreakDecision(is_break=False, confidence=0.2, reason="none")

    monkeypatch.setattr(scene_manager, "is_scene_break", low_confidence)

    received: list[Event] = []
    event_bus.subscribe("scene_break_suggested", lambda e: received.append(e))

    active = _make_active_turn(scene_id=scene_id)
    out = await orch._maybe_break_scene(
        campaign_id="c1",  # type: ignore[arg-type]
        scene_id=scene_id,  # type: ignore[arg-type]
        player_input="hello",
        triggering_pc="alistair",  # type: ignore[arg-type]
        turn_id=active.turn_id,
        active=active,
    )
    await asyncio.sleep(0)

    assert out == scene_id
    assert received == []
    assert active.scene_break_choice is None


async def test_medium_confidence_emits_and_continue_keeps_scene(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_id = await _setup_scene(scene_manager, fake_store)
    orch = _build_orchestrator(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )

    async def medium(*args: Any, **kwargs: Any) -> SceneBreakDecision:
        return SceneBreakDecision(
            is_break=True,
            confidence=0.65,
            reason="tonal_shift",
            proposed_new_scene=SceneInit(
                campaign_id="c1",
                title="Aftermath",
                present_pc_refs=["alistair"],
                present_character_refs=["alistair"],
            ),
        )

    monkeypatch.setattr(scene_manager, "is_scene_break", medium)

    received: list[Event] = []
    event_bus.subscribe("scene_break_suggested", lambda e: received.append(e))

    active = _make_active_turn(scene_id=scene_id)
    orch._campaigns.setdefault(active.campaign_id, _new_state(active))

    task = asyncio.create_task(
        orch._maybe_break_scene(
            campaign_id="c1",  # type: ignore[arg-type]
            scene_id=scene_id,  # type: ignore[arg-type]
            player_input="suddenly everything goes quiet",
            triggering_pc="alistair",  # type: ignore[arg-type]
            turn_id=active.turn_id,
            active=active,
        )
    )

    # Allow the orchestrator to attach the future and emit the event.
    for _ in range(50):
        await asyncio.sleep(0)
        if active.scene_break_choice is not None:
            break

    assert active.scene_break_choice is not None
    await asyncio.sleep(0)
    assert any(e.type == "scene_break_suggested" for e in received)
    payload = next(e for e in received if e.type == "scene_break_suggested").payload
    assert payload["scene_id"] == scene_id
    assert payload["turn_id"] == active.turn_id
    assert payload["confidence"] == pytest.approx(0.65)
    assert payload["reason"] == "tonal_shift"

    # Resolve as "continue".
    resolved = await orch.resolve_scene_break("c1", active.turn_id, "continue")  # type: ignore[arg-type]
    assert resolved is True

    new_scene_id = await task
    assert new_scene_id == scene_id  # unchanged
    assert active.scene_break_choice is None  # cleared after wait


async def test_medium_confidence_new_scene_opens_new_one(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_id = await _setup_scene(scene_manager, fake_store)
    orch = _build_orchestrator(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )

    proposed = SceneInit(
        campaign_id="c1",
        title="The chase",
        present_pc_refs=["alistair"],
        present_character_refs=["alistair"],
    )

    async def medium(*args: Any, **kwargs: Any) -> SceneBreakDecision:
        return SceneBreakDecision(
            is_break=True,
            confidence=0.6,
            reason="location_change",
            proposed_new_scene=proposed,
        )

    monkeypatch.setattr(scene_manager, "is_scene_break", medium)

    active = _make_active_turn(scene_id=scene_id)
    orch._campaigns.setdefault(active.campaign_id, _new_state(active))

    task = asyncio.create_task(
        orch._maybe_break_scene(
            campaign_id="c1",  # type: ignore[arg-type]
            scene_id=scene_id,  # type: ignore[arg-type]
            player_input="we leg it",
            triggering_pc="alistair",  # type: ignore[arg-type]
            turn_id=active.turn_id,
            active=active,
        )
    )

    for _ in range(50):
        await asyncio.sleep(0)
        if active.scene_break_choice is not None:
            break

    assert await orch.resolve_scene_break("c1", active.turn_id, "new_scene") is True  # type: ignore[arg-type]

    new_scene_id = await task
    assert new_scene_id != scene_id
    # The proposed scene was created.
    fresh = await scene_manager.get_scene(new_scene_id)
    assert fresh.campaign_id == "c1"
    # The old scene is closed.
    old = await scene_manager.get_scene(scene_id)
    assert old.closed is True


async def test_medium_confidence_timeout_defaults_to_continue(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_id = await _setup_scene(scene_manager, fake_store)
    orch = _build_orchestrator(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        config=OrchestratorConfig(scene_break=SceneBreakConfig(prompt_resume_timeout_seconds=0.05)),
    )

    async def medium(*args: Any, **kwargs: Any) -> SceneBreakDecision:
        return SceneBreakDecision(
            is_break=True,
            confidence=0.6,
            reason="tonal_shift",
            proposed_new_scene=None,
        )

    monkeypatch.setattr(scene_manager, "is_scene_break", medium)

    active = _make_active_turn(scene_id=scene_id)
    orch._campaigns.setdefault(active.campaign_id, _new_state(active))

    out = await orch._maybe_break_scene(
        campaign_id="c1",  # type: ignore[arg-type]
        scene_id=scene_id,  # type: ignore[arg-type]
        player_input="something portentous",
        triggering_pc="alistair",  # type: ignore[arg-type]
        turn_id=active.turn_id,
        active=active,
    )
    assert out == scene_id
    assert active.scene_break_choice is None


async def test_resolve_scene_break_returns_false_without_pending(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
) -> None:
    orch = _build_orchestrator(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )
    # No active turn at all.
    assert await orch.resolve_scene_break("c1", "t1", "continue") is False  # type: ignore[arg-type]

    # Active turn but no scene-break prompt outstanding.
    active = _make_active_turn(turn_id="t1")
    orch._campaigns.setdefault(active.campaign_id, _new_state(active))
    assert await orch.resolve_scene_break("c1", "t1", "continue") is False  # type: ignore[arg-type]

    # Active turn but a different turn_id is being resolved.
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    active.scene_break_choice = fut
    assert await orch.resolve_scene_break("c1", "t999", "continue") is False  # type: ignore[arg-type]
    # Original future still pending.
    assert not fut.done()


def _new_state(active: _ActiveTurn) -> Any:
    """Build a ``_CampaignTurnState`` with ``active`` attached.

    Pulled into a helper to keep tests focused; the dataclass is private to
    the service module, so this lives next to the import.
    """
    from grimoire.orchestrator.service import _CampaignTurnState

    state = _CampaignTurnState()
    state.active = active
    return state
