"""Thai Buddhist Era calendar.

Same months/days as Gregorian (1941 reform aligned to Gregorian solar
year). Year is Gregorian + 543. So 1 Jan 2025 CE = 1 Jan 2568 BE.
"""

from __future__ import annotations

from .base import CalendarEngine, DateParts
from .gregorian import (
    MONTH_NAMES,
    gregorian_from_jdn,
    gregorian_to_jdn,
)

BUDDHIST_OFFSET = 543


class BuddhistEngine(CalendarEngine):
    system = "buddhist"

    def to_jdn(self, date: DateParts) -> int:
        return gregorian_to_jdn(date.year - BUDDHIST_OFFSET, date.month, date.day)

    def from_jdn(self, jdn: int) -> DateParts:
        gy, gm, gd = gregorian_from_jdn(jdn)
        return DateParts(year=gy + BUDDHIST_OFFSET, month=gm, day=gd)

    def format(self, parts: DateParts) -> str:
        return f"{parts.day} {MONTH_NAMES[parts.month - 1]} {parts.year} BE"

    def month_name(self, parts: DateParts) -> str:
        return MONTH_NAMES[parts.month - 1]
