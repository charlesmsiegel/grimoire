"""Standalone tests for the calendar parser + season/holiday helpers."""

from __future__ import annotations

from datetime import datetime

from grimoire.types.common import InGameTime
from grimoire.world.calendar import holiday_at, parse_calendar, season_for


def _when(year: int, month: int, day: int) -> InGameTime:
    return InGameTime(moment=datetime(year, month, day))


def test_parse_calendar_normalizes_shapes() -> None:
    cal = parse_calendar(
        "x",
        {
            "epoch": "2024-01-01",
            "months": [{"name": "Jan", "days": 31}, {"name": "Feb", "days": 28}],
            "days_per_week": 7,
            "week_day_names": ["Mon", "Tue"],
            "seasons": [
                {"name": "spring", "start_month": 3, "start_day": 1},
                {"name": "autumn", "start_month": 9, "start_day": 1},
            ],
            "holidays": [{"name": "NYE", "month": 12, "day": 31}],
        },
    )
    assert cal.world_id == "x"
    assert cal.epoch == datetime(2024, 1, 1)
    assert [m.name for m in cal.months] == ["Jan", "Feb"]
    assert {s.name for s in cal.seasons} == {"spring", "autumn"}
    assert cal.holidays[0].name == "NYE"


def test_season_for_wraps_to_last_when_before_first() -> None:
    cal = parse_calendar(
        "x",
        {
            "seasons": [
                {"name": "spring", "start_month": 3, "start_day": 1},
                {"name": "summer", "start_month": 6, "start_day": 1},
                {"name": "winter", "start_month": 12, "start_day": 1},
            ],
        },
    )
    # January is before the first start; wraps to "winter".
    s = season_for(cal, _when(2024, 1, 15))
    assert s is not None and s.name == "winter"

    # June 30 falls within summer.
    assert season_for(cal, _when(2024, 6, 30)).name == "summer"

    # November 1 still in summer's window per start-only semantics until winter.
    assert season_for(cal, _when(2024, 11, 1)).name == "summer"


def test_season_for_default_when_no_seasons() -> None:
    cal = parse_calendar("x", {})
    s = season_for(cal, _when(2024, 7, 15))
    assert s is not None
    assert s.name == "summer"  # hemisphere default


def test_holiday_at() -> None:
    cal = parse_calendar(
        "x",
        {"holidays": [{"name": "Hallows", "month": 10, "day": 31}]},
    )
    assert holiday_at(cal, _when(2024, 10, 31)).name == "Hallows"
    assert holiday_at(cal, _when(2024, 11, 1)) is None
