"""ISO 8601 week-date calendar.

Same days as proleptic Gregorian. Weeks start on Monday; week 1 of a
year is the week containing the first Thursday of that year. Years run
in week-counts of 52 or 53.

`year` in DateParts is the ISO week year. `month` is the ISO week
number (1..53). `day` is the ISO weekday (1=Monday..7=Sunday).
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import gregorian_from_jdn, gregorian_to_jdn


def _iso_week_one_monday_jdn(year: int) -> int:
    """JDN of Monday of week 1 of the given ISO year."""
    # Jan 4 is always in week 1 of its ISO year. So find the Monday on
    # or before Jan 4.
    jan4_jdn = gregorian_to_jdn(year, 1, 4)
    weekday = jan4_jdn % 7  # 0=Mon..6=Sun
    return jan4_jdn - weekday


def iso_to_jdn(year: int, week: int, weekday: int) -> int:
    if weekday < 1 or weekday > 7:
        raise ValueError(f"ISO weekday must be 1..7, got {weekday}")
    return _iso_week_one_monday_jdn(year) + (week - 1) * 7 + (weekday - 1)


def iso_from_jdn(jdn: int) -> tuple[int, int, int]:
    gy, _, _ = gregorian_from_jdn(jdn)
    # Try the obvious year, then ±1 if jdn falls outside its week-year span.
    for candidate in (gy + 1, gy, gy - 1):
        start = _iso_week_one_monday_jdn(candidate)
        end = _iso_week_one_monday_jdn(candidate + 1)
        if start <= jdn < end:
            week = (jdn - start) // 7 + 1
            weekday = (jdn - start) % 7 + 1
            return candidate, week, weekday
    raise ValueError(f"Failed to locate ISO week for JDN {jdn}")


class IsoWeekEngine(CalendarEngine):
    system = "iso_week"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        # Reuse the (year, week, weekday) triple as (year, month, day).
        return iso_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, w, d = iso_from_jdn(jdn)
        return DateParts(year=y, month=w, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.year:04d}-W{parts.month:02d}-{parts.day}"

    def month_name(self, parts: DateParts) -> str:
        return f"Week {parts.month}"
