"""Calendar + holiday types.

Calendars are first-class library entities (peer of worlds, style-guides, etc.).
A built-in set covers the major real-world systems; users can define custom
fantasy calendars with declarative leap rules. Worlds and campaigns attach
zero or more calendars and pick one as the "display" calendar; all attached
calendars are reconciled through a shared Julian Day Number anchor so any
date in one calendar can be rendered in another.

Holidays are grouped into HolidaySets which are bound to a specific calendar
system (US Federal holidays only make sense on Gregorian, Jewish holidays
only on Hebrew, etc.). A world can attach multiple sets to overlay different
cultural traditions onto its calendar(s).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CalendarSystem(StrEnum):
    """Identifies which engine drives a calendar's date math.

    Custom calendars use the generic engine and store their full month /
    week / leap-rule definition in CustomCalendarConfig. Built-in systems
    are hard-coded in code so their leap rules (Gregorian's 400-year cycle,
    Hebrew's Metonic cycle, Islamic's 30-year cycle, Persian's 2820-year
    cycle, etc.) stay accurate.
    """

    GREGORIAN = "gregorian"
    JULIAN = "julian"
    HEBREW = "hebrew"
    ISLAMIC = "islamic"
    PERSIAN = "persian"
    CHINESE = "chinese"
    JAPANESE_ERA = "japanese_era"
    INDIAN_SAKA = "indian_saka"
    ETHIOPIAN = "ethiopian"
    COPTIC = "coptic"
    BAHAI = "bahai"
    BUDDHIST = "buddhist"
    ISO_WEEK = "iso_week"
    STARDATE = "stardate"
    CUSTOM = "custom"


class HolidayRule(StrEnum):
    """How to compute a holiday's date in a given year."""

    FIXED = "fixed"  # month + day
    NTH_WEEKDAY = "nth_weekday"  # nth weekday of a month, e.g. 4th Thursday of Nov
    LAST_WEEKDAY = "last_weekday"  # last weekday of a month, e.g. last Mon in May
    EASTER_WESTERN = "easter_western"  # offset from Western Easter (Gregorian only)
    EASTER_ORTHODOX = "easter_orthodox"  # offset from Orthodox Easter (Gregorian only)
    LUNAR_NEW_YEAR = "lunar_new_year"  # offset from Chinese Lunar New Year (Gregorian only)


class CalendarMonth(BaseModel):
    """A month definition for a custom calendar."""

    name: str
    days: int
    short_name: str = ""


class CalendarSeason(BaseModel):
    name: str
    start_month: int = 1
    start_day: int = 1
    palette: str = ""


class LeapRuleKind(StrEnum):
    NONE = "none"
    # Gregorian-style: leap every N years, except every M, except every L
    # (default: 4 / 100 / 400). Inserts `leap_days` into `leap_day_month`.
    GREGORIAN_LIKE = "gregorian_like"
    # Custom cycle: list of leap-year offsets within a fixed cycle.
    # Used for Hebrew-style (19-year Metonic) or Islamic-style (30-year)
    # patterns expressed declaratively.
    CUSTOM_CYCLE = "custom_cycle"
    # Add an entire intercalary month every `cycle_years` whose offsets
    # appear in `leap_years_in_cycle`. The month is inserted at
    # `leap_month_position` (1-indexed) with name `leap_month_name`.
    LEAP_MONTH = "leap_month"


class LeapRule(BaseModel):
    """Declarative leap rule for custom calendars.

    Built-in calendars don't read this — their engines own their own rules.
    """

    kind: LeapRuleKind = LeapRuleKind.NONE

    # GREGORIAN_LIKE
    cycle_short: int = 4  # year is leap if year % cycle_short == 0...
    cycle_skip: int = 100  # ...except if year % cycle_skip == 0...
    cycle_keep: int = 400  # ...unless year % cycle_keep == 0.
    leap_days: int = 1
    leap_day_month: int = 2  # 1-indexed; which month gets the extra day

    # CUSTOM_CYCLE / LEAP_MONTH
    cycle_years: int = 0
    leap_years_in_cycle: list[int] = Field(default_factory=list)

    # LEAP_MONTH
    leap_month_name: str = ""
    leap_month_days: int = 30
    leap_month_position: int = 1


class CustomCalendarConfig(BaseModel):
    """Full description of a user-defined calendar.

    Populated only when CalendarSystem is CUSTOM. Built-in systems leave
    this empty and use code-based engines instead.
    """

    months: list[CalendarMonth] = Field(default_factory=list)
    days_per_week: int = 7
    week_day_names: list[str] = Field(default_factory=list)
    seasons: list[CalendarSeason] = Field(default_factory=list)
    leap_rule: LeapRule = Field(default_factory=LeapRule)
    # Anchor: what JDN does year=1, month=1, day=1 of this calendar
    # represent? Default is the Gregorian proleptic epoch (1 Jan 1 AD =
    # JDN 1721426).
    epoch_jdn: int = 1721426
    # Era display name (e.g. "AC" for "After Cataclysm"). Empty means none.
    era_name: str = ""


class Holiday(BaseModel):
    """A single holiday entry within a HolidaySet."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    rule: HolidayRule = HolidayRule.FIXED

    # FIXED
    month: int = 1
    day: int = 1

    # NTH_WEEKDAY / LAST_WEEKDAY (weekday 0=Monday..6=Sunday for ISO).
    # For NTH, nth is 1..5 (5 means "last" too if the month is short).
    weekday: int = 0
    nth: int = 1
    weekday_month: int = 1

    # EASTER_*: offset_days from Easter Sunday (negative = before).
    # LUNAR_NEW_YEAR: offset_days from Lunar New Year (typically 0).
    offset_days: int = 0

    # Optional duration — Hanukkah is 8 days, Ramadan a month, etc.
    duration_days: int = 1


class Calendar(BaseModel):
    """A first-class calendar definition."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    system: CalendarSystem
    builtin: bool = False
    # Only populated when system == CUSTOM.
    custom: CustomCalendarConfig | None = None
    # Formatting hint for the frontend: how to render dates by default
    # (e.g. "%Y-%m-%d", "Year %Y, %B %-d", or era-specific).
    date_format: str = ""
    version: int = 0


class HolidaySet(BaseModel):
    """A named collection of holidays that target a specific calendar system.

    A HolidaySet binds to a CalendarSystem (not a calendar id) so that the
    same set (e.g. US Federal holidays) works against any Gregorian-system
    calendar attached to a world.
    """

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    calendar_system: CalendarSystem
    holidays: list[Holiday] = Field(default_factory=list)
    builtin: bool = False
    version: int = 0


class CalendarDate(BaseModel):
    """A date expressed in a specific calendar, plus its JDN anchor."""

    calendar_id: str
    jdn: int
    year: int
    month: int
    day: int
    formatted: str = ""
    era: str = ""
    weekday: str = ""


class HolidayOccurrence(BaseModel):
    """A holiday instance resolved to an absolute JDN range."""

    set_id: str
    holiday_id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    jdn_start: int
    jdn_end: int  # inclusive
