"""§10 Full LocationState get/update via apply_delta."""

from __future__ import annotations


async def _seed(store, library):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1",
        "location",
        "town",
        {"id": "town", "name": "Town", "kind": "city"},
        body="",
    )
    await store.upsert_world_ref(
        campaign_id="camp-1",
        world_id="w1",
        priority=1,
        include=None,
        track_latest=True,
        bound_at_version=None,
    )


async def test_get_location_state_empty_returns_default(store, library, world) -> None:
    await _seed(store, library)
    state = await world.get_location_state(
        "library:worlds/w1/locations/town",
        campaign_id="camp-1",
    )
    assert state.condition == ""
    assert state.occupants == []
    assert state.weather is None


async def test_update_location_state_round_trips(store, library, world) -> None:
    await _seed(store, library)
    updated = await world.update_location_state(
        "library:worlds/w1/locations/town",
        campaign_id="camp-1",
        patch={"condition": "ransacked", "transient_features": ["broken table"]},
        source="user",
        turn_id="t1",
    )
    assert updated.condition == "ransacked"
    assert updated.transient_features == ["broken table"]
    re_read = await world.get_location_state(
        "library:worlds/w1/locations/town",
        campaign_id="camp-1",
    )
    assert re_read.condition == "ransacked"


async def test_update_location_state_records_delta(store, library, world) -> None:
    await _seed(store, library)
    await world.update_location_state(
        "library:worlds/w1/locations/town",
        campaign_id="camp-1",
        patch={"condition": "burning"},
        source="user",
        turn_id="t1",
    )
    rows = await store.db.fetchall(
        "SELECT * FROM deltas WHERE kind = 'location_state_update' AND campaign_id = ?",
        ("camp-1",),
    )
    assert len(rows) >= 1
