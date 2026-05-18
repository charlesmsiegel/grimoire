"""§7 (option b) LocationConnection.to accepts entity refs across worlds."""

from __future__ import annotations


async def _seed_two_worlds(store, library) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_world("w2", {"id": "w2", "name": "W2"})
    await library.create_entity(
        "w1",
        "location",
        "town",
        {
            "id": "town",
            "name": "Town",
            "kind": "city",
            "connections": [
                # Same-world bare asset_id (legacy form).
                {"to": "tavern", "via": "street", "duration_min": 5},
                # Cross-world entity ref (new form).
                {
                    "to": "library:worlds/w2/locations/portal",
                    "via": "portal",
                    "duration_min": 1,
                },
            ],
        },
        body="",
    )
    await library.create_entity(
        "w1",
        "location",
        "tavern",
        {"id": "tavern", "name": "Tavern", "kind": "building"},
        body="",
    )
    await library.create_entity(
        "w2",
        "location",
        "portal",
        {"id": "portal", "name": "Portal", "kind": "other"},
        body="",
    )
    for wid in ("w1", "w2"):
        await store.upsert_world_ref(
            campaign_id="camp-1",
            world_id=wid,
            priority=1,
            include=None,
            track_latest=True,
            bound_at_version=None,
        )


async def test_adjacent_locations_resolves_same_world_asset_id(store, library, world) -> None:
    await _seed_two_worlds(store, library)
    out = await world.adjacent_locations("library:worlds/w1/locations/town", campaign_id="camp-1")
    ids = {loc.id for loc in out}
    assert "tavern" in ids


async def test_adjacent_locations_resolves_cross_world_ref(store, library, world) -> None:
    await _seed_two_worlds(store, library)
    out = await world.adjacent_locations("library:worlds/w1/locations/town", campaign_id="camp-1")
    ids = {loc.id for loc in out}
    assert "portal" in ids


async def test_adjacent_locations_no_campaign_still_resolves_cross_world_ref(
    store, library, world
) -> None:
    """Without a campaign_id, single-world callers can still follow refs."""
    await _seed_two_worlds(store, library)
    out = await world.adjacent_locations("library:worlds/w1/locations/town")
    ids = {loc.id for loc in out}
    assert ids == {"tavern", "portal"}


async def test_path_between_works_with_refs(store, library, world) -> None:
    await _seed_two_worlds(store, library)
    path = await world.path_between(
        "library:worlds/w1/locations/town",
        "library:worlds/w1/locations/tavern",
        campaign_id="camp-1",
    )
    assert len(path) == 1
    assert path[0].to == "tavern"


async def test_path_between_traverses_cross_world(store, library, world) -> None:
    await _seed_two_worlds(store, library)
    path = await world.path_between(
        "library:worlds/w1/locations/town",
        "library:worlds/w2/locations/portal",
        campaign_id="camp-1",
    )
    assert len(path) == 1
    assert path[0].to == "library:worlds/w2/locations/portal"


async def test_adjacent_unparseable_ref_returns_empty(store, library, world) -> None:
    await _seed_two_worlds(store, library)
    out = await world.adjacent_locations("not-a-ref", campaign_id="camp-1")
    assert out == []


async def test_adjacent_skips_world_outside_composition(store, library, world) -> None:
    """Cross-world ref to a world not in composition: skipped."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_world("w2", {"id": "w2", "name": "W2"})
    await library.create_entity(
        "w1",
        "location",
        "town",
        {
            "id": "town",
            "name": "Town",
            "connections": [{"to": "library:worlds/w2/locations/portal", "via": "portal"}],
        },
        body="",
    )
    await library.create_entity(
        "w2",
        "location",
        "portal",
        {"id": "portal", "name": "Portal"},
        body="",
    )
    # Only w1 in composition.
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w1",
        priority=1,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )
    out = await world.adjacent_locations("library:worlds/w1/locations/town", campaign_id="camp-1")
    ids = {loc.id for loc in out}
    assert "portal" not in ids


async def test_locations_within_refuses_outside_composition(store, library, world) -> None:
    """parent_ref naming a world not in the campaign's composition: empty."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity("w1", "location", "city", {"id": "city", "name": "City"}, body="")
    await library.create_entity(
        "w1",
        "location",
        "ward",
        {"id": "ward", "name": "Ward", "parent_id": "city"},
        body="",
    )
    # No world_ref for w1 in camp-1.
    out = await world.locations_within("library:worlds/w1/locations/city", campaign_id="camp-1")
    assert out == []
