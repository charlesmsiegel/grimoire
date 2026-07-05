"""Calendar of Harptos (Forgotten Realms): 12 months x 30 days with five 1-day
festivals between months, plus Shieldmeet after Midsummer when DR % 4 == 0.
Pure arithmetic; epoch 1 Hammer 1 DR = fixed day 1 (internal, primaries-only).
Native format: 1492-Mirtul-05; festivals are day 01 (1492-Midsummer-01)."""

from __future__ import annotations

from .base import CalendarError, CalendarProvider, register
from .harptos_years import YEAR_NAMES

# (key, display name, days). Stable month index = 1-based position in THIS
# list; Shieldmeet always owns slot 11 (absent from common years), so indices
# never shift and default age/is_anniversary stay correct.
_MONTHS = [
    ("Hammer", "Hammer", 30),
    ("Midwinter", "Midwinter", 1),
    ("Alturiak", "Alturiak", 30),
    ("Ches", "Ches", 30),
    ("Tarsakh", "Tarsakh", 30),
    ("Greengrass", "Greengrass", 1),
    ("Mirtul", "Mirtul", 30),
    ("Kythorn", "Kythorn", 30),
    ("Flamerule", "Flamerule", 30),
    ("Midsummer", "Midsummer", 1),
    ("Shieldmeet", "Shieldmeet", 1),
    ("Eleasis", "Eleasis", 30),
    ("Eleint", "Eleint", 30),
    ("Highharvestide", "Highharvestide", 1),
    ("Marpenoth", "Marpenoth", 30),
    ("Uktar", "Uktar", 30),
    ("FeastOfTheMoon", "Feast of the Moon", 1),
    ("Nightal", "Nightal", 30),
]
_ORDINALS = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"]


def _is_leap(year: int) -> bool:
    return year % 4 == 0


def _year_entries(year: int) -> list[tuple[int, str, str, int]]:
    """(stable_index, key, name, days) for the year, in calendar order."""
    return [(i, k, n, d) for i, (k, n, d) in enumerate(_MONTHS, 1)
            if k != "Shieldmeet" or _is_leap(year)]


def _days_before_year(year: int) -> int:
    # 365 per year plus one Shieldmeet per DR % 4 == 0 year; Python floor
    # division keeps this exact for zero and negative years.
    return 365 * (year - 1) + (year - 1) // 4


class HarptosProvider(CalendarProvider):
    RULE_REFERENCE_YEAR = 4  # leap, so Shieldmeet rules validate

    def __init__(self, config: dict):
        self.region = config.get("region", "")          # unused
        self.custom_holidays = config.get("custom_holidays", []) or []
        self.anchor = config.get("anchor")               # primaries-only — ignored

    def parse(self, native: str) -> int:
        parts = str(native).rsplit("-", 2)
        if len(parts) != 3:
            raise CalendarError(f"bad harptos date: {native!r}")
        y_str, token, d_str = parts
        try:
            y, d = int(y_str), int(d_str)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad harptos date: {native!r}") from e
        offset = 0
        for _i, key, _name, days in _year_entries(y):
            if key.lower() == token.lower():
                if not 1 <= d <= days:
                    raise CalendarError(f"harptos day out of range: {native!r}")
                return _days_before_year(y) + offset + d
            offset += days
        raise CalendarError(f"unknown harptos month: {native!r}")

    def _locate(self, fixed: int) -> tuple[int, tuple[int, str, str, int], int]:
        y = fixed // 366  # underestimate; walk up to the right year
        while _days_before_year(y + 1) < fixed:
            y += 1
        rem = fixed - _days_before_year(y)
        for entry in _year_entries(y):
            if rem <= entry[3]:
                return y, entry, rem
            rem -= entry[3]
        raise CalendarError(f"fixed day out of range: {fixed}")  # unreachable

    def format(self, fixed: int) -> str:
        y, (_i, key, _name, _days), d = self._locate(fixed)
        return f"{y}-{key}-{d:02d}"

    def describe(self, fixed: int) -> dict:
        y, (idx, _key, name, days), d = self._locate(fixed)
        if days == 1:  # festival
            friendly, weekday_name, weekday_index = f"{name}, {y} DR", "festival day", None
        else:
            pos = (d - 1) % 10
            friendly = f"{d} {name}, {y} DR"
            weekday_name, weekday_index = f"{_ORDINALS[pos]} day of the tenday", pos
        year_name = YEAR_NAMES.get(y)
        if year_name:
            friendly += f" ({year_name})"
        return {"year": y, "month": idx, "month_name": name, "day": d,
                "weekday_name": weekday_name, "weekday_index": weekday_index,
                "friendly": friendly}

    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        return []  # Task 8

    def months(self, year: int) -> list[dict]:
        try:
            y = int(year)
        except (ValueError, TypeError) as e:
            raise CalendarError(f"bad harptos year: {year!r}") from e
        return [{"key": k, "name": n, "days": d} for _i, k, n, d in _year_entries(y)]


register("harptos", HarptosProvider)
