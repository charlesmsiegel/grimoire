"""Calendar engine: a provider registry over a fixed-day integer axis.

Every date reduces to a fixed day (proleptic Gregorian ordinal, a Rata Die day
count) so dates from any calendar are orderable and arithmetic is exact. A
provider knows how to convert its own notation <-> fixed day, name the weekday,
and (in gregorian.py) list its holidays. Only `gregorian` ships today; future
calendars register here and need no changes elsewhere.

Time-of-day is handled by the agnostic helpers below (split/minutes/normalize),
not the providers — providers are purely calendrical (date-level).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CalendarError(Exception):
    pass


class CalendarProvider(ABC):
    @abstractmethod
    def parse(self, native: str) -> int:
        """Native date string (no time component) -> fixed day. Raise CalendarError on bad input."""

    @abstractmethod
    def format(self, fixed: int) -> str:
        """Fixed day -> canonical native date string (round-trips parse)."""

    @abstractmethod
    def describe(self, fixed: int) -> dict:
        """{year, month, month_name, day, weekday_name, weekday_index, friendly}."""

    # Age helpers default to fixed-day arithmetic via describe(); override if needed.
    def age(self, birth_fixed: int, asof_fixed: int) -> int:
        b, a = self.describe(birth_fixed), self.describe(asof_fixed)
        years = a["year"] - b["year"]
        if (a["month"], a["day"]) < (b["month"], b["day"]):
            years -= 1
        return years

    def is_anniversary(self, birth_fixed: int, asof_fixed: int) -> bool:
        b, a = self.describe(birth_fixed), self.describe(asof_fixed)
        return (a["month"], a["day"]) == (b["month"], b["day"])


REGISTRY: dict[str, type[CalendarProvider]] = {}


def register(provider_id: str, cls: type[CalendarProvider]) -> None:
    REGISTRY[provider_id] = cls


def get_provider(config: dict) -> CalendarProvider:
    cls = REGISTRY.get(config.get("provider", "gregorian"))
    if cls is None:
        raise CalendarError(f"unknown calendar provider: {config.get('provider')!r}")
    return cls(config)


# ---- time-of-day-aware, calendar-agnostic helpers ----

def split_native(native: str) -> tuple[str, str | None]:
    date_str, sep, time_str = native.partition("T")
    return date_str, (time_str if sep else None)


def minutes_of(native: str) -> int | None:
    _, time_str = split_native(native)
    if not time_str:
        return None
    try:
        hh, mm = time_str.split(":")
        h, m = int(hh), int(mm)
    except ValueError as e:
        raise CalendarError(f"bad time-of-day: {time_str!r}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise CalendarError(f"time-of-day out of range: {time_str!r}")
    return h * 60 + m


def fixed_of(provider: CalendarProvider, native: str) -> int:
    date_str, _ = split_native(native)
    return provider.parse(date_str)


def normalize(provider: CalendarProvider, native: str) -> str:
    """Canonicalize: validate date + optional time, return canonical date(+Thh:mm)."""
    date_str, time_str = split_native(native)
    canonical = provider.format(provider.parse(date_str))
    if time_str is not None:
        minutes_of(native)  # validates range, raises CalendarError
        return f"{canonical}T{time_str}"
    return canonical


def friendly(provider: CalendarProvider, native: str) -> str:
    return provider.describe(fixed_of(provider, native))["friendly"]
