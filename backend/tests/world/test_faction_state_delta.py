"""§11 update_faction_state records a delta and round-trips."""

from __future__ import annotations


async def _seed(store, library):
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    await library.create_world("w1", {"id": "w1", "name": "W1"})
    await library.create_entity(
        "w1",
        "faction",
        "guild",
        {"id": "guild", "name": "Guild"},
        body="",
    )


async def test_update_faction_state_records_delta(store, library, world) -> None:
    await _seed(store, library)
    await world.update_faction_state(
        faction_ref="library:worlds/w1/factions/guild",
        campaign_id="camp-1",
        patch={"current_focus": "recruiting"},
        source="user",
        turn_id="t1",
    )
    rows = await store.db.fetchall(
        "SELECT * FROM deltas WHERE kind = 'faction_state_update' AND campaign_id = ?",
        ("camp-1",),
    )
    assert len(rows) >= 1


async def test_update_faction_state_round_trips(store, library, world) -> None:
    await _seed(store, library)
    await world.update_faction_state(
        faction_ref="library:worlds/w1/factions/guild",
        campaign_id="camp-1",
        patch={"current_focus": "recruiting"},
        source="user",
        turn_id="t1",
    )
    state = await world.faction_state(
        faction_ref="library:worlds/w1/factions/guild",
        campaign_id="camp-1",
    )
    assert state.current_focus == "recruiting"
