"""§5 WorldService applies weather-override deltas."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grimoire.types.common import InGameTime, Scope
from grimoire.types.state import DeltaKind, StateDelta
from grimoire.types.world import Weather, WeatherKind


async def _seed_world_with_location(store, library):
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


async def test_apply_writes_override(store, library, world) -> None:
    await _seed_world_with_location(store, library)
    delta = StateDelta(
        kind=DeltaKind.OVERRIDE_WRITE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_table="location_state",
        target_id="library:worlds/w1/locations/town",
        after={
            "campaign_id": "camp-1",
            "branch_id": "camp-1:main",
            "weather": Weather(kind=WeatherKind.RAIN, source="override").model_dump(mode="json"),
        },
        confidence=0.9,
        source="extractor",
    )
    await world.apply_weather_override_delta(delta)
    w = await world.weather_for(
        "w1",
        "town",
        when=InGameTime(moment=datetime(2025, 1, 1, 12, 0, tzinfo=UTC)),
        campaign_id="camp-1",
    )
    assert w.kind == WeatherKind.RAIN
    assert w.source == "override"


async def test_apply_rejects_non_override_delta(store, library, world) -> None:
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_table="facts",
        target_id="x",
        after={},
        confidence=0.5,
        source="extractor",
    )
    with pytest.raises(ValueError, match="OVERRIDE_WRITE"):
        await world.apply_weather_override_delta(delta)


async def test_apply_rejects_wrong_target_table(store, library, world) -> None:
    delta = StateDelta(
        kind=DeltaKind.OVERRIDE_WRITE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_table="character_state",
        target_id="x",
        after={},
        confidence=0.5,
        source="extractor",
    )
    with pytest.raises(ValueError, match="location_state"):
        await world.apply_weather_override_delta(delta)


async def test_apply_rejects_unparseable_location_ref(store, library, world) -> None:
    delta = StateDelta(
        kind=DeltaKind.OVERRIDE_WRITE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_table="location_state",
        target_id="not-a-ref",
        after={
            "campaign_id": "camp-1",
            "branch_id": "camp-1:main",
            "weather": Weather(kind=WeatherKind.RAIN, source="override").model_dump(mode="json"),
        },
        confidence=0.5,
        source="extractor",
    )
    with pytest.raises(ValueError, match="unparseable"):
        await world.apply_weather_override_delta(delta)
