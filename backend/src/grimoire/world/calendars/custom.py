"""Generic engine for user-defined custom calendars.

Reads the calendar's CustomCalendarConfig (months, days-per-week,
declarative leap rule, epoch JDN) and computes (year, month, day) <->
JDN in pure arithmetic. Supports three leap-rule kinds:

  * NONE — fixed-length year
  * GREGORIAN_LIKE — leap day inserted in a specified month every N years
    with optional skip / keep exceptions (Gregorian-style 4/100/400)
  * CUSTOM_CYCLE — explicit list of leap-year offsets within a cycle
  * LEAP_MONTH — adds an entire intercalary month in leap years

All counting is done from the calendar's epoch_jdn (the JDN of
year=1, month=1, day=1). Negative years (BC equivalents) work too, but
fantasy calendars typically anchor year 1 at their creation moment.
"""

from __future__ import annotations

from grimoire.types.calendar import (
    CustomCalendarConfig,
    LeapRule,
    LeapRuleKind,
)

from .base import CalendarEngine, DateParts


def is_custom_leap(year: int, rule: LeapRule) -> bool:
    if rule.kind == LeapRuleKind.NONE:
        return False
    if rule.kind == LeapRuleKind.GREGORIAN_LIKE:
        if rule.cycle_short <= 0:
            return False
        keep = rule.cycle_keep
        skip = rule.cycle_skip
        short = rule.cycle_short
        if year % short != 0:
            return False
        if skip > 0 and year % skip == 0:
            return bool(keep > 0 and year % keep == 0)
        return True
    if rule.kind in (LeapRuleKind.CUSTOM_CYCLE, LeapRuleKind.LEAP_MONTH):
        if rule.cycle_years <= 0:
            return False
        # Offset within cycle is 1..cycle_years (year 1 -> offset 1).
        offset = ((year - 1) % rule.cycle_years) + 1
        return offset in set(rule.leap_years_in_cycle)
    return False


def custom_month_lengths(year: int, config: CustomCalendarConfig) -> list[tuple[str, int]]:
    """Return [(month_name, days), ...] for the given year."""
    rule = config.leap_rule
    months = [(m.name, m.days) for m in config.months]
    if not months:
        return [("Day", 1)]
    leap = is_custom_leap(year, rule)
    if leap and rule.kind == LeapRuleKind.GREGORIAN_LIKE:
        idx = rule.leap_day_month - 1
        if 0 <= idx < len(months):
            name, days = months[idx]
            months[idx] = (name, days + rule.leap_days)
    elif leap and rule.kind == LeapRuleKind.LEAP_MONTH:
        position = max(1, min(len(months) + 1, rule.leap_month_position))
        months.insert(position - 1, (rule.leap_month_name or "Intercalary", rule.leap_month_days))
    return months


def custom_year_length(year: int, config: CustomCalendarConfig) -> int:
    return sum(days for _, days in custom_month_lengths(year, config))


def custom_to_jdn(year: int, month: int, day: int, config: CustomCalendarConfig) -> int:
    if year >= 1:
        years_iter = range(1, year)
        days_before_year = sum(custom_year_length(y, config) for y in years_iter)
    else:
        # Years 0, -1, -2, ... count backward from the epoch.
        years_iter = range(year, 1)
        days_before_year = -sum(custom_year_length(y, config) for y in years_iter)
    months = custom_month_lengths(year, config)
    days_before_month = sum(days for _, days in months[: month - 1])
    return config.epoch_jdn + days_before_year + days_before_month + day - 1


def custom_from_jdn(jdn: int, config: CustomCalendarConfig) -> tuple[int, int, int]:
    days_since_epoch = jdn - config.epoch_jdn
    year = 1
    if days_since_epoch >= 0:
        # Walk forward year-by-year.
        remaining = days_since_epoch
        while True:
            length = custom_year_length(year, config)
            if remaining < length:
                break
            remaining -= length
            year += 1
    else:
        # Walk backward.
        remaining = days_since_epoch
        while remaining < 0:
            year -= 1
            remaining += custom_year_length(year, config)
    months = custom_month_lengths(year, config)
    month = 1
    while month <= len(months) and remaining >= months[month - 1][1]:
        remaining -= months[month - 1][1]
        month += 1
    return year, month, remaining + 1


class CustomCalendarEngine(CalendarEngine):
    system = "custom"

    def __init__(self, config: CustomCalendarConfig, era_name: str = "") -> None:
        self.config = config
        self.era_name = era_name

    def to_jdn(self, date: DateParts) -> int:
        return custom_to_jdn(date.year, date.month, date.day, self.config)

    def from_jdn(self, jdn: int) -> DateParts:
        y, m, d = custom_from_jdn(jdn, self.config)
        return DateParts(year=y, month=m, day=d, era=self.era_name)

    def format(self, parts: DateParts) -> str:
        months = custom_month_lengths(parts.year, self.config)
        name = months[parts.month - 1][0] if 1 <= parts.month <= len(months) else f"M{parts.month}"
        era = f" {self.era_name}" if self.era_name else ""
        return f"{parts.day} {name} {parts.year}{era}"

    def month_name(self, parts: DateParts) -> str:
        months = custom_month_lengths(parts.year, self.config)
        if 1 <= parts.month <= len(months):
            return months[parts.month - 1][0]
        return f"M{parts.month}"
