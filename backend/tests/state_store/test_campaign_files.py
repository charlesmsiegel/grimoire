"""Overrides, emergent entities, sheets, image metadata, and the resolve cascade."""

from __future__ import annotations

from grimoire.state_store import StateStore


async def _seed_campaign(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Test Campaign")


async def test_emergent_entity_round_trip(store: StateStore) -> None:
    await _seed_campaign(store)
    target = await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="He keeps the books.",
        source="extractor",
    )
    assert target.exists()

    fetched = await store.get_emergent("c1", "character", "the-bartender")
    assert fetched["frontmatter"]["name"] == "The Bartender"
    assert "books" in fetched["body"]

    listed = await store.list_emergent("c1", "character")
    assert len(listed) == 1
    assert listed[0]["asset_id"] == "the-bartender"


async def test_override_falls_back_to_library_via_resolve(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred", "voice": "patient"},
        body="Library copy.",
        source="user",
    )
    await store.upsert_world_ref(
        campaign_id="c1",
        world_id="wod-london",
        priority=1,
        include=["character"],
        track_latest=True,
    )

    # No override yet: resolve returns the library row.
    resolved = await store.resolve_entity(
        campaign_id="c1",
        branch_id="c1:main",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["source"] == "library-live"
    assert resolved["frontmatter"]["voice"] == "patient"

    # Write an override; resolve merges frontmatter on top of the library row.
    await store.write_override(
        campaign_id="c1",
        library_id="worlds/wod-london/characters/winifred",
        patch={"voice": "wary", "mood": "grim"},
        source="user",
    )
    resolved = await store.resolve_entity(
        campaign_id="c1",
        branch_id="c1:main",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["source"] == "campaign-override"
    assert resolved["frontmatter"]["voice"] == "wary"
    assert resolved["frontmatter"]["mood"] == "grim"
    assert resolved["frontmatter"]["name"] == "winifred"  # library still wins for unchanged keys


async def test_resolve_finds_emergent_when_world_omitted(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="campaign-local",
        source="extractor",
    )
    resolved = await store.resolve_entity(
        campaign_id="c1",
        branch_id="c1:main",
        kind="character",
        asset_id="the-bartender",
    )
    assert resolved["source"] == "campaign-emergent"


async def test_sheet_write_round_trip(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_sheet(
        campaign_id="c1",
        kind="character",
        entity_id="winifred",
        mechanics_id="wod",
        sheet={"clan": "Toreador", "attributes": {"dex": 4}},
        source="user",
    )
    sheet = await store.get_sheet("c1", "character", "winifred", "wod")
    assert sheet["clan"] == "Toreador"
    assert sheet["attributes"]["dex"] == 4


async def test_image_metadata_write(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_image_metadata(
        campaign_id="c1",
        image_id="img-123",
        metadata={
            "prompt": "a foggy street",
            "branch_id": "c1:main",
            "scene_id": "0001",
        },
        source="imagegen",
    )
    log = await store.get_delta_log(campaign_id="c1")
    write_log = [d for d in log if d.kind == "image_metadata_write"]
    assert len(write_log) == 1
