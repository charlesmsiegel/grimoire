"""Tests for §5: pre-roll confirmation round-trip in the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator import OrchestratorConfig, OrchestratorService
from grimoire.orchestrator.config import PreRollConfig
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneInit
from grimoire.types.mechanics import (
    ProposalResolution,
    ProposedRoll,
    Roll,
    RollResult,
)

from .conftest import (
    FakeContextBuilder,
    FakeExtractor,
    FakeGateway,
    FakeStateStore,
    WSCollector,
)


@dataclass
class StubMechanics:
    """Stubbed Mechanics façade returning preset proposals on evaluate_pre_roll."""

    proposals: list[ProposedRoll] = field(default_factory=list)
    resolve_calls: list[Roll] = field(default_factory=list)

    async def evaluate_pre_roll(
        self,
        campaign_id: str,
        player_input: str,
        scene: Any,
    ) -> list[ProposedRoll]:
        return list(self.proposals)

    async def resolve_roll(
        self,
        campaign_id: str,
        roll: Roll,
    ) -> RollResult:
        self.resolve_calls.append(roll)
        return RollResult(
            roll_id=roll.id,
            dice=[6, 6],
            successes=2,
            outcome="success",
        )


async def _setup(
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
        )
    )
    return scene.id


async def test_always_mode_pauses_turn_and_emits_event(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
) -> None:
    await _setup(scene_manager, fake_store)
    mechanics = StubMechanics(
        proposals=[
            ProposedRoll(label="climb", kind="dice-pool", pool=4),
        ]
    )
    received_events: list[Event] = []
    event_bus.subscribe("pre_roll_pending", lambda e: received_events.append(e))

    config = OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="always"))
    orch = OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        mechanics=mechanics,
        ws_push=ws,
        config=config,
    )

    result = await orch.submit_post("c1", "alistair", "I climb the tower")
    assert result.turn_id is not None
    # Allow async event dispatch to settle.
    import asyncio

    await asyncio.sleep(0)
    assert any(e.type == "pre_roll_pending" for e in received_events)

    # No model response was generated yet — the FakeGateway was never asked.
    assert fake_gateway.seen_requests == []
    # The orchestrator is in pre_roll_pending state.
    status = await orch.turn_in_progress("c1")
    assert status is not None
    assert status.stage == "pre_roll_pending"


async def test_resolve_pre_roll_resumes_and_completes(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
) -> None:
    await _setup(scene_manager, fake_store)
    mechanics = StubMechanics(
        proposals=[
            ProposedRoll(label="climb", kind="dice-pool", pool=4),
            ProposedRoll(label="balance", kind="dice-pool", pool=3),
        ]
    )

    config = OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="always"))
    orch = OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        mechanics=mechanics,
        ws_push=ws,
        config=config,
    )

    result = await orch.submit_post("c1", "alistair", "I climb")
    turn_id = result.turn_id
    assert turn_id

    # Accept climb (with a pool override), decline balance.
    await orch.resolve_pre_roll(
        "c1",
        turn_id,
        [
            ProposalResolution(label="climb", accepted=True, modifications={"pool": 5}),
            ProposalResolution(label="balance", accepted=False),
        ],
    )

    # Only one roll was resolved; the override survived.
    assert len(mechanics.resolve_calls) == 1
    assert mechanics.resolve_calls[0].pool == 5

    # The context builder ran and the gateway streamed a response.
    assert fake_context_builder.calls
    assert fake_gateway.seen_requests
    # Active turn is cleared.
    assert await orch.turn_in_progress("c1") is None


async def test_high_stakes_mode_only_pauses_flagged_proposals(
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
) -> None:
    await _setup(scene_manager, fake_store)
    mechanics = StubMechanics(
        proposals=[
            ProposedRoll(label="quick", kind="dice-pool", pool=2, high_stakes=False),
            ProposedRoll(label="deadly", kind="dice-pool", pool=8, high_stakes=True),
        ]
    )

    config = OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="high_stakes"))
    orch = OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        mechanics=mechanics,
        ws_push=ws,
        config=config,
    )

    result = await orch.submit_post("c1", "alistair", "do something")
    # Inline (quick) is already resolved before the pause.
    assert len(mechanics.resolve_calls) == 1
    assert mechanics.resolve_calls[0].id.endswith("quick")
    # Now resume by accepting the deadly proposal.
    await orch.resolve_pre_roll(
        "c1",
        result.turn_id or "",
        [ProposalResolution(label="deadly", accepted=True)],
    )
    assert len(mechanics.resolve_calls) == 2
