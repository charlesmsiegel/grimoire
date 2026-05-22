"""Calendar engines + reconciliation utilities.

Public exports:

  * `engine_for(calendar)` — get a CalendarEngine for a Calendar
  * `BUILTIN_CALENDARS`, `BUILTIN_HOLIDAY_SETS` — id-keyed registries
  * `convert_date(...)` — JDN-anchored conversion between calendars
  * `occurrences_in_year(...)` — expand a HolidaySet for a year
"""

from __future__ import annotations

from .base import (
    WEEKDAY_NAMES,
    CalendarEngine,
    DateParts,
    jdn_weekday,
    jdn_weekday_name,
)
from .holidays import (
    occurrences_in_jdn_range,
    occurrences_in_year,
    resolve_holiday,
)
from .holidays_seed import BUILTIN_HOLIDAY_SETS
from .registry import (
    BUILTIN_CALENDARS,
    engine_for,
    get_builtin_calendar,
    get_builtin_holiday_set,
    is_builtin_calendar,
    is_builtin_holiday_set,
    list_builtin_calendars,
    list_builtin_holiday_sets,
)

__all__ = [
    "WEEKDAY_NAMES",
    "CalendarEngine",
    "DateParts",
    "jdn_weekday",
    "jdn_weekday_name",
    "BUILTIN_CALENDARS",
    "BUILTIN_HOLIDAY_SETS",
    "engine_for",
    "get_builtin_calendar",
    "get_builtin_holiday_set",
    "is_builtin_calendar",
    "is_builtin_holiday_set",
    "list_builtin_calendars",
    "list_builtin_holiday_sets",
    "resolve_holiday",
    "occurrences_in_year",
    "occurrences_in_jdn_range",
]
