"""Hebrew calendar provider backed by pyluach (exact lunisolar arithmetic and
the traditional holiday cycle). Native format: 5786-Kislev-25. The config
`region` field selects observance: "IL" = Israel, anything else = diaspora."""

from __future__ import annotations

from datetime import date

from pyluach import dates as _pd, hebrewcal as _pc

from .base import CalendarError, CalendarProvider, register

# (token, display name, pyluach month number, in leap years, in common years)
_MONTHS = [
    ("Tishrei", "Tishrei", 7, True, True),
    ("Cheshvan", "Cheshvan", 8, True, True),
    ("Kislev", "Kislev", 9, True, True),
    ("Tevet", "Tevet", 10, True, True),
    ("Shevat", "Shevat", 11, True, True),
    ("Adar", "Adar", 12, False, True),
    ("Adar1", "Adar I", 12, True, False),
    ("Adar2", "Adar II", 13, True, False),
    ("Nisan", "Nisan", 1, True, True),
    ("Iyar", "Iyar", 2, True, True),
    ("Sivan", "Sivan", 3, True, True),
    ("Tammuz", "Tammuz", 4, True, True),
    ("Av", "Av", 5, True, True),
    ("Elul", "Elul", 6, True, True),
]
_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Shabbat"]


def _is_leap(year: int) -> bool:
    return _pc.Year(year).leap


def _year_months(year: int) -> list[tuple[str, str, int]]:
    leap = _is_leap(year)
    return [(t, disp, num) for t, disp, num, in_leap, in_common in _MONTHS
            if (in_leap if leap else in_common)]


class HebrewProvider(CalendarProvider):
    def __init__(self, config: dict):
        self.region = config.get("region", "")
        self.custom_holidays = config.get("custom_holidays", []) or []
        self.anchor = config.get("anchor")  # canonical calendar — anchor is ignored

    def parse(self, native: str) -> int:
        parts = str(native).rsplit("-", 2)
        if len(parts) != 3:
            raise CalendarError(f"bad hebrew date: {native!r}")
        y_str, token, d_str = parts
        try:
            y, d = int(y_str), int(d_str)
            leap = _is_leap(y)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad hebrew date: {native!r}") from e
        tok = token.lower()
        if tok == "adar" and leap:
            tok = "adar2"  # plain Adar in a leap year = the observance month
        entry = next((e for e in _year_months(y) if e[0].lower() == tok), None)
        if entry is None:
            raise CalendarError(f"unknown hebrew month for {y}: {token!r}")
        try:
            return _pd.HebrewDate(y, entry[2], d).to_pydate().toordinal()
        except ValueError as e:
            raise CalendarError(f"bad hebrew date: {native!r}") from e

    def format(self, fixed: int) -> str:
        h = _pd.HebrewDate.from_pydate(date.fromordinal(fixed))
        token = next(e[0] for e in _year_months(h.year) if e[2] == h.month)
        return f"{h.year}-{token}-{h.day:02d}"

    def describe(self, fixed: int) -> dict:
        h = _pd.HebrewDate.from_pydate(date.fromordinal(fixed))
        ms = _year_months(h.year)
        pos = next(i for i, e in enumerate(ms, 1) if e[2] == h.month)
        _token, disp, _num = ms[pos - 1]
        widx = (date.fromordinal(fixed).weekday() + 1) % 7  # Sunday=0 … Shabbat=6
        return {"year": h.year, "month": pos, "month_name": disp, "day": h.day,
                "weekday_name": _WEEKDAYS[widx], "weekday_index": widx,
                "friendly": f"{h.day} {disp} {h.year}"}

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        return []  # Task 5

    def months(self, year: int) -> list[dict]:
        try:
            y = int(year)
            ms = _year_months(y)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad hebrew year: {year!r}") from e
        return [{"key": token, "name": disp,
                 "days": len(list(_pc.Month(y, num).iterdates()))}
                for token, disp, num in ms]


register("hebrew", HebrewProvider)
