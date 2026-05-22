"""Islamic (Hijri) calendar engine.

Implements the tabular Islamic calendar (Kuwaiti algorithm). 30-year
cycle with 11 leap years at positions 2, 5, 7, 10, 13, 16, 18, 21, 24,
26, 29. Months alternate 30/29 days; a leap year extends the 12th month
to 30. Year length: 354 or 355 days.

Epoch: 1 Muharram AH 1 = 16 July 622 CE (Julian) = JDN 1948440.

Note: This is the arithmetic version; observed-crescent variants in
Saudi Arabia/Iran may differ by a day. For game purposes the tabular
form is the right default because it's deterministic.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts

ISLAMIC_EPOCH = 1948440  # JDN of 1 Muharram AH 1 (Friday, Julian 16 July 622)

MONTH_NAMES = [
    "Muharram",
    "Safar",
    "Rabi al-Awwal",
    "Rabi al-Thani",
    "Jumada al-Awwal",
    "Jumada al-Thani",
    "Rajab",
    "Sha'ban",
    "Ramadan",
    "Shawwal",
    "Dhu al-Qi'dah",
    "Dhu al-Hijjah",
]

LEAP_OFFSETS_IN_CYCLE = {2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29}


def is_islamic_leap(year: int) -> bool:
    return (year % 30) in LEAP_OFFSETS_IN_CYCLE


def days_in_islamic_month(year: int, month: int) -> int:
    if month == 12 and is_islamic_leap(year):
        return 30
    return 30 if month % 2 == 1 else 29


def islamic_to_jdn(year: int, month: int, day: int) -> int:
    # Days before this month in the current year: cumulative sum of
    # alternating 30/29 = 29*(m-1) + m//2 (excluding any leap-day on Dhu
    # al-Hijjah; that's already accounted for in the year offset).
    days_before_month = 29 * (month - 1) + month // 2
    days_before_year = (year - 1) * 354 + (11 * year + 3) // 30
    return ISLAMIC_EPOCH + days_before_year + days_before_month + day - 1


def islamic_from_jdn(jdn: int) -> tuple[int, int, int]:
    days = jdn - ISLAMIC_EPOCH
    # Pre-epoch falls back to a coarse estimate; post-epoch uses the
    # standard 30-year cycle approximation.
    year = (days * 30) // 10631 - 1 if days < 0 else (30 * days + 10646) // 10631
    if year < 1:
        year = 1
    # Adjust if our estimate is off.
    while islamic_to_jdn(year, 1, 1) > jdn:
        year -= 1
    while islamic_to_jdn(year + 1, 1, 1) <= jdn:
        year += 1
    month = 1
    while month < 12 and islamic_to_jdn(year, month + 1, 1) <= jdn:
        month += 1
    day = jdn - islamic_to_jdn(year, month, 1) + 1
    return year, month, day


class IslamicEngine(CalendarEngine):
    system = "islamic"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return islamic_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = islamic_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {MONTH_NAMES[parts.month - 1]} {parts.year} AH"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
