"""Tests for the rewritten lore_for_post algorithm.

Spec: docs/superpowers/specs/2026-05-19-card-imports-design.md §4.
Covers enabled/constant/keyword matching, selective_logic combinations,
probability roll determinism, scan_depth, and priority sort.
"""

from __future__ import annotations

from grimoire.state_store import StateStore
from grimoire.world import WorldService


async def _seed_world_min(world: WorldService, world_id: str) -> None:
    await world.create_world(
        world_id,
        {"id": world_id, "name": world_id, "tags": [], "version": 1},
    )


async def _seed_lore_extended(
    world: WorldService,
    world_id: str,
    lore_id: str,
    *,
    keywords: list[str] | None = None,
    body: str = "",
    **frontmatter: object,
) -> None:
    fm: dict[str, object] = {
        "id": lore_id,
        "name": frontmatter.pop("title", lore_id),
        "title": frontmatter.pop("title", lore_id)
        if "title" not in frontmatter
        else frontmatter.pop("title"),
        "keywords": keywords or [],
        "tags": ["lore"],
        "secrecy": "public",
    }
    fm.update(frontmatter)
    await world.create_entity(world_id, "lore", lore_id, fm, body=body or f"body for {lore_id}")


async def _bind(store: StateStore, campaign_id: str, world_id: str) -> None:
    await store.upsert_campaign(campaign_id=campaign_id, name=campaign_id)
    await store.upsert_world_ref(
        campaign_id=campaign_id,
        world_id=world_id,
        priority=1,
        include=None,
        track_latest=True,
    )


# ---------------------------------------------------------------------------
# Enabled / constant
# ---------------------------------------------------------------------------


async def test_enabled_false_is_skipped(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "off", keywords=["tremere"], enabled=False)
    await _seed_lore_extended(world, "w1", "on", keywords=["tremere"])
    await _bind(store, "c1", "w1")
    hits = await world.lore_for_post("the tremere are watching", "c1")
    assert {h.id for h in hits} == {"on"}


async def test_constant_fires_without_keyword(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "always", keywords=[], constant=True)
    await _bind(store, "c1", "w1")
    hits = await world.lore_for_post("nothing relevant here", "c1")
    assert {h.id for h in hits} == {"always"}


# ---------------------------------------------------------------------------
# Primary keyword matching
# ---------------------------------------------------------------------------


