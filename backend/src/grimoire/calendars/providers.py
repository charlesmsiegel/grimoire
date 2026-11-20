"""055 calendar notation over V1's fixed-day axis, without V1 stores or executable plugins.

Only Gregorian and Hebrew are built in. Custom month definitions are portable caller-owned
values, pinned by the campaign; native year 1, first month, day 1 maps to ``epoch_fixed``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pyluach import dates, hebrewcal

MAX_DAY = date.max.toordinal()
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_HEBREW = (("Tishrei", 7), ("Cheshvan", 8), ("Kislev", 9), ("Tevet", 10),
           ("Shevat", 11), ("Adar", 12), ("Nisan", 1), ("Iyar", 2),
           ("Sivan", 3), ("Tammuz", 4), ("Av", 5), ("Elul", 6))


class CalendarError(ValueError):
    """A refused calendar operation, with a stable machine-readable ``reason``."""

    def __init__(self, message: str, reason: str = "invalid_field") -> None:
        super().__init__(message)
        self.reason = reason


def integer(value: object, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise CalendarError(f"{label} must be an integer in {low}..{high}")
    return value


def text(value: object, label: str, *, empty: bool = False, maximum: int = 1000) -> str:
    if type(value) is not str or len(value) > maximum or (not empty and not value.strip()):
        raise CalendarError(f"{label} must be {'possibly empty ' if empty else 'nonempty '}text, at most {maximum} characters")
    return value


def _hebrew_months(year: int) -> tuple[tuple[str, int], ...]:
    if hebrewcal.Year(year).leap:
        return _HEBREW[:5] + (("Adar1", 12), ("Adar2", 13)) + _HEBREW[6:]
    return _HEBREW


@dataclass(frozen=True)
class Month:
    name: str
    days: int

    def __post_init__(self) -> None:
        if type(self.name) is not str or not _TOKEN.fullmatch(self.name):
            raise CalendarError("month name must be a portable ASCII token")
        integer(self.days, 1, MAX_DAY, "month days")


@dataclass(frozen=True)
class CalendarDefinition:
    provider: str = "gregorian"
    custom_id: str | None = None
    months: tuple[Month, ...] = ()
    epoch_fixed: int = 1

    def __post_init__(self) -> None:
        if type(self.provider) is not str or self.provider not in {"gregorian", "hebrew", "custom"}:
            raise CalendarError("unknown calendar provider")
        try:
            months = tuple(self.months)
        except TypeError:
            raise CalendarError("months must be a sequence") from None
        object.__setattr__(self, "months", months)
        integer(self.epoch_fixed, 1, MAX_DAY, "epoch_fixed")
        if self.provider != "custom":
            if months or self.custom_id is not None or self.epoch_fixed != 1:
                raise CalendarError("built-in calendars have no custom month or epoch settings")
        else:
            text(self.custom_id, "custom_id", maximum=128)
            if not 1 <= len(months) <= 128 or not all(type(m) is Month for m in months):
                raise CalendarError("a custom calendar needs 1..128 months")
            if len({m.name.casefold() for m in months}) != len(months):
                raise CalendarError("month names must be unique ignoring case")
            if sum(m.days for m in months) > MAX_DAY:
                raise CalendarError("custom year exceeds the fixed-day range")

    def parse(self, native: str) -> int:
        text(native, "native date", maximum=128)
        try:
            if self.provider == "gregorian":
                if not re.fullmatch(r"[0-9]{1,4}-[0-9]{1,2}-[0-9]{1,2}", native):
                    raise ValueError("expected YYYY-MM-DD")
                return date(*(int(piece) for piece in native.split("-"))).toordinal()
            match = re.fullmatch(r"([0-9]{1,7})-([A-Za-z][A-Za-z0-9_]*)-([0-9]{1,7})", native)
            if match is None:
                raise ValueError("expected year-month-day")
            year, token, day = int(match[1]), match[2], int(match[3])
            integer(year, 1, MAX_DAY, "year")
            if self.provider == "hebrew":
                months = _hebrew_months(year)
                if token.casefold() == "adar" and hebrewcal.Year(year).leap:
                    token = "Adar2"  # V1 compatibility: the observance month in a leap year
                number = next(number for name, number in months if name.casefold() == token.casefold())
                fixed = dates.HebrewDate(year, number, day).to_pydate().toordinal()
            else:
                index = next(i for i, month in enumerate(self.months) if month.name.casefold() == token.casefold())
                integer(day, 1, self.months[index].days, "day")
                fixed = self.epoch_fixed + (year - 1) * sum(m.days for m in self.months)
                fixed += sum(m.days for m in self.months[:index]) + day - 1
            return integer(fixed, 1, MAX_DAY, "fixed day")
        except (ValueError, OverflowError, StopIteration) as error:
            raise CalendarError(f"invalid {self.provider} date: {native!r}") from error

    def format(self, fixed: int) -> str:
        integer(fixed, 1, MAX_DAY, "fixed day")
        if self.provider == "gregorian":
            return date.fromordinal(fixed).isoformat()
        if self.provider == "hebrew":
            value = dates.HebrewDate.from_pydate(date.fromordinal(fixed))
            token = next(name for name, number in _hebrew_months(value.year) if number == value.month)
            return f"{value.year}-{token}-{value.day:02d}"
        if fixed < self.epoch_fixed:
            raise CalendarError("fixed day precedes the custom calendar epoch")
        year, day = divmod(fixed - self.epoch_fixed, sum(m.days for m in self.months))
        for month in self.months:
            if day < month.days:
                return f"{year + 1}-{month.name}-{day + 1:02d}"
            day -= month.days
        raise AssertionError("validated month lengths cover the year")
