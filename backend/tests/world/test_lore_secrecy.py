"""§2 Player-facing lore views filter restricted + secret entries."""

from __future__ import annotations

import pytest


async def _seed_lore_with_secrecy(store, library) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    for asset_id, secrecy in [
        ("public-fact", "public"),
        ("common-knowledge", "common-knowledge"),
        ("restricted-fact", "restricted"),
        ("secret-fact", "secret"),
    ]:
        await library.create_entity(
            "w1",
            "lore",
            asset_id,
            {
                "id": asset_id,
                "name": f"{asset_id.title()}",
                "keywords": ["thing"],
                "secrecy": secrecy,
            },
            body=f"Lore body for {asset_id}.",
        )
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w1",
        priority=1,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )


async def test_default_audience_returns_all(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_by_keyword("thing", campaign_id="camp-1")
    ids = {e.id for e in out}
    assert ids == {"public-fact", "common-knowledge", "restricted-fact", "secret-fact"}


async def test_player_audience_drops_restricted_and_secret(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_by_keyword("thing", campaign_id="camp-1", audience="player")
    ids = {e.id for e in out}
    assert ids == {"public-fact", "common-knowledge"}


async def test_model_audience_returns_all_explicit(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_by_keyword("thing", campaign_id="camp-1", audience="model")
    ids = {e.id for e in out}
    assert ids == {"public-fact", "common-knowledge", "restricted-fact", "secret-fact"}


async def test_lore_for_post_player_filter(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.lore_for_post(
        "tell me about this thing", campaign_id="camp-1", audience="player"
    )
    assert {e.id for e in out} == {"public-fact", "common-knowledge"}


async def test_search_lore_player_filter(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    out = await world.search_lore("Lore body", campaign_id="camp-1", audience="player")
    assert {e.id for e in out} == {"public-fact", "common-knowledge"}


async def test_unknown_audience_raises(store, library, world) -> None:
    await _seed_lore_with_secrecy(store, library)
    with pytest.raises(ValueError, match="audience"):
        await world.lore_by_keyword("thing", campaign_id="camp-1", audience="alien")