async def test_primary_key_match_default_case_insensitive_substring(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["Tremere"])
    await _bind(store, "c1", "w1")
    hits = await world.lore_for_post("the TREMERE chantry burned", "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_primary_key_match_case_sensitive(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["Tremere"], case_sensitive=True)
    await _bind(store, "c1", "w1")
    assert (await world.lore_for_post("the tremere burned", "c1")) == []
    hits = await world.lore_for_post("the Tremere burned", "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_primary_key_match_whole_words(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "mage", keywords=["mage"], match_whole_words=True)
    await _bind(store, "c1", "w1")
    # substring match would catch "magesty" but whole-word match must not
    assert (await world.lore_for_post("their magesty arrived", "c1")) == []
    hits = await world.lore_for_post("they hired a mage", "c1")
    assert {h.id for h in hits} == {"mage"}


# ---------------------------------------------------------------------------
# Secondary keys / selective_logic
# ---------------------------------------------------------------------------


async def test_secondary_keys_and_any(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(
        world,
        "w1",
        "trem",
        keywords=["tremere"],
        secondary_keys=["chantry", "blood"],
        selective_logic="and_any",
    )
    await _bind(store, "c1", "w1")
    assert (await world.lore_for_post("the tremere were quiet", "c1")) == []
    hits = await world.lore_for_post("the tremere chantry burned", "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_secondary_keys_and_all(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(
        world,
        "w1",
        "trem",
        keywords=["tremere"],
        secondary_keys=["chantry", "blood"],
        selective_logic="and_all",
    )
    await _bind(store, "c1", "w1")
    assert (await world.lore_for_post("the tremere chantry burned", "c1")) == []
    hits = await world.lore_for_post("the tremere chantry was thick with blood", "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_secondary_keys_not_any(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(
        world,
        "w1",
        "trem",
        keywords=["tremere"],
        secondary_keys=["chantry", "blood"],
        selective_logic="not_any",
    )
    await _bind(store, "c1", "w1")
    # any secondary present → blocked
    assert (await world.lore_for_post("the tremere chantry burned", "c1")) == []
    hits = await world.lore_for_post("the tremere are quiet", "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_secondary_keys_not_all(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(
        world,
        "w1",
        "trem",
        keywords=["tremere"],
        secondary_keys=["chantry", "blood"],
        selective_logic="not_all",
    )
    await _bind(store, "c1", "w1")
    # both present → blocked
    assert (await world.lore_for_post("tremere chantry of blood", "c1")) == []
    # one present → fires
    hits = await world.lore_for_post("the tremere chantry", "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_empty_secondary_keys_treated_as_no_requirement(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(
        world,
        "w1",
        "trem",
        keywords=["tremere"],
        secondary_keys=[],
        selective_logic="and_all",
    )
    await _bind(store, "c1", "w1")
    hits = await world.lore_for_post("the tremere are everywhere", "c1")
    assert {h.id for h in hits} == {"trem"}


# ---------------------------------------------------------------------------
# Probability
# ---------------------------------------------------------------------------


async def test_probability_zero_never_fires(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["tremere"], probability=0)
    await _bind(store, "c1", "w1")
    assert (await world.lore_for_post("tremere", "c1", turn_id="t1")) == []


async def test_probability_100_always_fires(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["tremere"], probability=100)
    await _bind(store, "c1", "w1")
    hits = await world.lore_for_post("tremere", "c1", turn_id="t1")
    assert {h.id for h in hits} == {"trem"}


async def test_probability_deterministic_per_turn(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["tremere"], probability=50)
    await _bind(store, "c1", "w1")
    h1 = await world.lore_for_post("tremere", "c1", turn_id="t-abc")
    h2 = await world.lore_for_post("tremere", "c1", turn_id="t-abc")
    assert {x.id for x in h1} == {x.id for x in h2}


# ---------------------------------------------------------------------------
# scan_depth
# ---------------------------------------------------------------------------


async def test_scan_depth_limits_haystack_to_last_n_lines(
    world: WorldService, store: StateStore
) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["tremere"], scan_depth=2)
    await _bind(store, "c1", "w1")
    text = "the tremere are mentioned here\nline 2\nline 3"
    # scan_depth=2 takes only the last 2 lines: "line 2" and "line 3"
    assert (await world.lore_for_post(text, "c1")) == []
    text2 = "line 1\nthe tremere appear\nline 3"
    hits = await world.lore_for_post(text2, "c1")
    assert {h.id for h in hits} == {"trem"}


async def test_scan_depth_zero_scans_nothing(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "trem", keywords=["tremere"], scan_depth=0)
    await _bind(store, "c1", "w1")
    assert (await world.lore_for_post("the tremere are loud", "c1")) == []


# ---------------------------------------------------------------------------
# Priority / max_results
# ---------------------------------------------------------------------------


async def test_priority_sort_and_max_results(world: WorldService, store: StateStore) -> None:
    await _seed_world_min(world, "w1")
    await _seed_lore_extended(world, "w1", "low", keywords=["wraith"], priority=10)
    await _seed_lore_extended(world, "w1", "high", keywords=["wraith"], priority=900)
    await _seed_lore_extended(world, "w1", "mid", keywords=["wraith"], priority=500)
    await _bind(store, "c1", "w1")
    hits = await world.lore_for_post("a wraith appeared", "c1", max_results=2)
    assert [h.id for h in hits] == ["high", "mid"]
