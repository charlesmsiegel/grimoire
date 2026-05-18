"""Tests for :class:`CharactersIntegration` — drift fan-out after turn_complete."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

from grimoire.characters.integration import CharactersIntegration
from grimoire.event_bus import Event, EventBus
from grimoire.orchestrator.config import BackgroundWorkConfig


@dataclass
class _FakeScene:
    id: str
    present_character_refs: list[str] = field(default_factory=list)


@dataclass
class _FakeSceneManager:
    scenes: dict[str, _FakeScene] = field(default_factory=dict)

    async def get_scene(self, scene_id: str) -> _FakeScene:
        return self.scenes[scene_id]


@dataclass
class _FakeCharactersService:
    calls: list[tuple[str, str]] = field(default_factory=list)
    raise_on_call: bool = False

    async def maybe_check_drift(self, ref: str, campaign_id: str, **kwargs: Any) -> None:
        self.calls.append((ref, campaign_id))
        if self.raise_on_call:
            raise RuntimeError("drift check boom")


async def _drain_pending_tasks() -> None:
    # Give scheduled drift-check tasks a chance to run.
    for _ in range(20):
        await asyncio.sleep(0)


async def test_characters_integration_samples_drift_on_turn_complete():
    bus = EventBus()
    sm = _FakeSceneManager(
        scenes={"s1": _FakeScene(id="s1", present_character_refs=["alice", "bob"])}
    )
    chars = _FakeCharactersService()

    integration = CharactersIntegration(
        chars,
        sm,
        bus,
        config=BackgroundWorkConfig(drift_check_sampling=1.0),
        rng=random.Random(0),
    )
    integration.start()
    try:
        await bus.emit(Event(type="turn_complete", payload={"campaign_id": "c1", "scene_id": "s1"}))
        await _drain_pending_tasks()
    finally:
        integration.stop()

    assert sorted(chars.calls) == [("alice", "c1"), ("bob", "c1")]


async def test_characters_integration_zero_sampling_skips_check():
    bus = EventBus()
    sm = _FakeSceneManager(scenes={"s1": _FakeScene(id="s1", present_character_refs=["alice"])})
    chars = _FakeCharactersService()

    integration = CharactersIntegration(
        chars,
        sm,
        bus,
        config=BackgroundWorkConfig(drift_check_sampling=0.0),
        rng=random.Random(0),
    )
    integration.start()
    try:
        await bus.emit(Event(type="turn_complete", payload={"campaign_id": "c1", "scene_id": "s1"}))
        await _drain_pending_tasks()
    finally:
        integration.stop()

    assert chars.calls == []


async def test_characters_integration_swallows_drift_errors():
    bus = EventBus()
    sm = _FakeSceneManager(scenes={"s1": _FakeScene(id="s1", present_character_refs=["alice"])})
    chars = _FakeCharactersService(raise_on_call=True)

    integration = CharactersIntegration(
        chars,
        sm,
        bus,
        config=BackgroundWorkConfig(drift_check_sampling=1.0),
        rng=random.Random(0),
    )
    integration.start()
    try:
        await bus.emit(Event(type="turn_complete", payload={"campaign_id": "c1", "scene_id": "s1"}))
        await _drain_pending_tasks()
    finally:
        integration.stop()

    # The integration must not propagate the error back through emit().
    assert chars.calls == [("alice", "c1")]
