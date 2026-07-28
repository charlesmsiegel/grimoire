"""Where in its climate's year a block falls.

Seasons are declared by the *climate*, not the calendar, as fractions of the
year — so a monsoon climate can have two and a temperate one four, and a preset
drops into a 400-day homebrew calendar as cleanly as into a 365-day one.
"""

from __future__ import annotations

from ..calendars.base import CalendarProvider


def year_length(provider: CalendarProvider, year: int) -> int:
    return sum(m["days"] for m in provider.months(year))


def year_fraction(provider: CalendarProvider, fixed_day: int) -> float:
    """How far through its year ``fixed_day`` sits, in [0, 1)."""
    year = provider.describe(fixed_day)["year"]
    months = provider.months(year)
    first = provider.parse(f"{year}-{months[0]['key']}-01")
    return (fixed_day - first) / sum(m["days"] for m in months)


def _contains(season: dict, fraction: float) -> bool:
    start, end = float(season["from"]), float(season["to"])
    if start == end:
        return True  # a single season spanning the whole year
    if start < end:
        return start <= fraction < end
    return fraction >= start or fraction < end  # wraps the year end


def season_for(climate: dict, fraction: float) -> dict:
    """The season covering ``fraction``; first match in array order.

    Validation guarantees the seasons tile the year, so the final fallback is
    unreachable for a validated document — it exists so a resolver handed an
    unvalidated one still returns a season rather than raising into a turn.
    """
    for season in climate["seasons"]:
        if _contains(season, fraction):
            return season
    return climate["seasons"][0]
