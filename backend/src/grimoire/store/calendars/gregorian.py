"""The Gregorian provider: Python `datetime`-backed, so leap years and weekdays
are exact. Holidays come in Task 2."""

from __future__ import annotations

import calendar as _cal
from datetime import date

import holidays as _holidays

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

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        out: list[dict] = []
        start, end = date.fromordinal(start_fixed), date.fromordinal(end_fixed)
        years = list(range(start.year, end.year + 1))
        if self.region:
            try:
                lib = _holidays.country_holidays(self.region, years=years)
            except NotImplementedError:
                lib = {}
            for d, name in lib.items():
                f = d.toordinal()
                if start_fixed <= f <= end_fixed:
                    out.append({"name": name, "fixed": f})
        for rule in self.custom_holidays:
            for y in years:
                d = _custom_date(rule, y)
                if d is None:
                    continue
                f = d.toordinal()
                if start_fixed <= f <= end_fixed:
                    out.append({"name": rule.get("name", ""), "fixed": f})
        out.sort(key=lambda h: h["fixed"])
        return out

    def months(self, year: int) -> list[dict]:
        try:
            y = int(year)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad gregorian year: {year!r}") from e
        return [{"key": f"{m:02d}", "name": _cal.month_name[m],
                 "days": _cal.monthrange(y, m)[1]} for m in range(1, 13)]

    def validate_rule(self, rule: dict) -> None:
        if "day" in rule:
            super().validate_rule(rule)
            return
        if not rule.get("name"):
            raise CalendarError(f"custom holiday needs a name: {rule!r}")
        try:
            month, nth, weekday = int(rule["month"]), int(rule["nth"]), int(rule["weekday"])
        except (KeyError, ValueError, TypeError) as e:
            raise CalendarError(f"custom holiday rule is malformed: {rule!r}") from e
        if not (1 <= month <= 12 and 1 <= nth <= 5 and 0 <= weekday <= 6):
            raise CalendarError(f"custom holiday rule is malformed: {rule!r}")


def _custom_date(rule: dict, year: int):
    """Resolve a custom-holiday rule to a date in `year`: fixed {month, day} or
    nth-weekday {month, nth, weekday} (weekday 0=Mon..6=Sun). None if malformed."""
    try:
        month = int(rule["month"])
        if "day" in rule:
            return date(year, month, int(rule["day"]))
        nth, weekday = int(rule["nth"]), int(rule["weekday"])
    except (KeyError, ValueError, TypeError):
        return None
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (nth - 1) * 7
    try:
        return date(year, month, day)
    except ValueError:
        return None


register("gregorian", GregorianProvider, "Gregorian")
