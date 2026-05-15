"""Standalone tests for the procedural weather generator."""

from __future__ import annotations

from datetime import datetime

from grimoire.types.common import InGameTime
from grimoire.types.world import WorldCalendar
from grimoire.world.weather import generate_weather


def _when(year: int, month: int, day: int, hour: int = 12) -> InGameTime:
    return InGameTime(moment=datetime(year, month, day, hour))


def test_same_inputs_same_output() -> None:
    cal = WorldCalendar(world_id="x")
    a = generate_weather(
        campaign_id="c1",
        location_ref="library:worlds/x/locations/y",
        when=_when(2024, 11, 1, 18),
        calendar=cal,
        climate_zone="temperate-oceanic",
    )
    b = generate_weather(
        campaign_id="c1",
        location_ref="library:worlds/x/locations/y",
        when=_when(2024, 11, 1, 18),
        calendar=cal,
        climate_zone="temperate-oceanic",
    )
    assert a == b


def test_different_hour_changes_outcome() -> None:
    cal = WorldCalendar(world_id="x")
    samples = {
        generate_weather(
            campaign_id="c1",
            location_ref="loc",
            when=_when(2024, 11, 1, h),
            calendar=cal,
            climate_zone=None,
        ).kind.value
        for h in range(0, 24)
    }
    assert len(samples) >= 2


def test_indoor_is_always_clear() -> None:
    cal = WorldCalendar(world_id="x")
    w = generate_weather(
        campaign_id="c1",
        location_ref="loc",
        when=_when(2024, 11, 1, 18),
        calendar=cal,
        climate_zone="arctic",
        indoor=True,
    )
    assert w.kind.value == "clear"
    assert w.summary == ""


def test_climate_zone_influences_distribution() -> None:
    cal = WorldCalendar(world_id="x")
    arctic_kinds = [
        generate_weather(
            campaign_id="c1",
            location_ref=f"loc{i}",
            when=_when(2024, 1, 15, 9),
            calendar=cal,
            climate_zone="arctic",
        ).kind.value
        for i in range(50)
    ]
    desert_kinds = [
        generate_weather(
            campaign_id="c1",
            location_ref=f"loc{i}",
            when=_when(2024, 7, 15, 12),
            calendar=cal,
            climate_zone="desert",
        ).kind.value
        for i in range(50)
    ]
    assert arctic_kinds.count("snow") + arctic_kinds.count("cold") > 5
    assert desert_kinds.count("heat") + desert_kinds.count("clear") > 25
