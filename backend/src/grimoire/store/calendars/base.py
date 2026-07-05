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

import re
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

    @abstractmethod
    def holidays(self, start_fixed: int, end_fixed: int) -> list[dict]:
        """Observances landing in [start_fixed, end_fixed], each {name, fixed}."""

    @abstractmethod
    def months(self, year: int) -> list[dict]:
        """The year's months in calendar order, each {key, name, days}.
        f"{year}-{key}-{day:02d}" must be a valid native date for 1 <= day <= days."""

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

    # Fixed {name, month, day} custom-holiday rules, validated against months().
    RULE_REFERENCE_YEAR = 2024  # a leap year, so Feb-29-style rules validate

    def _month_entry(self, key) -> dict | None:
        wanted = str(key)
        if wanted.isdigit():
            wanted = f"{int(wanted):02d}"  # legacy integer months (Gregorian)
        wanted = wanted.lower()
        for m in self.months(self.RULE_REFERENCE_YEAR):
            if m["key"].lower() == wanted:
                return m
        return None

    def validate_rule(self, rule: dict) -> None:
        """Raise CalendarError unless rule is a valid fixed {name, month, day} rule."""
        if not rule.get("name"):
            raise CalendarError(f"custom holiday needs a name: {rule!r}")
        if "day" not in rule:
            raise CalendarError(f"only fixed {{month, day}} custom holidays are supported: {rule!r}")
        try:
            day = int(rule["day"])
        except (ValueError, TypeError) as e:
            raise CalendarError(f"custom holiday day is malformed: {rule!r}") from e
        m = self._month_entry(rule.get("month"))
        if m is None:
            raise CalendarError(f"custom holiday month is unknown: {rule!r}")
        if not (1 <= day <= m["days"]):
            raise CalendarError(f"custom holiday day out of range: {rule!r}")

    def _custom_fixed(self, start_fixed: int, end_fixed: int) -> list[dict]:
        """Resolve this provider's fixed custom rules within a fixed-day range."""
        out: list[dict] = []
        y0 = self.describe(start_fixed)["year"]
        y1 = self.describe(end_fixed)["year"]
        for rule in getattr(self, "custom_holidays", []) or []:
            if "day" not in rule:
                continue
            for y in range(y0, y1 + 1):
                try:
                    f = self.parse(f"{y}-{rule['month']}-{int(rule['day']):02d}")
                except (CalendarError, KeyError, ValueError, TypeError):
                    continue
                if start_fixed <= f <= end_fixed:
                    out.append({"name": rule.get("name", ""), "fixed": f})
        return out


REGISTRY: dict[str, type[CalendarProvider]] = {}


def register(provider_id: str, cls: type[CalendarProvider]) -> None:
    REGISTRY[provider_id] = cls


def get_provider(config: dict) -> CalendarProvider:
    cls = REGISTRY.get(config.get("provider", "gregorian"))
    if cls is None:
        raise CalendarError(f"unknown calendar provider: {config.get('provider')!r}")
    return cls(config)


# ---- time-of-day-aware, calendar-agnostic helpers ----

_TIME_SUFFIX = re.compile(r"T(\d{1,2}:\d{1,2})$")


def split_native(native: str) -> tuple[str, str | None]:
    m = _TIME_SUFFIX.search(native)
    if m:
        return native[: m.start()], m.group(1)
    return native, None


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
        m = minutes_of(native)  # validates range, raises CalendarError
        return f"{canonical}T{m // 60:02d}:{m % 60:02d}"  # zero-pad for a stable key
    return canonical


def friendly(provider: CalendarProvider, native: str) -> str:
    return provider.describe(fixed_of(provider, native))["friendly"]


def age(provider: CalendarProvider, birth_native: str, asof_native: str) -> int:
    return provider.age(fixed_of(provider, birth_native), fixed_of(provider, asof_native))


def is_anniversary(provider: CalendarProvider, birth_native: str, asof_native: str) -> bool:
    return provider.is_anniversary(fixed_of(provider, birth_native), fixed_of(provider, asof_native))


UPCOMING_WINDOW_DAYS = 30


def _configured(cfg: dict) -> list[CalendarProvider]:
    out = [get_provider(cfg["primary"])]
    if cfg.get("secondary"):
        out.append(get_provider(cfg["secondary"]))
    return out


def today_facts(cfg: dict, native: str) -> dict:
    """Computed date facts for a scene's current moment, merged across all
    configured calendars. `cfg` is {primary, secondary|None}."""
    providers = _configured(cfg)
    primary = providers[0]
    fixed = fixed_of(primary, native)
    primary_desc = primary.describe(fixed)

    secondary_friendly = None
    if len(providers) > 1:
        secondary_friendly = providers[1].describe(fixed)["friendly"]

    holidays_today: list[str] = []
    for p in providers:
        for h in p.holidays(fixed, fixed):
            if h["name"] not in holidays_today:
                holidays_today.append(h["name"])

    upcoming = None
    soonest: dict | None = None
    for p in providers:
        for h in p.holidays(fixed + 1, fixed + UPCOMING_WINDOW_DAYS):
            if soonest is None or h["fixed"] < soonest["fixed"]:
                soonest = h
    if soonest is not None:
        upcoming = {"name": soonest["name"], "in_days": soonest["fixed"] - fixed}

    return {
        "friendly": primary_desc["friendly"],
        "weekday": primary_desc["weekday_name"],
        "secondary_friendly": secondary_friendly,
        "holidays_today": holidays_today,
        "upcoming": upcoming,
    }
