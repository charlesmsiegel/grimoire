"""Tests for :class:`OrchestratorService` — the turn loop driver."""

from __future__ import annotations

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator import (
    NoTurnsToUndoError,
    OrchestratorConfig,
    OrchestratorService,
    UnknownCampaignError,
    UnknownPCError,
)
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

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_orch(
    *,
    scene_manager: SceneManager,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector | None = None,
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
        config=config,
    )


async def _seed(
    scene_manager: SceneManager,
    fake_store: FakeStateStore,
    *,
    campaign_id: str = "c1",
    pc_ref: str = "alistair",
    extra_pcs: tuple[str, ...] = (),
):
    fake_store.db.campaigns.add(campaign_id)
    fake_store.db.pcs[campaign_id] = {pc_ref, *extra_pcs}
    scene = await scene_manager.start_scene(
        SceneInit(
            campaign_id=campaign_id,
            title="Opening",
            present_pc_refs=[pc_ref, *extra_pcs],
            present_character_refs=[pc_ref, *extra_pcs],
        )
    )
    return scene


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


async def test_submit_post_unknown_campaign_raises(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    with pytest.raises(UnknownCampaignError):
        await orch.submit_post("missing", "alistair", "hi")


async def test_submit_post_unknown_pc_raises(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    fake_store.db.campaigns.add("c1")
    fake_store.db.pcs["c1"] = {"someone-else"}
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    with pytest.raises(UnknownPCError):
        await orch.submit_post("c1", "alistair", "hi")


# --------------------------------------------------------------------------- #
# Single-PC turn flow
# --------------------------------------------------------------------------- #


async def test_single_pc_submit_runs_full_turn(
    scene_manager,
    event_bus,
    fake_store,
    fake_gateway,
    fake_extractor,
    fake_context_builder,
    ws,
):
    scene = await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-1",
            target_table="facts",
            after={"text": "they met at dusk"},
            confidence=0.9,
            source="extractor",
        ),
        StateDelta(
            kind=DeltaKind.OTHER,
            target_scope="campaign-sqlite",
            target_id="fact-2",
            target_table="facts",
            after={"text": "uncertain"},
            confidence=0.65,
            source="extractor",
        ),
    ]
    fake_gateway.chunks = ["She ", "nods ", "slowly."]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )

    # Capture lifecycle events for assertions.
    seen: list[Event] = []
    event_bus.subscribe("turn_started", seen.append)
    event_bus.subscribe("turn_complete", seen.append)
    event_bus.subscribe("deltas_extracted", seen.append)
    event_bus.subscribe("context_built", seen.append)
    event_bus.subscribe("review_item_added", seen.append)

    result = await orch.submit_post("c1", "alistair", "I bow.")
    assert result.accepted
    assert result.auto_responding is True
    assert result.turn_id is not None

    # Posts: 1 = player, 2 = model response
    posts = await scene_manager.get_posts(scene.id)
    assert len(posts) == 2
    assert posts[0].body == "I bow."
    assert posts[1].body == "She nods slowly."
    assert posts[1].turn_id == result.turn_id

    # High-confidence delta auto-applied; low-confidence queued.
    assert len(fake_store.applied) == 1
    assert len(fake_store.reviewed) == 1
    assert fake_store.applied[0]["turn_id"] == result.turn_id

    types = [e.type for e in seen]
    assert types[0] == "turn_started"
    assert "context_built" in types
    assert "deltas_extracted" in types
    assert types[-1] == "turn_complete"
    # Review-item event fired.
    assert "review_item_added" in types

    # WebSocket received token chunks and lifecycle messages.
    token_msgs = [m for _, m in ws.messages if m.get("type") == "token"]
    assert [m["delta"] for m in token_msgs] == ["She ", "nods ", "slowly."]
    types_ws = {m["type"] for _, m in ws.messages}
    assert {"turn_started", "context_built", "turn_complete"}.issubset(types_ws)


async def test_context_builder_receives_player_input_and_pc(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    await orch.submit_post("c1", "alistair", "I draw my sword.")
    assert len(fake_context_builder.calls) == 1
    call = fake_context_builder.calls[0]
    assert call["player_input"] == "I draw my sword."
    assert call["campaign_id"] == "c1"
    assert call["pc_ref"] == "alistair"


async def test_extract_mode_threaded_to_context_builder_and_extractor(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    from grimoire.extractor.config import ExtractorConfig
    from grimoire.types.extraction_modes import ExtractionMode

    await _seed(scene_manager, fake_store)
    # Campaign prefers TOGETHER; the orchestrator should pick that mode and
    # thread it to both the context builder and the extractor.
    orch = OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        extractor_config=ExtractorConfig(mode=ExtractionMode.TOGETHER),
    )
    await orch.submit_post("c1", "alistair", "I draw my sword.")
    assert fake_context_builder.calls[0]["extractor_mode"] == ExtractionMode.TOGETHER
    assert fake_extractor.seen[0]["mode"] == ExtractionMode.TOGETHER


# --------------------------------------------------------------------------- #
# Multi-PC advance flow
# --------------------------------------------------------------------------- #


async def test_multi_pc_submit_waits_for_advance(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store, extra_pcs=("brigid",))
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    result = await orch.submit_post("c1", "alistair", "I wait.")
    assert result.accepted is True
    assert result.auto_responding is False
    assert result.turn_id is None
    # Player post appended, but no LLM call made yet.
    posts = await scene_manager.get_posts(scene.id)
    assert len(posts) == 1
    assert fake_gateway.seen_tasks == []
    assert fake_context_builder.calls == []


async def test_advance_runs_turn_for_multi_pc(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store, extra_pcs=("brigid",))
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    await orch.submit_post("c1", "alistair", "I draw.")
    await orch.submit_post("c1", "brigid", "I cover the door.")
    adv = await orch.advance("c1", scene.id)
    assert adv.turn_id is not None
    # Context Builder was called exactly once, with both inputs in the prompt.
    assert len(fake_context_builder.calls) == 1
    text = fake_context_builder.calls[0]["player_input"]
    assert "I draw." in text
    assert "I cover the door." in text


# --------------------------------------------------------------------------- #
# Status / queue inspection
# --------------------------------------------------------------------------- #


async def test_queue_length_zero_when_idle(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    assert await orch.queue_length("c1") == 0
    assert await orch.turn_in_progress("c1") is None


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #


async def test_undo_reverses_deltas_for_last_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-x",
            target_table="facts",
            after={"text": "the door is open"},
            confidence=0.95,
            source="extractor",
        )
    ]
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    submit = await orch.submit_post("c1", "alistair", "I open the door.")
    assert submit.turn_id is not None
    assert len(fake_store.applied) == 1

    undo = await orch.undo_turn("c1", count=1)
    assert undo.turns_undone == [submit.turn_id]
    assert len(undo.reversed_delta_ids) == 1
    assert fake_store.reversed_ids == undo.reversed_delta_ids


