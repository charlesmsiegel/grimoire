"""The Gregorian provider: Python `datetime`-backed, so leap years and weekdays
are exact. Holidays come in Task 2."""

from __future__ import annotations

import calendar as _cal
from datetime import date

from .base import CalendarError, CalendarProvider, register


class GregorianProvider(CalendarProvider):
    def __init__(self, config: dict):
        self.region = config.get("region", "US")
        self.custom_holidays = config.get("custom_holidays", []) or []
        self.anchor = config.get("anchor")  # canonical calendar — anchor is ignored

    def parse(self, native: str) -> int:
        try:
            y, m, d = (int(x) for x in native.split("-"))
            return date(y, m, d).toordinal()
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad gregorian date: {native!r}") from e

    def format(self, fixed: int) -> str:
        return date.fromordinal(fixed).isoformat()

    def describe(self, fixed: int) -> dict:
        d = date.fromordinal(fixed)
        return {
            "year": d.year, "month": d.month, "month_name": _cal.month_name[d.month],
            "day": d.day, "weekday_name": _cal.day_name[d.weekday()],
            "weekday_index": d.weekday(),
            "friendly": f"{d.day} {_cal.month_name[d.month]} {d.year}",
        }


register("gregorian", GregorianProvider)
