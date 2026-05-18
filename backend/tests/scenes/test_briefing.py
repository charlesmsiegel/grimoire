"""Scene Manager pre-scene briefing (§10).

When ``SceneManager`` is constructed with a Continuity wired up, calling
``start_scene`` should pull a per-scene briefing from
``Continuity.brief_for_scene`` and stuff a compact summary into the
``scene_started`` event payload so the Frontend can render it without an
extra round-trip.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from grimoire.continuity import (
    Commitment,
    CommitmentKind,
    ContinuityService,
    InGameTime,
)
from grimoire.scenes import (
    SCENE_STARTED,
    InMemoryEventBus,
    SceneInit,
    SceneManager,
    SceneManagerConfig,
)
from tests.continuity.conftest import make_fact

pytestmark = pytest.mark.asyncio


async def test_start_scene_includes_briefing_payload(tmp_path: Path) -> None:
    continuity = ContinuityService()
    await continuity.add_fact(
        make_fact(text="winifred knows about the orchard.", characters=["winifred"]),
        source="narrator",
    )
    await continuity.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="winifred will return the heirloom.",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
            from_id="winifred",
            to_id="rosaline",
        ),
        source="extractor",
    )
    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
        continuity=continuity,
    )

    await manager.start_scene(
        SceneInit(
            campaign_id="campaign-a",
            title="Garden Confrontation",
            location_ref="garden",
            in_game_start=datetime(2024, 10, 31, 22, 0, 0),
            present_pc_refs=["winifred"],
            present_character_refs=["winifred"],
        )
    )

    events = [e for e in bus.events if e.type == SCENE_STARTED]
    assert events, "scene_started event not emitted"
    briefing = events[0].payload.get("briefing")
    assert briefing is not None, "briefing payload missing"
    assert briefing["pc_refs"] == ["winifred"]
    assert briefing["commitment_count"] >= 1
    assert briefing["fact_count"] >= 1
    assert "winifred" in (briefing["fact_texts"][0])


async def test_start_scene_briefing_none_when_no_continuity(tmp_path: Path) -> None:
    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
    )
    await manager.start_scene(
        SceneInit(
            campaign_id="campaign-a",
            title="No briefing",
            location_ref="x",
            in_game_start=datetime(2024, 1, 1, 0, 0, 0),
            present_pc_refs=[],
            present_character_refs=[],
        )
    )
    events = [e for e in bus.events if e.type == SCENE_STARTED]
    assert events
    assert events[0].payload.get("briefing") is None
