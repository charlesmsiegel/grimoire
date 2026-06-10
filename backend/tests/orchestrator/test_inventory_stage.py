"""The shared post-extraction state stage runs in both pipeline copies (#603).

The speaker loop (``per_character_multi_call``) used to skip the inventory
stage entirely: ``INVENTORY_CHANGE`` deltas extracted in a speaker round were
silently discarded while the single-response path applied them. These tests
pin the orchestrator → InventoryService handoff in both pipelines, the parity
between them, the fail-with-compensation failure semantics (#584), and a
holdings write end-to-end against the real InventoryService + StateStore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator.config import OrchestratorConfig, SpeakerLoopConfig
from grimoire.orchestrator.errors import OrchestratorError
from grimoire.orchestrator.service import OrchestratorService
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.scenes.types import SceneInit
from grimoire.types.common import Scope
from grimoire.types.state import DeltaKind, StateDelta

from .conftest import (
    FakeContextBuilder,
    FakeDB,
    FakeExtractor,
    FakeGateway,
    FakeInventory,
    FakeStateStore,
    WSCollector,
    fixed_clock,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MULTI_CALL_CONFIG = json.dumps({"narrator": {"response_mode": "per_character_multi_call"}})


def _inventory_delta() -> StateDelta:
    return StateDelta(
        kind=DeltaKind.INVENTORY_CHANGE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="ring",
        after={"action": "acquire", "item": "ring", "holder": "alice", "quantity": 1},
        confidence=0.95,
    )


def _make_orchestrator(
    *,
    tmp_path: Path,
    inventory: FakeInventory,
    deltas: list[StateDelta],
    campaign_config: str | None = None,
    speaker_timeout: float = 0.05,
) -> tuple[OrchestratorService, SceneManager, WSCollector]:
    bus = EventBus()
    sm = SceneManager(tmp_path, config=SceneManagerConfig(running_summary_every_n_posts=0))
    db = FakeDB(campaigns={"camp1"}, pcs={"camp1": {"pc1"}})
    if campaign_config:
        db.campaign_configs["camp1"] = campaign_config
    ws = WSCollector()
    orch = OrchestratorService(
        event_bus=bus,
        scene_manager=sm,
        llm_gateway=FakeGateway(chunks=["Alice pockets the ring."]),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(deltas=deltas),
        state_store=FakeStateStore(db=db),
        inventory=inventory,
        ws_push=ws,
        config=OrchestratorConfig(speaker_loop=SpeakerLoopConfig(timeout_seconds=speaker_timeout)),
        clock=fixed_clock(),
    )
    return orch, sm, ws


async def _start_scene(sm: SceneManager, npc_refs: list[str] | None = None):
    return await sm.start_scene(
        SceneInit(
            campaign_id="camp1",
            present_character_refs=npc_refs or ["worlds/w/characters/alice"],
            present_pc_refs=["pc1"],
        )
    )


def _subscribe_fragments(orch: OrchestratorService) -> list[Event]:
    captured: list[Event] = []
    orch.event_bus().subscribe(events.TURN_AUDIT_FRAGMENT, captured.append)
    return captured


async def test_speaker_round_inventory_deltas_reach_inventory(tmp_path: Path) -> None:
    """Regression (#603): an INVENTORY_CHANGE delta extracted in a speaker
    round must be handed to the InventoryService, not silently discarded."""
    inventory = FakeInventory()
    orch, sm, _ws = _make_orchestrator(
        tmp_path=tmp_path,
        inventory=inventory,
        deltas=[_inventory_delta()],
        campaign_config=MULTI_CALL_CONFIG,
    )
    await _start_scene(sm)

    result = await orch.submit_post("camp1", "pc1", "I hand Alice the ring")

    assert len(inventory.calls) >= 1
    call = inventory.calls[0]
    assert call["campaign_id"] == "camp1"
    assert call["turn_id"] == result.turn_id
    assert [d.kind for d in call["deltas"]] == [DeltaKind.INVENTORY_CHANGE]


async def test_inventory_handoff_parity_between_pipelines(tmp_path: Path) -> None:
    """The speaker loop hands the InventoryService the same payload the
    single-response path does (#603 acceptance criterion)."""
    inv_single = FakeInventory()
    orch_single, sm_single, _ = _make_orchestrator(
        tmp_path=tmp_path / "single",
        inventory=inv_single,
        deltas=[_inventory_delta()],
    )
    await _start_scene(sm_single)
    result_single = await orch_single.submit_post("camp1", "pc1", "I hand Alice the ring")

    inv_multi = FakeInventory()
    orch_multi, sm_multi, _ = _make_orchestrator(
        tmp_path=tmp_path / "multi",
        inventory=inv_multi,
        deltas=[_inventory_delta()],
        campaign_config=MULTI_CALL_CONFIG,
    )
    await _start_scene(sm_multi)
    result_multi = await orch_multi.submit_post("camp1", "pc1", "I hand Alice the ring")

    assert len(inv_single.calls) == 1
    assert len(inv_multi.calls) == 1
    single, multi = inv_single.calls[0], inv_multi.calls[0]
    assert single["campaign_id"] == multi["campaign_id"] == "camp1"
    assert single["turn_id"] == result_single.turn_id
    assert multi["turn_id"] == result_multi.turn_id
    assert [(d.kind, d.after) for d in single["deltas"]] == [
        (d.kind, d.after) for d in multi["deltas"]
    ]


async def test_speaker_round_inventory_failure_fails_turn_with_compensation(
    tmp_path: Path,
) -> None:
    """A failed inventory stage fails the turn (#584): the round's committed
    state is unwound, no completion is published, and the player post rolls
    back — never narrative state without its inventory effects."""
    inventory = FakeInventory(raise_on_apply=RuntimeError("inventory boom"))
    orch, sm, ws = _make_orchestrator(
        tmp_path=tmp_path,
        inventory=inventory,
        deltas=[_inventory_delta()],
        campaign_config=MULTI_CALL_CONFIG,
    )
    scene = await _start_scene(sm)

    with pytest.raises(OrchestratorError):
        await orch.submit_post("camp1", "pc1", "I hand Alice the ring")

    assert len(inventory.calls) == 1
    types = [m["type"] for _, m in ws.messages]
    assert "turn_failed" in types
    assert "turn_complete" not in types
    assert await sm.get_posts(scene.id) == []


async def test_single_response_inventory_failure_fails_turn_with_compensation(
    tmp_path: Path,
) -> None:
    """The single-response path fails the same failure the same way (#584)."""
    inventory = FakeInventory(raise_on_apply=RuntimeError("inventory boom"))
    orch, sm, ws = _make_orchestrator(
        tmp_path=tmp_path,
        inventory=inventory,
        deltas=[_inventory_delta()],
    )
    scene = await _start_scene(sm)

    with pytest.raises(OrchestratorError):
        await orch.submit_post("camp1", "pc1", "I hand Alice the ring")

    assert len(inventory.calls) == 1
    types = [m["type"] for _, m in ws.messages]
    assert "turn_failed" in types
    assert "turn_complete" not in types
    assert await sm.get_posts(scene.id) == []


async def test_multi_round_applied_deltas_accumulate_in_audit_fragment(tmp_path: Path) -> None:
    """Round results merge into one audit fragment: the TurnAuditor keeps the
    last value per fragment key, so per-round emission would persist only the
    final round's ``applied_deltas``. Two rounds x one auto-applied delta must
    surface both delta ids."""
    import asyncio

    fact_delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="fact-ring",
        after={"text": "Alice has the ring"},
        confidence=0.95,
    )
    orch, sm, ws = _make_orchestrator(
        tmp_path=tmp_path,
        inventory=FakeInventory(),
        deltas=[fact_delta],
        campaign_config=MULTI_CALL_CONFIG,
        speaker_timeout=2.0,
    )
    await _start_scene(sm, npc_refs=["worlds/w/characters/alice", "worlds/w/characters/bob"])
    fragments = _subscribe_fragments(orch)

    submit_task = asyncio.create_task(orch.submit_post("camp1", "pc1", "Hello everyone"))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if [m["type"] for _, m in ws.messages].count("speaker_round_waiting") >= 1:
            break
    await orch.next_speaker("camp1")
    for _ in range(100):
        await asyncio.sleep(0.05)
        if [m["type"] for _, m in ws.messages].count("speaker_round_waiting") >= 2:
            break
    await asyncio.wait_for(submit_task, timeout=10.0)

    applied_fragments = [e for e in fragments if "applied_deltas" in e.payload]
    assert len(applied_fragments) == 1
    assert len(applied_fragments[0].payload["applied_deltas"]) == 2


async def test_speaker_round_updates_holdings_with_real_inventory(tmp_path: Path) -> None:
    """End-to-end over the real StateStore + InventoryService: a speaker-round
    INVENTORY_CHANGE delta lands in ``inventory_holdings`` exactly as a
    single-response turn's would (#603 acceptance criterion)."""
    from grimoire.inventory.service import InventoryService
    from grimoire.state_store import StateStore
    from grimoire.storage import Database
    from grimoire.testing.db_template import stamp_migrated_db

    bus = EventBus()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    try:
        store = StateStore(db, tmp_path / "data")
        await store.upsert_campaign(campaign_id="camp1", name="C")
        await store.set_campaign_config(
            "camp1",
            {
                "narrator": {"response_mode": "per_character_multi_call"},
                "inventory": {"enabled": True},
            },
        )
        await store.add_pc(campaign_id="camp1", character_ref="pc1", display_name="PC One")
        await store.write_emergent(
            campaign_id="camp1",
            kind="character",
            entity_id="alice",
            frontmatter={"id": "alice", "name": "Alice"},
            body="",
            source="test",
        )

        sm = SceneManager(
            tmp_path / "scenes", config=SceneManagerConfig(running_summary_every_n_posts=0)
        )
        orch = OrchestratorService(
            event_bus=bus,
            scene_manager=sm,
            llm_gateway=FakeGateway(chunks=["Alice pockets the ring."]),
            context_builder=FakeContextBuilder(),
            extractor=FakeExtractor(deltas=[_inventory_delta()]),
            state_store=store,
            inventory=InventoryService(store=store, event_bus=bus),
            config=OrchestratorConfig(speaker_loop=SpeakerLoopConfig(timeout_seconds=0.05)),
        )
        await sm.start_scene(
            SceneInit(
                campaign_id="camp1",
                present_character_refs=["alice"],
                present_pc_refs=["pc1"],
            )
        )

        await orch.submit_post("camp1", "pc1", "I hand Alice the ring")

        rows = await store.list_inventory_holdings("camp1", item_ref="ring")
        assert len(rows) == 1
        assert rows[0]["holder_id"] == "alice"
    finally:
        await db.close()
