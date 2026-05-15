"""Library snapshot writing on bind, upgrade, and pinned-vs-live reads."""

from __future__ import annotations

from grimoire.state_store import StateStore


async def _seed_library(store: StateStore) -> None:
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="v1",
        source="user",
    )
    await store.write_library_file(
        library_id="worlds/wod-london/characters/alistair",
        frontmatter={"name": "Alistair"},
        body="alpha",
        source="user",
    )


async def test_pinned_bind_writes_snapshots(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Pinned")
    await _seed_library(store)

    await store.upsert_world_ref(
        campaign_id="c1",
        world_id="wod-london",
        priority=1,
        include=["character"],
        track_latest=False,
    )

    # Now update the library — pinned campaign should still see v1.
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="v2",
        source="user",
    )

    resolved = await store.resolve_entity(
        campaign_id="c1",
        branch_id="c1:main",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["source"] == "library-snapshot"
    assert resolved["body"] == "v1"


async def test_track_latest_skips_snapshots(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Live")
    await _seed_library(store)

    await store.upsert_world_ref(
        campaign_id="c1",
        world_id="wod-london",
        priority=1,
        include=["character"],
        track_latest=True,
    )

    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred"},
        body="v2",
        source="user",
    )

    resolved = await store.resolve_entity(
        campaign_id="c1",
        branch_id="c1:main",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["source"] == "library-live"
    assert resolved["body"] == "v2"


async def test_upgrade_world_ref_refreshes_snapshots(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Pinned upgrade")
    await _seed_library(store)
    await store.upsert_world_ref(
        campaign_id="c1",
        world_id="wod-london",
        priority=1,
        include=["character"],
        track_latest=False,
    )

    # Bump the library version.
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred", "tags": ["new"]},
        body="v2",
        source="user",
    )

    report = await store.upgrade_world_ref(campaign_id="c1", world_id="wod-london")
    assert "worlds/wod-london/characters/winifred" in report.diff
    diff = report.diff["worlds/wod-london/characters/winifred"]
    assert diff["before"] == 1
    assert diff["after"] == 2

    resolved = await store.resolve_entity(
        campaign_id="c1",
        branch_id="c1:main",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["body"] == "v2"