async def test_undo_no_turns_raises(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    with pytest.raises(NoTurnsToUndoError):
        await orch.undo_turn("c1")


# --------------------------------------------------------------------------- #
# Regenerate
# --------------------------------------------------------------------------- #


async def test_regenerate_without_prior_turn_returns_not_accepted(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    await _seed(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    res = await orch.regenerate_last("c1")
    assert res.accepted is False


async def test_regenerate_replays_last_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [
        StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope="campaign-sqlite",
            target_id="fact-r",
            target_table="facts",
            after={"text": "the wind howls"},
            confidence=0.95,
            source="extractor",
        )
    ]
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    first = await orch.submit_post("c1", "alistair", "I knock.")

    # The mock gateway needs more chunks queued — set a fresh response.
    fake_gateway.chunks = ["She ", "knocks ", "back."]

    regen = await orch.regenerate_last("c1")
    assert regen.accepted
    assert regen.turn_id != first.turn_id
    # Player post remains; previous response post deleted; new response appended.
    posts = await scene_manager.get_posts(scene.id)
    assert posts[0].body == "I knock."
    assert posts[-1].body == "She knocks back."


# --------------------------------------------------------------------------- #
# PC-absent scene direction
# --------------------------------------------------------------------------- #


async def _seed_pc_absent(
    scene_manager: SceneManager,
    fake_store: FakeStateStore,
    *,
    campaign_id: str = "c1",
):
    fake_store.db.campaigns.add(campaign_id)
    fake_store.db.pcs[campaign_id] = set()
    scene = await scene_manager.start_scene(
        SceneInit(
            campaign_id=campaign_id,
            title="NPC Meeting",
            present_pc_refs=[],
            present_character_refs=["npc-winifred", "npc-drake"],
        )
    )
    return scene


async def test_submit_direction_runs_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    result = await orch.submit_direction("c1", scene.id, text="winifred confronts Drake")
    assert result.accepted is True
    assert result.turn_id is not None
    assert result.auto_responding is True
    assert fake_context_builder.calls[0]["player_input"] == "winifred confronts Drake"


async def test_submit_direction_continue_with_empty_text(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    result = await orch.submit_direction("c1", scene.id)
    assert result.accepted is True
    assert result.turn_id is not None
    assert fake_context_builder.calls[0]["player_input"] == ""


async def test_submit_direction_rejects_pc_present_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    from grimoire.orchestrator.errors import OrchestratorError

    with pytest.raises(OrchestratorError, match="not a PC-absent scene"):
        await orch.submit_direction("c1", scene.id, text="Direct something")


async def test_submit_direction_rejects_cross_campaign_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store, campaign_id="c1")
    fake_store.db.campaigns.add("c2")
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    from grimoire.orchestrator.errors import OrchestratorError

    with pytest.raises(OrchestratorError, match="does not belong to campaign"):
        await orch.submit_direction("c2", scene.id, text="Sneak in")


async def test_submit_direction_rejects_non_active_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    older = await _seed_pc_absent(scene_manager, fake_store)
    # Start a newer PC-absent scene; it becomes the active scene.
    await scene_manager.start_scene(
        SceneInit(
            campaign_id="c1",
            title="Second NPC Meeting",
            present_pc_refs=[],
            present_character_refs=["npc-zara"],
        )
    )
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    from grimoire.orchestrator.errors import OrchestratorError

    with pytest.raises(OrchestratorError, match="not the active scene"):
        await orch.submit_direction("c1", older.id, text="Old scene direction")


async def test_submit_direction_empty_text_records_marker_post(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    await orch.submit_direction("c1", scene.id)
    posts = await scene_manager.get_posts(scene.id)
    # First post is the empty-body direction marker; second is the narrator response.
    direction_posts = [p for p in posts if p.is_player]
    assert len(direction_posts) == 1
    assert direction_posts[0].body == ""


async def test_submit_direction_rejects_closed_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store)
    await scene_manager.close_scene(scene.id, closed_at_turn="t_close")
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    from grimoire.orchestrator.errors import OrchestratorError

    with pytest.raises(OrchestratorError, match="closed"):
        await orch.submit_direction("c1", scene.id)
