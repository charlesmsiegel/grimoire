"""Proleptic Gregorian engine.

Uses the Fliegel-Van Flandern algorithm. Year 0 is supported (= 1 BC), so
this works as a proleptic calendar back to JDN ~0. Months and days are
1-indexed.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def is_gregorian_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def gregorian_to_jdn(year: int, month: int, day: int) -> int:
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def gregorian_from_jdn(jdn: int) -> tuple[int, int, int]:
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return year, month, day


def days_in_gregorian_month(year: int, month: int) -> int:
    if month == 2 and is_gregorian_leap(year):
        return 29
    return MONTH_DAYS[month - 1]


def gregorian_day_of_year(year: int, month: int, day: int) -> int:
    """1-indexed day of year."""
    total = day
    for m in range(1, month):
        total += days_in_gregorian_month(year, m)
    return total


class GregorianEngine(CalendarEngine):
    system = "gregorian"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return gregorian_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = gregorian_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{MONTH_NAMES[parts.month - 1]} {parts.day}, {parts.year}"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
