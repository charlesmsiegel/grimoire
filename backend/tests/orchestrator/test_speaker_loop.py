"""Integration tests for the per_character_multi_call speaker loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator.config import OrchestratorConfig, SpeakerLoopConfig
from grimoire.orchestrator.service import OrchestratorService
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.scenes.types import AuthorKind, SceneInit

from .conftest import (
    FakeContextBuilder,
    FakeDB,
    FakeExtractor,
    FakeGateway,
    FakeStateStore,
    WSCollector,
    fixed_clock,
)

pytestmark = pytest.mark.integration


def _make_orchestrator(
    *,
    tmp_path: Path,
    gateway_chunks: list[str] | None = None,
    campaign_config: str | None = None,
    speaker_timeout: float = 1.0,
) -> tuple[OrchestratorService, SceneManager, WSCollector, FakeStateStore]:
    bus = EventBus()
    sm = SceneManager(tmp_path, config=SceneManagerConfig(running_summary_every_n_posts=0))
    gw = FakeGateway(chunks=gateway_chunks or ["Hello."])
    ext = FakeExtractor()
    db = FakeDB(campaigns={"camp1"}, pcs={"camp1": {"pc1"}})
    if campaign_config:
        db.campaign_configs["camp1"] = campaign_config
    store = FakeStateStore(db=db)
    ctx = FakeContextBuilder()
    ws = WSCollector()
    config = OrchestratorConfig(
        speaker_loop=SpeakerLoopConfig(timeout_seconds=speaker_timeout),
    )
    orch = OrchestratorService(
        event_bus=bus,
        scene_manager=sm,
        llm_gateway=gw,
        context_builder=ctx,
        extractor=ext,
        state_store=store,
        ws_push=ws,
        config=config,
        clock=fixed_clock(),
    )
    return orch, sm, ws, store


@pytest.mark.asyncio
async def test_speaker_loop_emits_waiting_event(tmp_path: Path) -> None:
    """In multi-call mode, the orchestrator emits speaker_round_waiting."""
    config = json.dumps({"narrator": {"response_mode": "per_character_multi_call"}})
    orch, sm, ws, _ = _make_orchestrator(
        tmp_path=tmp_path,
        campaign_config=config,
        speaker_timeout=0.1,
    )
    scene = await sm.start_scene(
        SceneInit(
            campaign_id="camp1",
            present_character_refs=["worlds/w/characters/alice"],
            present_pc_refs=["pc1"],
        )
    )
    await orch.submit_post("camp1", "pc1", "Hello Alice")
    # Wait for the turn to complete (timeout will end the speaker loop)
    await asyncio.sleep(1.0)

    ws_types = [m["type"] for _, m in ws.messages]
    assert "speaker_round_waiting" in ws_types

    posts = await sm.recent_posts(scene.id, n=20)
    npc_posts = [p for p in posts if p.author_kind == AuthorKind.NPC]
    assert len(npc_posts) >= 1
    assert npc_posts[0].author_npc_ref == "worlds/w/characters/alice"


@pytest.mark.asyncio
async def test_next_speaker_continues_loop(tmp_path: Path) -> None:
    """Calling next_speaker should produce another NPC post."""
    config = json.dumps({"narrator": {"response_mode": "per_character_multi_call"}})
    orch, sm, ws, _ = _make_orchestrator(
        tmp_path=tmp_path,
        campaign_config=config,
        speaker_timeout=2.0,
    )
    scene = await sm.start_scene(
        SceneInit(
            campaign_id="camp1",
            present_character_refs=["worlds/w/characters/alice", "worlds/w/characters/bob"],
            present_pc_refs=["pc1"],
        )
    )

    # Start the turn in a background task
    submit_task = asyncio.create_task(orch.submit_post("camp1", "pc1", "Hello everyone"))

    # Wait for the first speaker_round_waiting
    for _ in range(50):
        await asyncio.sleep(0.05)
        ws_types = [m["type"] for _, m in ws.messages]
        if "speaker_round_waiting" in ws_types:
            break

    # Trigger next speaker
    await orch.next_speaker("camp1")

    # Wait for second speaker_round_waiting
    for _ in range(50):
        await asyncio.sleep(0.05)
        ws_types = [m["type"] for _, m in ws.messages]
        if ws_types.count("speaker_round_waiting") >= 2:
            break

    # Let timeout end the loop
    await asyncio.sleep(2.5)
    await submit_task

    posts = await sm.recent_posts(scene.id, n=20)
    npc_posts = [p for p in posts if p.author_kind == AuthorKind.NPC]
    assert len(npc_posts) >= 2


@pytest.mark.asyncio
async def test_speaker_loop_refreshes_cast_after_confirmed_leave(tmp_path: Path) -> None:
    """Regression (#464): a cast change confirmed during ``speaker_round_waiting``
    must take effect on the next selection. With a single present NPC, confirming
    its LEAVE mid-wait should end the loop rather than re-select the departed NPC
    from a stale ``present_npcs`` list captured before the loop began.
    """
    config = json.dumps({"narrator": {"response_mode": "per_character_multi_call"}})
    orch, sm, _ws, _store = _make_orchestrator(
        tmp_path=tmp_path,
        campaign_config=config,
        speaker_timeout=2.0,
    )
    scene = await sm.start_scene(
        SceneInit(
            campaign_id="camp1",
            present_character_refs=["worlds/w/characters/alice"],
            present_pc_refs=["pc1"],
        )
    )

    submit_task = asyncio.create_task(orch.submit_post("camp1", "pc1", "Hello Alice"))

    # Wait until the loop is parked on the next-speaker event (Alice posted once).
    state = orch._state_for("camp1")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if state.speaker_loop_event is not None:
            break
    assert state.speaker_loop_event is not None

    # User confirms Alice's LEAVE while the loop waits, then advances the round.
    await sm.remove_present_character(scene.id, "worlds/w/characters/alice")
    await orch.next_speaker("camp1")

    await asyncio.sleep(0.2)
    await asyncio.wait_for(submit_task, timeout=3.0)

    posts = await sm.recent_posts(scene.id, n=20)
    npc_posts = [p for p in posts if p.author_kind == AuthorKind.NPC]
    # The departed NPC must not be re-selected after the refresh: exactly the
    # one pre-leave post, and no second round for the absent character.
    assert len(npc_posts) == 1
    assert npc_posts[0].author_npc_ref == "worlds/w/characters/alice"
