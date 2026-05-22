"""Shared engine protocol for calendar systems.

Each calendar system implements a CalendarEngine that converts between
its native (year, month, day) triple and a Julian Day Number. JDN is the
lingua franca that lets us reconcile any two calendars: a date in one is
converted to JDN and back to the other's representation.

Stardate is the one exception — it isn't a day-based calendar, so its
engine returns a float and only operates on Gregorian-equivalent days.
"""

from __future__ import annotations

from dataclasses import dataclass

# Weekday names indexed 0=Monday..6=Sunday (ISO).
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def jdn_weekday(jdn: int) -> int:
    """Return weekday for a JDN as 0=Monday..6=Sunday."""
    return (jdn + 0) % 7  # JDN 0 was a Monday


def jdn_weekday_name(jdn: int) -> str:
    return WEEKDAY_NAMES[jdn_weekday(jdn)]


@dataclass(frozen=True)
class DateParts:
    """Native date representation for a calendar."""

    year: int
    month: int
    day: int
    era: str = ""


class CalendarEngine:
    """Abstract interface every calendar engine implements."""

    system: str = ""

    def to_jdn(self, year: int, month: int, day: int) -> int:
        raise NotImplementedError

    def from_jdn(self, jdn: int) -> DateParts:
        raise NotImplementedError

    def format(self, parts: DateParts) -> str:
        return f"{parts.year:04d}-{parts.month:02d}-{parts.day:02d}"

    def month_name(self, parts: DateParts) -> str:
        return f"M{parts.month}"

    def year_length_days(self, year: int) -> int:
        """Return total days in a year (used for stardate-style computations)."""
        next_year_start = self.to_jdn(year + 1, 1, 1)
        this_year_start = self.to_jdn(year, 1, 1)
        return next_year_start - this_year_start
