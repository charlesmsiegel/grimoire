"""Proleptic Julian engine.

Same month structure as Gregorian but the leap rule is "every 4 years"
without the 100/400 correction. Drifts from Gregorian by ~3 days per 400
years; used for historical European dates pre-1582 and modern Orthodox
liturgical observance.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import MONTH_NAMES


def is_julian_leap(year: int) -> bool:
    return year % 4 == 0


def julian_to_jdn(year: int, month: int, day: int) -> int:
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083


def julian_from_jdn(jdn: int) -> tuple[int, int, int]:
    c = jdn + 32082
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = d - 4800 + m // 10
    return year, month, day


class JulianEngine(CalendarEngine):
    system = "julian"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return julian_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = julian_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{MONTH_NAMES[parts.month - 1]} {parts.day}, {parts.year} (Julian)"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
