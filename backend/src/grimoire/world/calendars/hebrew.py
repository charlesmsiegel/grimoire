"""Hebrew calendar engine.

Implements the standard fixed-arithmetic Hebrew calendar (post-Hillel II).
Year 1 begins at Tishri 1, AM 1, which is JDN 347997 (= -3760 Sept 7
proleptic Gregorian).

Months in a common year (12 months, ~354 days):
  1 Tishri, 2 Cheshvan, 3 Kislev, 4 Tevet, 5 Shevat, 6 Adar,
  7 Nisan, 8 Iyar, 9 Sivan, 10 Tammuz, 11 Av, 12 Elul.

Leap years (7 out of every 19, the Metonic cycle) have 13 months:
Adar I is inserted at position 6 and Adar (now Adar II) shifts to 7.
A few months have variable length (Cheshvan 29/30, Kislev 29/30) so the
total year length can be 353/354/355 (common) or 383/384/385 (leap).

Numbering convention here: months are numbered as they appear in the
civil year starting from Tishri (1..12 or 1..13). This matches the
display most users expect.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts

HEBREW_EPOCH = 347997  # JDN of 1 Tishri AM 1

MONTH_NAMES_COMMON = [
    "Tishri",
    "Cheshvan",
    "Kislev",
    "Tevet",
    "Shevat",
    "Adar",
    "Nisan",
    "Iyar",
    "Sivan",
    "Tammuz",
    "Av",
    "Elul",
]
MONTH_NAMES_LEAP = [
    "Tishri",
    "Cheshvan",
    "Kislev",
    "Tevet",
    "Shevat",
    "Adar I",
    "Adar II",
    "Nisan",
    "Iyar",
    "Sivan",
    "Tammuz",
    "Av",
    "Elul",
]


def is_hebrew_leap(year: int) -> bool:
    return ((7 * year) + 1) % 19 < 7


def _elapsed_months(year: int) -> int:
    months = 235 * ((year - 1) // 19) + 12 * ((year - 1) % 19)
    leap_years_in_partial_cycle = (((year - 1) % 19) * 7 + 1) // 19
    return months + leap_years_in_partial_cycle


def _elapsed_days(year: int) -> int:
    """Days from epoch (1 Tishri AM 1) to 1 Tishri of `year`."""
    months_elapsed = _elapsed_months(year)
    # Conjunctions: each lunation = 29 days, 12 hours, 793/1080 parts.
    parts_elapsed = 204 + 793 * (months_elapsed % 1080)
    hours_elapsed = 5 + 12 * months_elapsed + 793 * (months_elapsed // 1080) + parts_elapsed // 1080
    parts_elapsed = parts_elapsed % 1080
    day = 1 + 29 * months_elapsed + hours_elapsed // 24

    # Dehiyyot (postponement rules).
    if (
        parts_elapsed >= 19440
        or (day % 7 == 2 and parts_elapsed >= 9924 and not is_hebrew_leap(year))
        or (day % 7 == 1 and parts_elapsed >= 16789 and is_hebrew_leap(year - 1))
    ):
        day += 1
    # Rosh Hashanah can't fall on Sunday, Wednesday, or Friday.
    if day % 7 in (0, 3, 5):
        day += 1
    return day


def hebrew_year_length(year: int) -> int:
    return _elapsed_days(year + 1) - _elapsed_days(year)


def _month_lengths(year: int) -> list[int]:
    leap = is_hebrew_leap(year)
    year_len = hebrew_year_length(year)
    # Determine Cheshvan & Kislev lengths from year length.
    # Defective: 353/383 (Cheshvan=29, Kislev=29)
    # Regular:   354/384 (Cheshvan=29, Kislev=30)
    # Complete:  355/385 (Cheshvan=30, Kislev=30)
    cheshvan = 30 if year_len in (355, 385) else 29
    kislev = 29 if year_len in (353, 383) else 30
    if leap:
        # Tishri, Cheshvan, Kislev, Tevet, Shevat, Adar I, Adar II,
        # Nisan, Iyar, Sivan, Tammuz, Av, Elul
        return [30, cheshvan, kislev, 29, 30, 30, 29, 30, 29, 30, 29, 30, 29]
    return [30, cheshvan, kislev, 29, 30, 29, 30, 29, 30, 29, 30, 29]


def hebrew_to_jdn(year: int, month: int, day: int) -> int:
    days_in_months = _month_lengths(year)
    total = day - 1
    for i in range(month - 1):
        total += days_in_months[i]
    return HEBREW_EPOCH + _elapsed_days(year) - 1 + total


def hebrew_from_jdn(jdn: int) -> tuple[int, int, int]:
    # Approximate year then refine.
    days_since_epoch = jdn - HEBREW_EPOCH + 1
    year = max(1, (days_since_epoch * 98496) // 35975351 + 1)
    while _elapsed_days(year + 1) <= days_since_epoch:
        year += 1
    while _elapsed_days(year) > days_since_epoch:
        year -= 1

    day_of_year = days_since_epoch - _elapsed_days(year)
    months = _month_lengths(year)
    month = 1
    while month <= len(months) and day_of_year >= months[month - 1]:
        day_of_year -= months[month - 1]
        month += 1
    day = day_of_year + 1
    return year, month, day


class HebrewEngine(CalendarEngine):
    system = "hebrew"

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return hebrew_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = hebrew_from_jdn(jdn)
        return DateParts(year=y, month=m, day=d)

    def format(self, parts: DateParts) -> str:
        names = MONTH_NAMES_LEAP if is_hebrew_leap(parts.year) else MONTH_NAMES_COMMON
        return f"{parts.day} {names[parts.month - 1]} {parts.year} AM"

    def month_name(self, parts: DateParts) -> str:
        names = MONTH_NAMES_LEAP if is_hebrew_leap(parts.year) else MONTH_NAMES_COMMON
        return names[parts.month - 1]
