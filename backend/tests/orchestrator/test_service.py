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
from grimoire.orchestrator.errors import SceneClosedError, TurnAlreadyInProgressError
from grimoire.scenes import AuthorKind, new_post
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
    continuity: object | None = None,
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
        continuity=continuity,
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


# --------------------------------------------------------------------------- #
# Cascade delete
# --------------------------------------------------------------------------- #


def _seed_applied(fake_store, *, campaign_id, turn_id, target_id):
    """Record a fake applied delta for ``turn_id`` so the cascade can reverse it."""
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope="campaign-sqlite",
        target_id=target_id,
        target_table="facts",
        after={"text": target_id},
        confidence=0.95,
        source="extractor",
    )
    did = f"d_seed_{target_id}"
    fake_store.applied.append(
        {
            "id": did,
            "delta": delta,
            "source": "extractor",
            "turn_id": turn_id,
            "campaign_id": campaign_id,
        }
    )
    return did


async def test_cascade_delete_reverses_fully_contained_turns(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m2", is_player=False, turn_id="T2"),
    )
    d1 = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-1")
    d2 = _seed_applied(fake_store, campaign_id="c1", turn_id="T2", target_id="fact-2")

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    posts = await scene_manager.get_posts(scene.id)
    target = next(p for p in posts if p.body == "m1")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    remaining = await scene_manager.get_posts(scene.id)
    assert [p.body for p in remaining] == ["hi"]
    assert set(fake_store.reversed_ids) == {d1, d2}
    assert set(result.reversed_turn_ids) == {"T1", "T2"}
    assert result.requeued_review_ids == []
    assert len(result.deleted_post_ids) == 2


async def test_cascade_delete_requeues_straddling_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    # Split turn T1 produces two model posts; T2 a third.
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m2", is_player=False, turn_id="T1"),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m3", is_player=False, turn_id="T2"),
    )
    d1 = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-1")
    d2 = _seed_applied(fake_store, campaign_id="c1", turn_id="T2", target_id="fact-2")

    review_events: list = []
    event_bus.subscribe("review_item_added", review_events.append)

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    posts = await scene_manager.get_posts(scene.id)
    target = next(p for p in posts if p.body == "m2")  # mid-split -> T1 straddles
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    remaining = await scene_manager.get_posts(scene.id)
    assert [p.body for p in remaining] == ["hi", "m1"]
    # Both turns reversed; only the straddling turn (T1) is re-queued.
    assert set(fake_store.reversed_ids) == {d1, d2}
    assert len(fake_store.reviewed) == 1
    assert fake_store.reviewed[0]["source"] == "cascade_delete"
    assert len(result.requeued_review_ids) == 1
    assert len(review_events) == 1


async def test_cascade_delete_rejects_closed_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    appended = (await scene_manager.get_posts(scene.id))[0]
    await scene_manager.close_scene(scene.id, closed_at_turn="T1")

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    with pytest.raises(SceneClosedError):
        await orch.delete_post_cascade("c1", scene.id, appended.id)


async def test_cascade_delete_rejected_during_active_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    from types import SimpleNamespace

    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    appended = (await scene_manager.get_posts(scene.id))[0]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    # Simulate a turn streaming into this scene.
    orch._state_for("c1").active = SimpleNamespace(turn_id="T1", scene_id=scene.id)

    with pytest.raises(TurnAlreadyInProgressError):
        await orch.delete_post_cascade("c1", scene.id, appended.id)
    # Nothing was truncated.
    assert len(await scene_manager.get_posts(scene.id)) == 1


