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

    _MAX_DAYS = {"Tishrei": 30, "Cheshvan": 30, "Kislev": 30, "Tevet": 29,
                 "Shevat": 30, "Adar": 29, "Adar1": 30, "Adar2": 29, "Nisan": 30,
                 "Iyar": 29, "Sivan": 30, "Tammuz": 29, "Av": 30, "Elul": 29}

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        israel = self.region == "IL"
        out: list[dict] = []
        for f in range(start_fixed, end_fixed + 1):
            h = _pd.HebrewDate.from_pydate(date.fromordinal(f))
            for name in (h.festival(israel=israel, include_working_days=True),
                         h.fast_day()):
                if name:
                    out.append({"name": name, "fixed": f})
        out.extend(self._custom_fixed(start_fixed, end_fixed))
        out.sort(key=lambda h: h["fixed"])
        return out

    def _anniversary_fixed(self, birth_fixed: int, asof_year: int) -> int:
        """The fixed day the birth date is observed in asof_year (Adar folding,
        day-30 births observed on the 29th when the month is short)."""
        b = _pd.HebrewDate.from_pydate(date.fromordinal(birth_fixed))
        token = next(e[0] for e in _year_months(b.year) if e[2] == b.month)
        if _is_leap(asof_year):
            if token == "Adar":
                token = "Adar2"
        elif token in ("Adar1", "Adar2"):
            token = "Adar"
        num = next(e[2] for e in _year_months(asof_year) if e[0] == token)
        try:
            return _pd.HebrewDate(asof_year, num, b.day).to_pydate().toordinal()
        except ValueError:
            return _pd.HebrewDate(asof_year, num, 29).to_pydate().toordinal()

    def age(self, birth_fixed: int, asof_fixed: int) -> int:
        b_year = _pd.HebrewDate.from_pydate(date.fromordinal(birth_fixed)).year
        a_year = _pd.HebrewDate.from_pydate(date.fromordinal(asof_fixed)).year
        years = a_year - b_year
        if self._anniversary_fixed(birth_fixed, a_year) > asof_fixed:
            years -= 1
        return years

    def is_anniversary(self, birth_fixed: int, asof_fixed: int) -> bool:
        a_year = _pd.HebrewDate.from_pydate(date.fromordinal(asof_fixed)).year
        return self._anniversary_fixed(birth_fixed, a_year) == asof_fixed

    def validate_rule(self, rule: dict) -> None:
        if "day" not in rule:
            raise CalendarError(
                f"the hebrew calendar supports only fixed {{month, day}} custom holidays: {rule!r}")
        if not rule.get("name"):
            raise CalendarError(f"custom holiday needs a name: {rule!r}")
        token = next((t for t in self._MAX_DAYS
                      if t.lower() == str(rule.get("month", "")).lower()), None)
        if token is None:
            raise CalendarError(f"custom holiday month is unknown: {rule!r}")
        try:
            day = int(rule["day"])
        except (ValueError, TypeError) as e:
            raise CalendarError(f"custom holiday day is malformed: {rule!r}") from e
        if not (1 <= day <= self._MAX_DAYS[token]):
            raise CalendarError(f"custom holiday day out of range: {rule!r}")

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
