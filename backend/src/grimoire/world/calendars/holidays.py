"""Holiday date resolution.

Given a HolidaySet and a year, expand its declarative holiday rules
into concrete (jdn_start, jdn_end) ranges. Movable holidays (nth
weekday of a month, Easter offsets, Lunar New Year offsets) are
computed here.
"""

from __future__ import annotations

from grimoire.types.calendar import (
    CalendarSystem,
    Holiday,
    HolidayOccurrence,
    HolidayRule,
    HolidaySet,
)

from .gregorian import (
    days_in_gregorian_month,
    gregorian_to_jdn,
)

# ---------------------------------------------------------------------------
# Easter computation
# ---------------------------------------------------------------------------


def _gregorian_easter(year: int) -> tuple[int, int]:
    """Return (month, day) of Western Easter in `year` (Gregorian)."""
    # Meeus / Butcher algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return month, day


def _orthodox_easter(year: int) -> tuple[int, int]:
    """Return (month, day) of Orthodox Easter in `year` (Gregorian).

    Computes the Julian Pascha and shifts to Gregorian.
    """
    # Meeus's Julian algorithm.
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month_j = (d + e + 114) // 31  # March = 3, April = 4
    day_j = ((d + e + 114) % 31) + 1
    # Now month_j/day_j are in Julian — shift to Gregorian by adding the
    # offset (13 days for 1900-2099).
    if year < 1900:
        offset = 12
    elif year < 2100:
        offset = 13
    else:
        offset = 14
    day_g = day_j + offset
    month_g = month_j
    days_in_month = days_in_gregorian_month(year, month_g)
    if day_g > days_in_month:
        day_g -= days_in_month
        month_g += 1
    return month_g, day_g


# ---------------------------------------------------------------------------
# Chinese Lunar New Year (table-driven via chinese.py)
# ---------------------------------------------------------------------------


def _lunar_new_year_jdn(gregorian_year: int) -> int:
    """Return the JDN of Chinese Lunar New Year that falls in `gregorian_year`."""
    from .chinese import _YEAR_DATA

    # _YEAR_DATA[y] is (lunar_new_year_jdn, leap_month_or_zero); we want [0].
    if gregorian_year in _YEAR_DATA:
        return _YEAR_DATA[gregorian_year][0]
    raise ValueError(f"Lunar New Year not available for Gregorian year {gregorian_year}")


# ---------------------------------------------------------------------------
# Weekday-of-month
# ---------------------------------------------------------------------------


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> int:
    """Return the day-of-month for the nth `weekday` (0=Mon..6=Sun) of (year, month).

    Returns -1 if there's no such weekday (e.g. asking for the 5th Tuesday
    in a month that only has 4 Tuesdays).
    """
    first_jdn = gregorian_to_jdn(year, month, 1)
    first_weekday = first_jdn % 7  # 0=Mon..6=Sun
    delta = (weekday - first_weekday) % 7
    day = 1 + delta + (nth - 1) * 7
    if day > days_in_gregorian_month(year, month):
        return -1
    return day


def _last_weekday_of_month(year: int, month: int, weekday: int) -> int:
    last_day = days_in_gregorian_month(year, month)
    last_jdn = gregorian_to_jdn(year, month, last_day)
    last_weekday = last_jdn % 7
    delta = (last_weekday - weekday) % 7
    return last_day - delta


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_holiday(holiday: Holiday, year: int, system: CalendarSystem) -> int | None:
    """Return the JDN of the first day of `holiday` in `year`.

    `year` is interpreted in the calendar's native system (Gregorian
    year for Gregorian sets, Hebrew year for Hebrew sets, etc.). Returns
    None when the rule can't be resolved for this year (e.g. lunar table
    not available, or 5th-weekday-of-month doesn't exist).
    """
    if holiday.rule == HolidayRule.FIXED:
        if system == CalendarSystem.GREGORIAN:
            return gregorian_to_jdn(year, holiday.month, holiday.day)
        # For non-Gregorian systems, the caller resolves to JDN via the
        # appropriate engine; we return the (month, day) packed into a
        # negative sentinel? Simpler: return None and have the resolver
        # function below dispatch to the right engine.
        return _native_fixed_to_jdn(year, holiday.month, holiday.day, system)

    if holiday.rule == HolidayRule.NTH_WEEKDAY:
        day = _nth_weekday_of_month(year, holiday.weekday_month, holiday.weekday, holiday.nth)
        if day < 0:
            return None
        return gregorian_to_jdn(year, holiday.weekday_month, day)

    if holiday.rule == HolidayRule.LAST_WEEKDAY:
        day = _last_weekday_of_month(year, holiday.weekday_month, holiday.weekday)
        return gregorian_to_jdn(year, holiday.weekday_month, day)

    if holiday.rule == HolidayRule.EASTER_WESTERN:
        month, day = _gregorian_easter(year)
        return gregorian_to_jdn(year, month, day) + holiday.offset_days

    if holiday.rule == HolidayRule.EASTER_ORTHODOX:
        month, day = _orthodox_easter(year)
        return gregorian_to_jdn(year, month, day) + holiday.offset_days

    if holiday.rule == HolidayRule.LUNAR_NEW_YEAR:
        try:
            cny = _lunar_new_year_jdn(year)
        except ValueError:
            return None
        return cny + holiday.offset_days

    return None


def _native_fixed_to_jdn(year: int, month: int, day: int, system: CalendarSystem) -> int | None:
    """Resolve a fixed (year, month, day) in a non-Gregorian system to JDN."""
    from .registry import _ENGINE_FACTORIES  # local import to avoid cycle

    factory = _ENGINE_FACTORIES.get(system)
    if factory is None:
        return None
    try:
        return factory().to_jdn(year, month, day)
    except Exception:
        return None


def occurrences_in_year(holiday_set: HolidaySet, year: int) -> list[HolidayOccurrence]:
    """Expand `holiday_set` for a given year into concrete JDN ranges."""
    out: list[HolidayOccurrence] = []
    for h in holiday_set.holidays:
        start = resolve_holiday(h, year, holiday_set.calendar_system)
        if start is None:
            continue
        end = start + max(1, h.duration_days) - 1
        out.append(
            HolidayOccurrence(
                set_id=holiday_set.id,
                holiday_id=h.id,
                name=h.name,
                description=h.description,
                tags=list(h.tags),
                jdn_start=start,
                jdn_end=end,
            )
        )
    return out


def occurrences_in_jdn_range(
    holiday_set: HolidaySet,
    jdn_from: int,
    jdn_to: int,
    *,
    system_year_resolver,
) -> list[HolidayOccurrence]:
    """All occurrences from `holiday_set` overlapping [jdn_from, jdn_to].

    `system_year_resolver(jdn)` returns the year of the holiday_set's
    calendar system for that JDN — provided by the calendar service so
    Hebrew sets get queried by Hebrew years, Islamic by Hijri, etc.
    """
    year_lo = system_year_resolver(jdn_from)
    year_hi = system_year_resolver(jdn_to)
    out: list[HolidayOccurrence] = []
    for y in range(year_lo - 1, year_hi + 2):  # ±1 to catch boundary spans
        for occ in occurrences_in_year(holiday_set, y):
            if occ.jdn_end >= jdn_from and occ.jdn_start <= jdn_to:
                out.append(occ)
    out.sort(key=lambda o: o.jdn_start)
    return out
