"""Indian National (Saka) calendar.

12 months. Months 1-6 (Chaitra to Bhadra) have 31 days in a leap year
and 30 in a common year except Chaitra which is 30 in common, 31 in
leap. Months 7-12 (Asvina to Phalguna) all have 30 days.

Specifically: Chaitra 30/31, Vaisakha 31, Jyestha 31, Ashadha 31,
Shravana 31, Bhadra 31, Asvina 30, Kartika 30, Agrahayana 30, Pausha 30,
Magha 30, Phalguna 30.

A Saka year is leap when the corresponding Gregorian year (Saka + 78) is
leap. Chaitra 1 = March 22 in a Gregorian common year, March 21 in a
Gregorian leap year.

Epoch: Saka year 1, Chaitra 1 = 22 March 79 CE (Julian).
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import gregorian_from_jdn, gregorian_to_jdn, is_gregorian_leap

MONTH_NAMES = [
    "Chaitra", "Vaisakha", "Jyaistha", "Ashadha", "Shravana", "Bhadra",
    "Asvina", "Kartika", "Agrahayana", "Pausha", "Magha", "Phalguna",
]


def _gregorian_year_for_saka(saka_year: int) -> int:
    return saka_year + 78


def saka_to_jdn(year: int, month: int, day: int) -> int:
    greg_year = _gregorian_year_for_saka(year)
    leap = is_gregorian_leap(greg_year)
    chaitra_start_day = 21 if leap else 22  # March
    new_year_jdn = gregorian_to_jdn(greg_year, 3, chaitra_start_day)

    # Month lengths (1-indexed):
    chaitra_len = 31 if leap else 30
    month_lengths = [chaitra_len, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 30]
    days_before = sum(month_lengths[: month - 1])
    return new_year_jdn + days_before + day - 1


def saka_from_jdn(jdn: int) -> tuple[int, int, int]:
    # Saka new year falls in March of (saka + 78). Use Gregorian as anchor.
    gy, gm, gd = gregorian_from_jdn(jdn)
    leap = is_gregorian_leap(gy)
    chaitra_start = gregorian_to_jdn(gy, 3, 21 if leap else 22)
    if jdn >= chaitra_start:
        saka_year = gy - 78
        ny_jdn = chaitra_start
        leap_for_year = leap
    else:
        saka_year = gy - 79
        prev_leap = is_gregorian_leap(gy - 1)
        ny_jdn = gregorian_to_jdn(gy - 1, 3, 21 if prev_leap else 22)
        leap_for_year = prev_leap

    day_of_year = jdn - ny_jdn + 1
    chaitra_len = 31 if leap_for_year else 30
    month_lengths = [chaitra_len, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 30]
    month = 1
    while month <= 12 and day_of_year > month_lengths[month - 1]:
        day_of_year -= month_lengths[month - 1]
        month += 1
    return saka_year, month, day_of_year


class IndianSakaEngine(CalendarEngine):
    system = "indian_saka"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return saka_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = saka_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {MONTH_NAMES[parts.month - 1]} {parts.year} Saka"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
