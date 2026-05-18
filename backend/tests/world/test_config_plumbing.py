"""WorldConfig knobs flow into lore_for_post / lore_by_keyword."""

from __future__ import annotations

import pytest

from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.world import LoreConfig, WorldConfig, WorldService


@pytest.fixture
async def world_with_lore(store: StateStore, library: LibraryService):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1",
        "lore",
        "fire",
        {"id": "fire", "name": "Fire Lore", "keywords": ["fire", "ember"]},
        body="Long ago, fire was discovered.",
    )
    await library.create_entity(
        "w1",
        "lore",
        "ice",
        {"id": "ice", "name": "Ice Lore", "keywords": ["ice", "frost"]},
        body="Ice is cold.",
    )
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w1",
        priority=1,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )
    return WorldService(library)


async def test_config_overrides_default_min_length(world_with_lore) -> None:
    svc = world_with_lore
    svc.config = WorldConfig(lore=LoreConfig(keyword_min_length=10))
    hits = await svc.lore_by_keyword("fire", campaign_id="camp-1")
    assert hits == []  # "fire" is 4 chars, below the configured 10


async def test_config_default_min_length_still_works(world_with_lore) -> None:
    svc = world_with_lore  # default min_length=4
    hits = await svc.lore_by_keyword("fire", campaign_id="camp-1")
    assert len(hits) == 1


async def test_lore_for_post_honors_config_max_results(world_with_lore) -> None:
    svc = world_with_lore
    svc.config = WorldConfig(lore=LoreConfig(max_lore_in_archive=1))
    hits = await svc.lore_for_post("the fire burned bright next to the ice", campaign_id="camp-1")
    assert len(hits) == 1