async def test_cascade_delete_surfaces_failed_reversals(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-1")

    async def _boom(delta_id: str) -> None:
        raise RuntimeError("cannot reverse")

    fake_store.reverse_delta = _boom  # type: ignore[assignment]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    posts = await scene_manager.get_posts(scene.id)
    target = next(p for p in posts if p.body == "m1")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    # The API no longer reports a clean success: the un-reversed delta is surfaced.
    assert any("could not be reversed" in w for w in result.warnings)
    assert result.reversed_turn_ids == []
    # The prose is still truncated (the user's explicit intent).
    assert [p.body for p in await scene_manager.get_posts(scene.id)] == ["hi"]


async def test_cascade_delete_skips_unapplied_review_deltas(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    applied = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-1")
    # A second T1 delta that is queued for review but was never applied.
    queued = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-pending")
    fake_store.pending_delta_ids.add(queued)

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    posts = await scene_manager.get_posts(scene.id)
    target = next(p for p in posts if p.body == "m1")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    # The applied delta is reversed; the unapplied review delta is left alone.
    assert applied in fake_store.reversed_ids
    assert queued not in fake_store.reversed_ids
    assert result.warnings == []


async def test_cascade_delete_dismisses_pending_cast_changes_for_removed_turns(
    tmp_path, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    from grimoire.scenes.cast_changes import CastChangeStore
    from grimoire.scenes.manager import SceneManagerConfig
    from grimoire.storage.db import Database
    from grimoire.testing.db_template import stamp_migrated_db
    from grimoire.types.scene import CastChange

    db = Database(stamp_migrated_db(tmp_path / "cc.sqlite"))
    await db.connect()
    try:
        scene_manager = SceneManager(
            tmp_path,
            config=SceneManagerConfig(running_summary_every_n_posts=0),
            event_bus=event_bus,
            cast_change_store=CastChangeStore(db),
        )
        scene = await _seed(scene_manager, fake_store)
        await scene_manager.append_post(
            scene.id,
            new_post(
                author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True
            ),
        )
        await scene_manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
        )
        # The deleted turn proposed an NPC entering the scene.
        await scene_manager.queue_cast_change(
            scene.id,
            character_ref="library:worlds/w/characters/reyes",
            change=CastChange.ENTER,
            is_pc=False,
            evidence="strides in",
            confidence=0.8,
            turn_id="T1",
        )
        assert len(await scene_manager.list_pending_cast_changes(scene.id)) == 1

        orch = _build_orch(
            scene_manager=scene_manager,
            event_bus=event_bus,
            fake_store=fake_store,
            fake_gateway=fake_gateway,
            fake_extractor=fake_extractor,
            fake_context_builder=fake_context_builder,
        )
        target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m1")
        await orch.delete_post_cascade("c1", scene.id, target.id)

        # The stale prompt for the deleted turn is dismissed.
        assert await scene_manager.list_pending_cast_changes(scene.id) == []
    finally:
        await db.close()


async def test_cascade_delete_rejected_when_turn_queued(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """A turn can be queued (player post appended) before _run_turn_inner sets
    state.active; deleting its prompt while it waits would orphan the response.
    The delete must reject on a queued turn too, not just an active one."""
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    appended = (await scene_manager.get_posts(scene.id))[0]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    # No active turn, but one is queued waiting on the campaign lock.
    state = orch._state_for("c1")
    state.active = None
    state.queued = 1

    with pytest.raises(TurnAlreadyInProgressError):
        await orch.delete_post_cascade("c1", scene.id, appended.id)
    # Nothing was truncated.
    assert len(await scene_manager.get_posts(scene.id)) == 1


async def test_cascade_delete_rejected_while_submitting(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """submit_post bumps state.submitting and appends the player post before the
    turn is queued/active. A delete in that window must reject too, else it could
    truncate away a post a turn is about to consume."""
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    appended = (await scene_manager.get_posts(scene.id))[0]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    # A submission is in flight: post appended, turn not yet queued/active.
    state = orch._state_for("c1")
    state.submitting = 1

    with pytest.raises(TurnAlreadyInProgressError):
        await orch.delete_post_cascade("c1", scene.id, appended.id)
    # Nothing was truncated.
    assert len(await scene_manager.get_posts(scene.id)) == 1


async def test_cascade_delete_warns_on_straddling_continuity(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """A straddling turn keeps its continuity writes (a post survives), but those
    writes aren't post-attributed, so the delete surfaces a warning rather than
    silently trusting state whose evidence may have been removed."""
    from grimoire.continuity import ContinuityService, InGameTime
    from tests.continuity.conftest import make_fact

    continuity = ContinuityService()
    # T1 established a continuity fact and is about to straddle the cut.
    await continuity.add_fact(make_fact(text="from T1", post="T1"), source="extractor")

    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    # Split turn T1 produces two model posts; T2 a third.
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m2", is_player=False, turn_id="T1"),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m3", is_player=False, turn_id="T2"),
    )

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        continuity=continuity,
    )
    target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m2")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    assert any("T1 straddles the deletion" in w for w in result.warnings)
    # The straddling fact is *not* retracted — a post (m1) still survives.
    active = await continuity.recent_facts(InGameTime(day_count=0), limit=50)
    assert "from T1" in {f.text for f in active}


async def _seed_split_straddle(scene_manager, fake_store):
    """Scene where deleting m2 makes split turn T1 straddle and T2 fully removed."""
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    for body, tid in (("m1", "T1"), ("m2", "T1"), ("m3", "T2")):
        await scene_manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=body, is_player=False, turn_id=tid),
        )
    return scene


async def test_cascade_delete_requeues_straddling_deltas_in_apply_order(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """Straddling deltas reverse LIFO but must re-queue in original apply order
    so approving the review items replays A→B→C (not the reversed C→B→A)."""
    scene = await _seed_split_straddle(scene_manager, fake_store)
    for target in ("f0", "f1", "f2"):
        _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id=target)
    _seed_applied(fake_store, campaign_id="c1", turn_id="T2", target_id="g0")

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m2")
    await orch.delete_post_cascade("c1", scene.id, target.id)

    # Only the straddling turn T1 re-queues, and in apply order f0, f1, f2.
    assert [r["delta"].target_id for r in fake_store.reviewed] == ["f0", "f1", "f2"]


async def test_cascade_delete_warns_when_straddling_requeue_fails(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """If queue_for_review fails after a straddling delta is already reversed,
    the delete surfaces a warning (state removed without a re-approval prompt)."""
    scene = await _seed_split_straddle(scene_manager, fake_store)
    _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="f0")

    async def _boom(**kwargs):
        raise RuntimeError("review queue down")

    fake_store.queue_for_review = _boom  # type: ignore[assignment]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m2")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)
    assert any("could not be re-queued" in w for w in result.warnings)


