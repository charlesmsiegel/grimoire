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

from .plugins import load_custom_providers


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
NAMES: dict[str, str] = {}


def register(provider_id: str, cls: type[CalendarProvider], name: str | None = None) -> None:
    REGISTRY[provider_id] = cls
    NAMES[provider_id] = name or provider_id.replace("_", " ").title()


def get_provider(config: dict) -> CalendarProvider:
    load_custom_providers()
    cls = REGISTRY.get(config.get("provider", "gregorian"))
    if cls is None:
        raise CalendarError(f"unknown calendar provider: {config.get('provider')!r}")
    return cls(config)


def list_providers() -> list[dict]:
    """Every registered calendar (built-in + user-authored), for a UI picker."""
    load_custom_providers()
    return [{"id": pid, "name": NAMES.get(pid, pid)} for pid in REGISTRY]


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


#: How far either side of an anchor `resolve` will look for a day this calendar
#: renders the way the caller wrote it. A year and a bit: far enough for any
#: "next scene" a suggestion proposes (and for the season-away ones), short
#: enough that the scan stays bounded work on a path that only runs after a
#: parse has already failed.
RESOLVE_WINDOW_DAYS = 400


def _loose(text: str) -> str:
    """Case, spacing and comma-insensitive, for comparing two renderings of a date."""
    return " ".join(text.replace(",", " ").split()).casefold()


def resolve(provider: CalendarProvider, native: str, near: str = "") -> str:
    """Canonical native date, accepting any form THIS calendar renders.

    `normalize` first; failing that, look for a day near `near` whose own
    `format` or `describe(...)["friendly"]` matches what the caller wrote.

    Exists because every prompt shows a date as `friendly` ("25 Kislev 5786")
    and no prompt can make a model spell one back in a notation it has never
    seen. Gregorian survived that on luck alone -- its native form is ISO-8601,
    which is what a model writes into JSON unprompted -- and every other
    calendar, Hebrew and hand-written plugins alike, lost the date silently.
    The templates now show the native form too; this is the half that does not
    depend on the model reading them.

    Calendar-agnostic by construction, and deliberately not a date *parser*: the
    only strings it adds are ones the provider itself produced, so a plugin
    gets the same tolerance as a built-in without implementing anything, and
    nothing can be accepted that the calendar would not have written.

    Anchored rather than open-ended for two reasons: a scan needs a bound, and
    "2 Tevet" is only unambiguous near a year. No `near`, or one this calendar
    cannot read, means no window -- `CalendarError`, exactly as before.
    """
    date_str, time_str = split_native(native)
    try:
        return normalize(provider, native)
    except CalendarError:
        pass
    wanted = _loose(date_str)
    if not wanted or not near:
        raise CalendarError(f"bad date: {native!r}")
    try:
        anchor = fixed_of(provider, near)
    except CalendarError as e:
        raise CalendarError(f"bad date: {native!r}") from e
    for offset in range(RESOLVE_WINDOW_DAYS + 1):
        # Outward from the anchor, and FORWARD first at each distance: the
        # caller is dating the next scene, and where a calendar renders two days
        # the same way the later one is the likelier read. Ordered explicitly
        # rather than left to a set, whose iteration order is a hash detail --
        # the same question must not get two answers.
        for fixed in ((anchor,) if offset == 0 else (anchor + offset, anchor - offset)):
            try:
                canonical = provider.format(fixed)
            except (CalendarError, ValueError, OverflowError, OSError):
                continue   # a day outside this calendar's own range is simply not the answer
            # `describe` only where `format` has already missed, and unguarded:
            # a provider whose `describe` cannot be read is broken everywhere
            # (`friendly`, `today_facts`), and swallowing it here would hide
            # that behind a dropped date -- once per day of the window, silently.
            if _loose(canonical) != wanted and _loose(provider.describe(fixed)["friendly"]) != wanted:
                continue
            if time_str is None:
                return canonical
            minutes = minutes_of(native)   # validates the range, raises CalendarError
            return f"{canonical}T{minutes // 60:02d}:{minutes % 60:02d}"
    raise CalendarError(f"bad date: {native!r}")
    try:
        anchor = fixed_of(provider, near)
    except CalendarError as e:
        raise CalendarError(f"bad date: {native!r}") from e
    # Outward from the anchor, nearest day first: where two days could be
    # written the same way, the one closest to the campaign's present is the
    # one that was meant -- and the common case (days or weeks ahead) stops
    # the scan early rather than walking the whole window.
    for offset in range(RESOLVE_WINDOW_DAYS + 1):
        for fixed in ({anchor + offset, anchor - offset} if offset else {anchor}):
            try:
                canonical = provider.format(fixed)
                if _loose(canonical) == wanted:
                    return canonical
                if _loose(provider.describe(fixed)["friendly"]) == wanted:
                    return canonical
            except (CalendarError, KeyError, TypeError, ValueError, OverflowError, OSError):
                continue   # a day outside this calendar's own range is simply not the answer
    raise CalendarError(f"bad date: {native!r}")


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


def _upcoming(providers: list[CalendarProvider], fixed: int, window: int) -> list[dict]:
    """Observances in `(fixed, fixed + window]` across `providers`, soonest first.

    Each `{name, fixed, in_days}`. Deduplicated on (day, name), because a
    secondary calendar that observes the same thing the primary does would
    otherwise offer it twice, and the reader is being warned about one day.

    Sorted only on `in_days`, and Python's sort is stable, so two DIFFERENT
    observances landing on one day keep the order the providers emitted them
    in -- the primary's first. That is what `today_facts` already picked when
    it scanned for the strictly-soonest, and this must not quietly rename the
    "Upcoming:" line every prompt has been carrying.

    The name is coerced to `str` before it is used, and that is load-bearing
    rather than defensive. `validate_rule` accepts any TRUTHY name, so a
    hand-written `"name": ["Saltmarch", "Eve"]` in calendar.json is a config
    this app already stores -- and the dedup below puts the name in a set, where
    a list raises `TypeError`. `today_facts` reaches this helper now, so that
    would leave an accepted calendar 500-ing the scene datetime route and
    failing prompt assembly, past every caller's `except CalendarError`. The
    old scan compared `h["fixed"]` alone and never touched the name, which is
    why nothing noticed before.
    """
    seen: set[tuple[int, str]] = set()
    out: list[dict] = []
    for p in providers:
        for h in p.holidays(fixed + 1, fixed + max(int(window), 0)):
            name = h["name"] if isinstance(h["name"], str) else str(h["name"])
            key = (h["fixed"], name)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "fixed": h["fixed"],
                        "in_days": h["fixed"] - fixed})
    out.sort(key=lambda h: h["in_days"])
    return out


def upcoming_holidays(cfg: dict, native: str, window: int = UPCOMING_WINDOW_DAYS) -> list[dict]:
    """Every observance within `window` days after `native`, soonest first.

    `today_facts` answers with the soonest one alone, which is all a prompt
    line can say; a warn window (#106) wants everything inside it, and widening
    `today_facts` would change a shape three consumers already read. So the
    scan is shared and only the projection differs.
    """
    providers = _configured(cfg)
    return _upcoming(providers, fixed_of(providers[0], native), window)


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

    ahead = _upcoming(providers, fixed, UPCOMING_WINDOW_DAYS)
    upcoming = {"name": ahead[0]["name"], "in_days": ahead[0]["in_days"]} if ahead else None

    return {
        "friendly": primary_desc["friendly"],
        "weekday": primary_desc["weekday_name"],
        "secondary_friendly": secondary_friendly,
        "holidays_today": holidays_today,
        "upcoming": upcoming,
    }
