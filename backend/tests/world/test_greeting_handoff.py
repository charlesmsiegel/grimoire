"""§8 Greeting hands off to SceneManager for scene-1 seeding."""

from __future__ import annotations

import pytest


class _FakeSceneManager:
    def __init__(self) -> None:
        self.calls: list = []
        self.scene = type("Scene", (), {"id": "scene-1"})()

    async def start_scene(self, init):
        self.calls.append(init)
        return self.scene


async def _seed_world_with_greeting(library) -> None:
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1",
        "location",
        "town",
        {"id": "town", "name": "Town", "kind": "city"},
        body="",
    )
    await library.create_entity(
        "w1",
        "greeting",
        "intro",
        {
            "id": "intro",
            "world_id": "w1",
            "name": "Intro",
            "starting_location": "library:worlds/w1/locations/town",
            "starting_time": "2025-01-01T08:00:00+00:00",
            "present_characters": ["library:characters/alice"],
            "pov_character": "library:characters/pc",
            "mood": "tense",
            "tags": ["dawn", "city"],
        },
        body="You wake at dawn in the town square.",
    )


async def test_seed_scene_from_greeting_calls_start_scene(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await _seed_world_with_greeting(library)
    sm = _FakeSceneManager()
    scene = await world.seed_scene_from_greeting(
        campaign_id="camp-1",
        greeting_id="intro",
        world_id="w1",
        scene_manager=sm,
    )
    assert scene.id == "scene-1"
    assert len(sm.calls) == 1
    init = sm.calls[0]
    assert init.greeting_id == "intro"
    assert init.campaign_id == "camp-1"
    assert init.title == "Intro"
    assert init.location_ref == "library:worlds/w1/locations/town"
    assert init.pov_character_ref == "library:characters/pc"
    assert init.present_character_refs == ["library:characters/alice"]
    assert init.mood == "tense"
    assert init.tags == ["dawn", "city"]


async def test_seed_scene_unknown_greeting_raises(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await _seed_world_with_greeting(library)
    with pytest.raises(Exception):
        await world.seed_scene_from_greeting(
            campaign_id="camp-1",
            greeting_id="missing",
            world_id="w1",
            scene_manager=_FakeSceneManager(),
        )


async def test_seed_scene_handles_missing_optional_fields(store, library, world) -> None:
    """Greeting with minimal frontmatter still produces a valid SceneInit."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1",
        "greeting",
        "minimal",
        {
            "id": "minimal",
            "world_id": "w1",
            "name": "Minimal",
            "starting_location": None,
            "starting_time": None,
        },
        body="",
    )
    sm = _FakeSceneManager()
    await world.seed_scene_from_greeting(
        campaign_id="camp-1",
        greeting_id="minimal",
        world_id="w1",
        scene_manager=sm,
    )
    init = sm.calls[0]
    assert init.title == "Minimal"
    assert init.location_ref is None
    assert init.in_game_start is None
    assert init.present_character_refs == []
