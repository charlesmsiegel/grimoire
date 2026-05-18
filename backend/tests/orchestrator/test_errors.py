"""Tests for orchestrator error / rollback story (§6/§7/§8/§9/§11)."""

from __future__ import annotations

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator import (
    OrchestratorConfig,
    OrchestratorError,
    OrchestratorService,
)
from grimoire.orchestrator.config import ErrorConfig, HeartbeatConfig
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneInit
from grimoire.types.state import DeltaKind, StateDelta

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
    ws: WSCollector | None = None,
    config: OrchestratorConfig | None = None,
) -> OrchestratorService:
    if config is None:
        config = OrchestratorConfig(heartbeat=HeartbeatConfig(enabled=False))
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


async def test_llm_failure_emits_turn_failed_and_rolls_back(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    fake_gateway.chunks = ["partial ", "text"]
    fake_gateway.fail_after = 1  # one chunk emitted, then boom

    failed: list[Event] = []
    event_bus.subscribe("turn_failed", failed.append)

    config = OrchestratorConfig(
        heartbeat=HeartbeatConfig(enabled=False),
        errors=ErrorConfig(surface_partial_response_on_llm_error=False),
    )
    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
        config=config,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "I bow.")

    assert len(failed) == 1
    payload = failed[0].payload
    assert payload["reason"] == "llm_gateway"
    assert payload["partial_response"] == "partial "
    # Player post was rolled back.
    posts = await scene_manager.get_posts(scene.id)
    assert posts == []


async def test_delta_apply_failure_rolls_back_batch(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-1",
            target_table="facts",
            after={"text": "one"},
            confidence=0.95,
            source="extractor",
        ),
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-2",
            target_table="facts",
            after={"text": "two"},
            confidence=0.95,
            source="extractor",
        ),
    ]
    fake_store.fail_apply_on_call = 1  # succeed on first, fail on second

    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "I bow.")

    # First delta was applied, then reversed during batch rollback.
    assert len(fake_store.applied) == 1
    assert fake_store.reversed_ids == ["d_0000"]


async def test_extractor_parse_failure_retries(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-r",
            target_table="facts",
            after={"text": "ok"},
            confidence=0.95,
            source="extractor",
        )
    ]
    # First call: emit a parse-failure flag; second call: clean.
    fake_extractor.scripted_flag_codes = ["llm_json_unparseable", None]

    config = OrchestratorConfig(
        heartbeat=HeartbeatConfig(enabled=False),
        errors=ErrorConfig(retry_extractor_on_parse_failure=1),
    )
    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
        config=config,
    )

    await orch.submit_post("c1", "alistair", "I bow.")

    assert len(fake_extractor.seen) == 2
    # The successful retry's delta was applied.
    assert len(fake_store.applied) == 1


async def test_retcon_flags_downstream_turns(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)

    # Turn A produces a fact at target_id=fact-shared.
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-shared",
            target_table="facts",
            after={"text": "the door is open"},
            confidence=0.95,
            source="extractor",
        )
    ]
    orch = _build_orch(
        scene_manager,
        event_bus,
        fake_store,
        fake_gateway,
        fake_extractor,
        fake_context_builder,
    )
    first = await orch.submit_post("c1", "alistair", "I open the door.")
    assert first.turn_id is not None

    # Turn B also touches fact-shared.
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-shared",
            target_table="facts",
            after={"text": "the door is also broken"},
            confidence=0.95,
            source="extractor",
        )
    ]
    fake_gateway.chunks = ["X"]
    second = await orch.submit_post("c1", "alistair", "I kick it.")
    assert second.turn_id is not None

    posts = await scene_manager.get_posts(scene.id)
    # The player post of turn A is the first post in the scene.
    turn_a_player_post = posts[0]
    assert turn_a_player_post.is_player

    # Retcon turn A's narrator post (which has the turn_id we want to undo).
    turn_a_narrator = next(p for p in posts if not p.is_player and p.turn_id == first.turn_id)
    result = await orch.retcon_post(turn_a_narrator.id, "She refuses the door.")

    assert second.turn_id in result.downstream_flagged_turns
