"""Bahá'í (Badí') calendar engine.

19 months of 19 days = 361 days, plus Ayyám-i-Há (intercalary days)
of 4 or 5 days before the final month ('Alá'). Year length: 365 or 366.

We use the algorithmic (pre-2015) form for simplicity: each year begins
on 21 March (Gregorian), Ayyám-i-Há is 4 or 5 days based on whether the
following Gregorian year is leap (a year-length match — close to but
not identical with the astronomically-fixed post-2015 form).

Months are indexed 1..19; Ayyám-i-Há is treated as a virtual "month 20"
of length 4 or 5; this matches some implementations and keeps the
arithmetic clean.

Epoch: 1 Bahá, BE 1 = 21 March 1844 CE = JDN 2394644.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import gregorian_from_jdn, gregorian_to_jdn, is_gregorian_leap

BAHAI_EPOCH = 2394644

MONTH_NAMES = [
    "Baha", "Jalal", "Jamal", "'Azamat", "Nur", "Rahmat",
    "Kalimat", "Kamal", "Asma'", "'Izzat", "Mashiyyat",
    "'Ilm", "Qudrat", "Qawl", "Masa'il", "Sharaf",
    "Sultan", "Mulk", "'Ayyam-i-Ha", "'Ala'",
]


def _ayyamiha_length(bahai_year: int) -> int:
    # The year that "wraps up" at end of 'Ala (month 19) is Gregorian
    # bahai_year + 1844. Ayyám-i-Há has 5 days if the Gregorian year just
    # after 21 March of bahai_year+1843 is a leap year.
    return 5 if is_gregorian_leap(bahai_year + 1844) else 4


def bahai_to_jdn(year: int, month: int, day: int) -> int:
    new_year_jdn = gregorian_to_jdn(year + 1843, 3, 21)
    if month <= 18:
        days_before = (month - 1) * 19
    elif month == 19:  # Ayyám-i-Há
        days_before = 18 * 19
    else:  # month == 20 → 'Alá'
        days_before = 18 * 19 + _ayyamiha_length(year)
    return new_year_jdn + days_before + day - 1


def bahai_from_jdn(jdn: int) -> tuple[int, int, int]:
    gy, _, _ = gregorian_from_jdn(jdn)
    ny_jdn = gregorian_to_jdn(gy, 3, 21)
    if jdn < ny_jdn:
        bahai_year = gy - 1844
        ny_jdn = gregorian_to_jdn(gy - 1, 3, 21)
    else:
        bahai_year = gy - 1843
    day_of_year = jdn - ny_jdn  # 0-indexed
    if day_of_year < 18 * 19:
        month = day_of_year // 19 + 1
        day = day_of_year % 19 + 1
    else:
        rem = day_of_year - 18 * 19
        ayyam = _ayyamiha_length(bahai_year)
        if rem < ayyam:
            month = 19  # Ayyám-i-Há
            day = rem + 1
        else:
            month = 20  # 'Alá'
            day = rem - ayyam + 1
    return bahai_year, month, day


class BahaiEngine(CalendarEngine):
    system = "bahai"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return bahai_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = bahai_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {MONTH_NAMES[parts.month - 1]} {parts.year} BE"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
