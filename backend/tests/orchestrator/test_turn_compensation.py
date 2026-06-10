"""Characterization tests for #584: cross-stage compensation in the turn
pipeline.

Covers the two gaps:

1. ``resolve_pre_roll`` used to clear ``pending_pre_roll`` before running the
   continuation pipeline — a mid-pipeline failure stranded the player on a
   cleared pre-roll plus a half-applied turn. It must now re-park the turn
   (resumable) with the committed batch unwound.
2. A failure in any stage after the delta batch committed (inventory apply,
   post append, ...) must reverse the batch — and delete any response posts
   appended after it — before the turn error propagates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator import OrchestratorConfig, OrchestratorService
from grimoire.orchestrator.config import PreRollConfig
from grimoire.orchestrator.errors import OrchestratorError
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneInit
from grimoire.types.mechanics import ProposalResolution, ProposedRoll, Roll, RollResult
from grimoire.types.state import DeltaKind, StateDelta

from .conftest import (
    FakeContextBuilder,
    FakeExtractor,
    FakeGateway,
    FakeStateStore,
    WSCollector,
)

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class FlakyInventory:
    """Inventory facade whose ``apply_from_deltas`` fails the first N calls.

    Successful applies return a ``rollback`` payload (mirroring the real
    service); ``restore_holders`` records what the unwind hands back.
    """

    fail_times: int = 0
    rollback_payload: list = field(default_factory=lambda: [("character", "joe", [])])
    calls: list[dict] = field(default_factory=list)
    restores: list[dict] = field(default_factory=list)

    async def apply_from_deltas(
        self, *, campaign_id: str, turn_id: str | None, deltas: list[Any]
    ) -> dict | None:
        self.calls.append({"campaign_id": campaign_id, "turn_id": turn_id})
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("inventory boom")
        return {"touched": 1, "flags": 0, "rollback": list(self.rollback_payload)}

    async def restore_holders(
        self, *, campaign_id: str, turn_id: str | None, rollback: list
    ) -> None:
        self.restores.append(
            {"campaign_id": campaign_id, "turn_id": turn_id, "rollback": list(rollback)}
        )


@dataclass
class StubMechanics:
    """Returns preset proposals so the turn parks on pre_roll_pending."""

    proposals: list[ProposedRoll] = field(default_factory=list)
    resolve_calls: list[Roll] = field(default_factory=list)

    async def evaluate_pre_roll(
        self, campaign_id: str, player_input: str, scene: Any
    ) -> list[ProposedRoll]:
        return list(self.proposals)

    async def resolve_roll(self, campaign_id: str, roll: Roll) -> RollResult:
        self.resolve_calls.append(roll)
        return RollResult(roll_id=roll.id, dice=[6, 6], successes=2, outcome="success")


class FailingScenes:
    """Delegates to a real SceneManager, with scripted failures.

    ``fail_append_on_call`` fails the Nth ``append_post`` (1-based, counting
    the player post). ``fail_list_pending_cast_changes`` makes the end-of-turn
    cast-change listing raise — i.e. a failure *after* response posts landed.
    """

    def __init__(
        self,
        inner: SceneManager,
        *,
        fail_append_on_call: int | None = None,
        fail_list_pending_cast_changes: bool = False,
    ) -> None:
        self._inner = inner
        self._fail_append_on_call = fail_append_on_call
        self._fail_list_pending = fail_list_pending_cast_changes
        self.append_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def append_post(self, scene_id: str, post: Any) -> Any:
        self.append_calls += 1
        if self.append_calls == self._fail_append_on_call:
            raise RuntimeError("append boom")
        return await self._inner.append_post(scene_id, post)

    async def list_pending_cast_changes(self, scene_id: str) -> list[Any]:
        if self._fail_list_pending:
            raise RuntimeError("cast-change listing boom")
        return await self._inner.list_pending_cast_changes(scene_id)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _auto_delta(target_id: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope="campaign-sqlite",
        target_id=target_id,
        target_table="facts",
        after={"text": target_id},
        confidence=0.95,
        source="extractor",
    )


async def _seed(scene_manager: Any, fake_store: FakeStateStore):
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


def _build_orch(
    *,
    scene_manager: Any,
    event_bus: EventBus,
    fake_store: FakeStateStore,
    fake_gateway: FakeGateway,
    fake_extractor: FakeExtractor,
    fake_context_builder: FakeContextBuilder,
    ws: WSCollector,
    inventory: Any | None = None,
    mechanics: Any | None = None,
    config: OrchestratorConfig | None = None,
) -> OrchestratorService:
    return OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        inventory=inventory,
        mechanics=mechanics,
        ws_push=ws,
        config=config,
    )


def _ws_events(ws: WSCollector, type_: str) -> list[dict]:
    return [m for _, m in ws.messages if m.get("type") == type_]


# --------------------------------------------------------------------------- #
# Post-batch failure on the direct (no pre-roll) path
# --------------------------------------------------------------------------- #


async def test_inventory_failure_reverses_batch_and_fails_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """Inventory apply failure is no longer log-and-continue: the committed
    delta batch is reversed and the turn fails with the player post rolled
    back — never narrative deltas applied with inventory silently dropped."""
    scene = await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [_auto_delta("fact-1")]
    inventory = FlakyInventory(fail_times=99)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        inventory=inventory,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "I pick up the lamp.")

    assert inventory.calls, "inventory stage ran"
    # The batch committed, then was reversed by the cross-stage unwind.
    assert [e["id"] for e in fake_store.applied] == ["d_0000"]
    assert fake_store.reversed_ids == ["d_0000"]
    # Player post rolled back; no response post was ever appended.
    assert await scene_manager.get_posts(scene.id) == []
    assert _ws_events(ws, "turn_failed")
    assert await orch.turn_in_progress("c1") is None


async def test_failure_after_posts_appended_deletes_them_lifo(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """A failure after response posts landed deletes them and reverses the
    batch newest-first — the whole turn unwinds, not just the SQLite half."""
    scenes = FailingScenes(scene_manager, fail_list_pending_cast_changes=True)
    scene = await _seed(scenes, fake_store)
    fake_extractor.deltas = [_auto_delta("fact-1"), _auto_delta("fact-2")]
    orch = _build_orch(
        scene_manager=scenes,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "I bow.")

    # LIFO across the batch.
    assert [e["id"] for e in fake_store.applied] == ["d_0000", "d_0001"]
    assert fake_store.reversed_ids == ["d_0001", "d_0000"]
    # Response post deleted by the unwind, player post by the rollback.
    assert await scene_manager.get_posts(scene.id) == []


async def test_failure_after_inventory_apply_restores_holders(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """A failure in a stage *after* a successful inventory apply hands the
    apply's pre-images back through restore_holders, so committed holder
    writes unwind with the rest of the turn."""
    scenes = FailingScenes(scene_manager, fail_list_pending_cast_changes=True)
    await _seed(scenes, fake_store)
    fake_extractor.deltas = [_auto_delta("fact-1")]
    inventory = FlakyInventory(fail_times=0, rollback_payload=[("character", "joe", [])])
    orch = _build_orch(
        scene_manager=scenes,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        inventory=inventory,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "I pocket the coin.")

    assert inventory.calls and len(inventory.restores) == 1
    assert inventory.restores[0]["rollback"] == [("character", "joe", [])]
    assert fake_store.reversed_ids == ["d_0000"]


async def test_unwind_rejects_queued_review_items(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """Review items queued by the failed turn are rejected by the unwind so
    the queue doesn't surface a turn that no longer exists (a retry would
    re-queue fresh rows)."""
    await _seed(scene_manager, fake_store)
    review_delta = StateDelta(
        kind=DeltaKind.OTHER,
        target_scope="campaign-sqlite",
        target_id="fact-low",
        target_table="facts",
        after={"text": "uncertain"},
        confidence=0.65,
        source="extractor",
    )
    fake_extractor.deltas = [_auto_delta("fact-1"), review_delta]
    inventory = FlakyInventory(fail_times=99)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        inventory=inventory,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "I bow.")

    assert [e["id"] for e in fake_store.reviewed] == ["r_0000"]
    assert fake_store.rejected_review_ids == ["r_0000"]
    assert fake_store.reversed_ids == ["d_0000"]


# --------------------------------------------------------------------------- #
# resolve_pre_roll: failure keeps the pre-roll resumable
# --------------------------------------------------------------------------- #


async def test_resolve_pre_roll_failure_is_resumable(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    scene = await _seed(scene_manager, fake_store)
    fake_extractor.deltas = [_auto_delta("fact-1")]
    inventory = FlakyInventory(fail_times=1)
    mechanics = StubMechanics(proposals=[ProposedRoll(label="climb", kind="dice-pool", pool=4)])
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        inventory=inventory,
        mechanics=mechanics,
        config=OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="always")),
    )

    result = await orch.submit_post("c1", "alistair", "I climb the tower")
    turn_id = result.turn_id
    assert turn_id

    resolutions = [ProposalResolution(label="climb", accepted=True)]
    with pytest.raises(OrchestratorError):
        await orch.resolve_pre_roll("c1", turn_id, resolutions)

    # The first attempt's batch committed and was reversed.
    assert [e["id"] for e in fake_store.applied] == ["d_0000"]
    assert fake_store.reversed_ids == ["d_0000"]
    # The turn re-parked: still active in pre_roll_pending, player post kept.
    status = await orch.turn_in_progress("c1")
    assert status is not None and status.stage == "pre_roll_pending"
    posts = await scene_manager.get_posts(scene.id)
    assert [p.is_player for p in posts] == [True]
    # The failure was surfaced with the resumable marker.
    failed = _ws_events(ws, "turn_failed")
    assert failed and failed[-1].get("pre_roll_resumable") is True

    # Second submission of the same resolutions completes the turn.
    second = await orch.resolve_pre_roll("c1", turn_id, resolutions)
    assert second.accepted and second.turn_id == turn_id
    assert len(mechanics.resolve_calls) == 2  # proposals re-rolled on retry
    assert await orch.turn_in_progress("c1") is None
    posts = await scene_manager.get_posts(scene.id)
    assert [p.is_player for p in posts] == [True, False]
    # The retry's batch stayed applied.
    assert [e["id"] for e in fake_store.applied] == ["d_0000", "d_0001"]
    assert fake_store.reversed_ids == ["d_0000"]
    assert _ws_events(ws, "turn_complete")

    # The lock was released: the campaign accepts the next post.
    next_result = await orch.submit_post("c1", "alistair", "Onward.")
    assert next_result.accepted


async def test_resolve_pre_roll_after_cancel_cleans_up(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """Cancelling a parked turn then resolving drops it cleanly: pending
    cleared, player post rolled back, lock released — not an error and not a
    re-park."""
    scene = await _seed(scene_manager, fake_store)
    mechanics = StubMechanics(proposals=[ProposedRoll(label="climb", kind="dice-pool", pool=4)])
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        mechanics=mechanics,
        config=OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="always")),
    )

    result = await orch.submit_post("c1", "alistair", "I climb")
    turn_id = result.turn_id
    assert turn_id
    assert await orch.cancel_turn("c1", turn_id)

    resolved = await orch.resolve_pre_roll("c1", turn_id, [])
    assert resolved.accepted and resolved.auto_responding is False
    assert resolved.reason == "turn cancelled"
    assert await orch.turn_in_progress("c1") is None
    assert await scene_manager.get_posts(scene.id) == []
    assert _ws_events(ws, "turn_cancelled")
    with pytest.raises(OrchestratorError, match="no pre_roll_pending"):
        await orch.resolve_pre_roll("c1", turn_id, [])
    # Lock released: the campaign accepts the next post.
    assert (await orch.submit_post("c1", "alistair", "Onward.")).accepted


async def test_concurrent_resolve_pre_roll_is_rejected(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """A second resolve call while the first is mid-pipeline must not
    double-roll or run the continuation twice — it is rejected up front."""
    import asyncio

    await _seed(scene_manager, fake_store)
    fake_gateway.chunk_delay = 0.05  # keep the first resolve in-flight
    mechanics = StubMechanics(proposals=[ProposedRoll(label="climb", kind="dice-pool", pool=4)])
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        mechanics=mechanics,
        config=OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="always")),
    )

    result = await orch.submit_post("c1", "alistair", "I climb")
    turn_id = result.turn_id
    assert turn_id

    first = asyncio.create_task(orch.resolve_pre_roll("c1", turn_id, []))
    await asyncio.sleep(0.02)  # first call is awaiting the gateway stream
    with pytest.raises(OrchestratorError, match="already being resolved"):
        await orch.resolve_pre_roll("c1", turn_id, [])

    second = await first
    assert second.accepted
    # Exactly one roll resolution and one streamed response.
    assert len(mechanics.resolve_calls) == 1
    assert len(fake_gateway.seen_requests) == 1
    assert await orch.turn_in_progress("c1") is None


async def test_resolve_pre_roll_success_clears_pending(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws
):
    """A second resolve after success must not double-process the turn."""
    await _seed(scene_manager, fake_store)
    mechanics = StubMechanics(proposals=[ProposedRoll(label="climb", kind="dice-pool", pool=4)])
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
        mechanics=mechanics,
        config=OrchestratorConfig(pre_roll=PreRollConfig(confirm_before_executing="always")),
    )

    result = await orch.submit_post("c1", "alistair", "I climb")
    turn_id = result.turn_id
    assert turn_id
    await orch.resolve_pre_roll("c1", turn_id, [])

    with pytest.raises(OrchestratorError, match="no pre_roll_pending"):
        await orch.resolve_pre_roll("c1", turn_id, [])


# --------------------------------------------------------------------------- #
# Speaker loop: a failed round unwinds its own batch
# --------------------------------------------------------------------------- #


async def test_speaker_round_append_failure_reverses_round_batch(
    event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder, ws, tmp_path
):
    import json

    from grimoire.scenes.manager import SceneManagerConfig

    inner = SceneManager(tmp_path, config=SceneManagerConfig(running_summary_every_n_posts=0))
    # Call 1 appends the player post; call 2 is the round's NPC post.
    scenes = FailingScenes(inner, fail_append_on_call=2)
    fake_store.db.campaigns.add("c1")
    fake_store.db.pcs["c1"] = {"alistair"}
    fake_store.db.campaign_configs["c1"] = json.dumps(
        {"narrator": {"response_mode": "per_character_multi_call"}}
    )
    scene = await scenes.start_scene(
        SceneInit(
            campaign_id="c1",
            present_pc_refs=["alistair"],
            present_character_refs=["alistair", "worlds/w/characters/alice"],
        )
    )
    fake_extractor.deltas = [_auto_delta("fact-1")]
    orch = _build_orch(
        scene_manager=scenes,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
        ws=ws,
    )

    with pytest.raises(OrchestratorError):
        await orch.submit_post("c1", "alistair", "Hello Alice")

    # The round's batch was reversed; the player post was rolled back.
    assert [e["id"] for e in fake_store.applied] == ["d_0000"]
    assert fake_store.reversed_ids == ["d_0000"]
    assert await inner.get_posts(scene.id) == []