async def test_cascade_delete_emits_review_item_in_client_shape(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """The requeued-delta event carries item.{id,summary} so the frontend's
    push-review handler shows it live (not just review_id/turn_id)."""
    scene = await _seed_split_straddle(scene_manager, fake_store)
    _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="f0")

    seen: list = []
    event_bus.subscribe("review_item_added", seen.append)

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m2")
    await orch.delete_post_cascade("c1", scene.id, target.id)

    assert seen
    item = seen[0].payload["item"]
    assert isinstance(item["id"], str) and item["id"]
    assert isinstance(item["summary"], str) and item["summary"]


async def test_cascade_delete_warns_on_unreverted_fact_update(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """A FACT_UPDATE has no pre-image, so retract_turn leaves it applied; the
    delete warns the edit was not reverted."""
    from grimoire.continuity import ContinuityService
    from tests.continuity.conftest import make_fact

    continuity = ContinuityService()
    fid = await continuity.add_fact(make_fact(text="orig", post="T0"), source="extractor")
    await continuity.update_fact(fid, {"text": "edited in T1"}, in_post="T1")

    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        continuity=continuity,
    )
    target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m1")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)
    assert any("FACT_UPDATE" in w for w in result.warnings)


async def test_cascade_delete_warns_on_confirmed_cast_changes_for_removed_turns(
    tmp_path, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """A cast change the user already confirmed mutated the cast outside the
    delta log; deleting its turn can't reverse it, so the delete warns."""
    from grimoire.scenes.cast_changes import CastChangeStore
    from grimoire.scenes.manager import SceneManagerConfig
    from grimoire.storage.db import Database
    from grimoire.testing.db_template import stamp_migrated_db
    from grimoire.types.scene import CastChange

    db = Database(stamp_migrated_db(tmp_path / "cc.sqlite"))
    await db.connect()
    try:
        scene_manager = SceneManager(
            tmp_path,
            config=SceneManagerConfig(running_summary_every_n_posts=0),
            event_bus=event_bus,
            cast_change_store=CastChangeStore(db),
        )
        scene = await _seed(scene_manager, fake_store)
        await scene_manager.append_post(
            scene.id,
            new_post(
                author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True
            ),
        )
        await scene_manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
        )
        change_id = await scene_manager.queue_cast_change(
            scene.id,
            character_ref="library:worlds/w/characters/reyes",
            change=CastChange.ENTER,
            is_pc=False,
            evidence="strides in",
            confidence=0.8,
            turn_id="T1",
        )
        await scene_manager.confirm_cast_change(scene.id, change_id)

        orch = _build_orch(
            scene_manager=scene_manager,
            event_bus=event_bus,
            fake_store=fake_store,
            fake_gateway=fake_gateway,
            fake_extractor=fake_extractor,
            fake_context_builder=fake_context_builder,
        )
        target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m1")
        result = await orch.delete_post_cascade("c1", scene.id, target.id)
        assert any("confirmed by a removed turn" in w for w in result.warnings)
    finally:
        await db.close()


async def test_advance_blocks_concurrent_cascade_delete(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """advance() holds the in-flight guard across on_advance_requested, so a
    cascade delete racing the dispatch is rejected (would otherwise truncate the
    pending PC posts the advance turn is about to consume)."""
    scene = await _seed(scene_manager, fake_store, extra_pcs=("beatrice",))
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="p1", is_player=True),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="beatrice", body="p2", is_player=True),
    )
    p1 = (await scene_manager.get_posts(scene.id))[0]

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    rejected: dict = {}
    original = scene_manager.on_advance_requested

    async def _hooked(scene_id):
        # Mid-dispatch: a concurrent delete must be rejected by the guard.
        try:
            await orch.delete_post_cascade("c1", scene.id, p1.id)
        except TurnAlreadyInProgressError:
            rejected["ok"] = True
        return await original(scene_id)

    scene_manager.on_advance_requested = _hooked  # type: ignore[assignment]
    await orch.advance("c1", scene.id)
    assert rejected.get("ok") is True


async def test_cascade_delete_rejects_review_items_for_removed_turns(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    """A fully-removed turn's pending review delta is skipped from reversal,
    but its review-queue row must also be rejected so it can't be approved
    later and re-apply state from a turn whose evidence is gone."""
    scene = await _seed(scene_manager, fake_store)
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True),
    )
    await scene_manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
    )
    queued = _seed_applied(fake_store, campaign_id="c1", turn_id="T1", target_id="fact-pending")
    fake_store.pending_delta_ids.add(queued)

    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    target = next(p for p in await scene_manager.get_posts(scene.id) if p.body == "m1")
    result = await orch.delete_post_cascade("c1", scene.id, target.id)

    # The unapplied review delta is not reversed, but its review row is rejected.
    assert queued not in fake_store.reversed_ids
    assert f"rq_{queued}" in fake_store.rejected_review_ids
    assert result.warnings == []
