"""Persian (Solar Hijri / Jalali) calendar engine.

Uses Birashk's algorithmic 2820-year cycle approximation. Months:
6 × 31 days (Farvardin..Shahrivar), 5 × 30 days (Mehr..Bahman),
and Esfand (29 or 30 in leap years).

Epoch: 1 Farvardin AP 1 = 19 March 622 CE (Julian) = JDN 1948321.

Year 1 Farvardin coincides with the vernal equinox; the algorithmic
form is accurate to within a day of the astronomical reality for the
2820-year span centered on the present.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts

PERSIAN_EPOCH = 1948321  # JDN of 1 Farvardin AP 1

MONTH_NAMES = [
    "Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand",
]


def is_persian_leap(year: int) -> bool:
    # Birashk's algorithm: 2820-year cycle with 683 leap years.
    # Year is leap iff ((year - 474) % 2820) + 474 ... applied via a
    # canonical reduction.
    epoch_base = year - (474 if year >= 0 else 473)
    cycle_year = (epoch_base % 2820) + 474
    return ((cycle_year + 38) * 31) % 128 < 31


def persian_to_jdn(year: int, month: int, day: int) -> int:
    epoch_base = year - (474 if year >= 0 else 473)
    cycle = epoch_base // 2820
    cycle_year = (epoch_base % 2820) + 474

    if month <= 7:
        days_before_month = (month - 1) * 31
    else:
        days_before_month = (month - 1) * 30 + 6

    return (
        day
        + days_before_month
        + ((cycle_year * 682) - 110) // 2816
        + (cycle_year - 1) * 365
        + cycle * 1029983
        + (PERSIAN_EPOCH - 1)
    )


def persian_from_jdn(jdn: int) -> tuple[int, int, int]:
    # Reverse via search around a coarse estimate.
    estimate_year = ((jdn - PERSIAN_EPOCH) * 33 // 12053) + 1
    if estimate_year < 1:
        estimate_year = 1
    while persian_to_jdn(estimate_year, 1, 1) > jdn:
        estimate_year -= 1
    while persian_to_jdn(estimate_year + 1, 1, 1) <= jdn:
        estimate_year += 1
    year = estimate_year

    day_of_year = jdn - persian_to_jdn(year, 1, 1) + 1
    if day_of_year <= 186:
        month = 1 + (day_of_year - 1) // 31
        day = (day_of_year - 1) % 31 + 1
    else:
        rem = day_of_year - 186
        month = 7 + (rem - 1) // 30
        day = (rem - 1) % 30 + 1
    return year, month, day


class PersianEngine(CalendarEngine):
    system = "persian"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return persian_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = persian_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {MONTH_NAMES[parts.month - 1]} {parts.year} AP"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
