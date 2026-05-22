"""Japanese era calendar.

Wraps Gregorian: same day count and month structure, but years are
named by reign era. We currently track post-Meiji eras (the Meiji
restoration adopted the Solar/Gregorian calendar in 1873; pre-Meiji
years used a different lunisolar system not covered here).
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import (
    MONTH_NAMES,
    GregorianEngine,
    gregorian_from_jdn,
    gregorian_to_jdn,
)

# (era_name, start_gregorian_year, start_gregorian_month, start_gregorian_day)
ERAS = [
    ("Reiwa", 2019, 5, 1),
    ("Heisei", 1989, 1, 8),
    ("Showa", 1926, 12, 25),
    ("Taisho", 1912, 7, 30),
    ("Meiji", 1868, 10, 23),
]


def era_for_gregorian(year: int, month: int, day: int) -> tuple[str, int]:
    """Return (era_name, era_year) for a Gregorian date."""
    for era_name, ey, em, ed in ERAS:
        if (year, month, day) >= (ey, em, ed):
            return era_name, year - ey + 1
    # Pre-Meiji — return Gregorian year unmodified with a marker era.
    return "Pre-Meiji", year


def gregorian_year_for_era(era_name: str, era_year: int) -> int:
    """Translate (era, era_year) back to absolute Gregorian year."""
    for name, ey, _, _ in ERAS:
        if name == era_name:
            return ey + era_year - 1
    return era_year  # Pre-Meiji passthrough


class JapaneseEraEngine(CalendarEngine):
    """Japanese era calendar (post-1873).

    Internal `year` stored in DateParts is the absolute Gregorian year so
    JDN conversion is symmetric; the era name + era-year are computed
    for display only and surface as DateParts.era (e.g. "Reiwa 6").
    """

    system = "japanese_era"

    def __init__(self) -> None:
        self._greg = GregorianEngine()

    def to_jdn(self, year: int, month: int, day: int) -> int:
        return gregorian_to_jdn(year, month, day)

    def from_jdn(self, jdn: int) -> DateParts:
        gy, gm, gd = gregorian_from_jdn(jdn)
        era, era_year = era_for_gregorian(gy, gm, gd)
        return DateParts(year=gy, month=gm, day=gd, era=f"{era} {era_year}")

    def format(self, parts: DateParts) -> str:
        return f"{parts.era}, {MONTH_NAMES[parts.month - 1]} {parts.day}"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
