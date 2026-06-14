"""Calendar engine round-trip + reconciliation tests."""

from __future__ import annotations

import pytest

from grimoire.types.calendar import (
    Calendar,
    CalendarSystem,
    CustomCalendarConfig,
    LeapRule,
    LeapRuleKind,
)
from grimoire.world.calendars import (
    BUILTIN_CALENDARS,
    BUILTIN_HOLIDAY_SETS,
    DateParts,
    engine_for,
    jdn_weekday_name,
    occurrences_in_year,
)
from grimoire.world.calendars.gregorian import (
    gregorian_from_jdn,
    gregorian_to_jdn,
    is_gregorian_leap,
)
from grimoire.world.calendars.hebrew import is_hebrew_leap
from grimoire.world.calendars.islamic import is_islamic_leap

# ---------------------------------------------------------------------------
# Gregorian — anchor everyone uses
# ---------------------------------------------------------------------------


def test_gregorian_known_jdn() -> None:
    # JDN 2451545 is famously 1 Jan 2000 (Saturday).
    assert gregorian_to_jdn(2000, 1, 1) == 2451545
    assert gregorian_from_jdn(2451545) == (2000, 1, 1)
    assert jdn_weekday_name(2451545) == "Saturday"


def test_gregorian_leap_rule() -> None:
    assert is_gregorian_leap(2000)
    assert is_gregorian_leap(2024)
    assert not is_gregorian_leap(1900)
    assert not is_gregorian_leap(2023)


def test_gregorian_round_trip_range() -> None:
    # 100 years of round-trip
    base = gregorian_to_jdn(1950, 1, 1)
    for offset in range(0, 365 * 100, 37):
        y, m, d = gregorian_from_jdn(base + offset)
        assert gregorian_to_jdn(y, m, d) == base + offset


# ---------------------------------------------------------------------------
# Per-system round-trip coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("calendar_id", sorted(BUILTIN_CALENDARS.keys()))
def test_every_builtin_round_trips_today(calendar_id: str) -> None:
    """Round-trip JDN of 2025-05-22 through every built-in engine."""
    if calendar_id == "stardate":
        pytest.skip("Stardate intentionally lossy below day precision")
    cal = BUILTIN_CALENDARS[calendar_id]
    eng = engine_for(cal)
    jdn = gregorian_to_jdn(2025, 5, 22)
    parts = eng.from_jdn(jdn)
    assert eng.to_jdn(parts) == jdn


@pytest.mark.parametrize("calendar_id", sorted(BUILTIN_CALENDARS.keys()))
def test_every_builtin_format_renders(calendar_id: str) -> None:
    cal = BUILTIN_CALENDARS[calendar_id]
    eng = engine_for(cal)
    parts = eng.from_jdn(gregorian_to_jdn(2025, 5, 22))
    rendered = eng.format(parts)
    assert isinstance(rendered, str)
    assert len(rendered) > 0


# ---------------------------------------------------------------------------
# Hebrew + Islamic leap rules
# ---------------------------------------------------------------------------


def test_hebrew_metonic_leap_pattern() -> None:
    # 7 leap years in every 19: positions 3, 6, 8, 11, 14, 17, 19 of the cycle.
    cycle = set()
    for y in range(1, 20):
        if is_hebrew_leap(y):
            cycle.add(y % 19 if y % 19 != 0 else 19)
    assert cycle == {3, 6, 8, 11, 14, 17, 19}


def test_islamic_30year_leap_pattern() -> None:
    leaps = [y for y in range(1, 31) if is_islamic_leap(y)]
    assert leaps == [2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29]


# ---------------------------------------------------------------------------
# Reconciliation between calendars
# ---------------------------------------------------------------------------


def test_reconciliation_via_jdn() -> None:
    """A Gregorian date converts to Hebrew + Islamic via shared JDN."""
    greg = engine_for(BUILTIN_CALENDARS["gregorian"])
    hebrew = engine_for(BUILTIN_CALENDARS["hebrew"])
    islamic = engine_for(BUILTIN_CALENDARS["islamic"])

    jdn = greg.to_jdn(DateParts(2024, 10, 3))  # Rosh Hashanah eve, ~Hijri 30 Rabi al-Awwal
    h = hebrew.from_jdn(jdn)
    i = islamic.from_jdn(jdn)
    # Round trip back to JDN should match.
    assert hebrew.to_jdn(h) == jdn
    assert islamic.to_jdn(i) == jdn
    # Hebrew year 5785 begins at sunset of 2 Oct 2024.
    assert h.year == 5785


def test_julian_lags_gregorian_by_13_days_in_20th_century() -> None:
    greg = engine_for(BUILTIN_CALENDARS["gregorian"])
    julian = engine_for(BUILTIN_CALENDARS["julian"])
    jdn = greg.to_jdn(DateParts(2000, 1, 14))  # Russian Orthodox Christmas day
    parts = julian.from_jdn(jdn)
    assert (parts.year, parts.month, parts.day) == (2000, 1, 1)


def test_buddhist_year_offset() -> None:
    greg = engine_for(BUILTIN_CALENDARS["gregorian"])
    buddhist = engine_for(BUILTIN_CALENDARS["buddhist"])
    jdn = greg.to_jdn(DateParts(2025, 5, 22))
    parts = buddhist.from_jdn(jdn)
    assert parts.year == 2568  # 2025 + 543


# ---------------------------------------------------------------------------
# Custom calendar engine with declarative leap rules
# ---------------------------------------------------------------------------


def test_custom_calendar_simple_round_trip() -> None:
    config = CustomCalendarConfig(
        months=[{"name": "Sun", "days": 30}, {"name": "Moon", "days": 30}],  # type: ignore[list-item]
        days_per_week=5,
        week_day_names=["A", "B", "C", "D", "E"],
        leap_rule=LeapRule(kind=LeapRuleKind.NONE),
        epoch_jdn=2400000,
    )
    cal = Calendar(id="x", name="X", system=CalendarSystem.CUSTOM, custom=config)
    eng = engine_for(cal)

    # Year 1 month 1 day 1 = epoch_jdn
    assert eng.to_jdn(DateParts(1, 1, 1)) == 2400000
    # Last day of year 1 = epoch + 59
    assert eng.to_jdn(DateParts(1, 2, 30)) == 2400059
    # Round-trip
    parts = eng.from_jdn(2400059)
    assert (parts.year, parts.month, parts.day) == (1, 2, 30)


def test_custom_gregorian_like_leap_inserts_extra_day() -> None:
    config = CustomCalendarConfig(
        months=[
            {"name": "M1", "days": 30},
            {"name": "M2", "days": 30},  # type: ignore[list-item]
            {"name": "M3", "days": 30},
            {"name": "M4", "days": 30},
        ],  # type: ignore[list-item]
        leap_rule=LeapRule(
            kind=LeapRuleKind.GREGORIAN_LIKE,
            cycle_short=4,
            cycle_skip=100,
            cycle_keep=400,
            leap_days=1,
            leap_day_month=2,
        ),
        epoch_jdn=2400000,
    )
    cal = Calendar(id="y", name="Y", system=CalendarSystem.CUSTOM, custom=config)
    engine_for(cal)  # sanity check that the engine can be built
    # Year 4 should be leap (day added to month 2). Year 4 should have
    # 121 days (30+31+30+30) instead of 120.
    from grimoire.world.calendars.custom import custom_year_length

    assert custom_year_length(1, config) == 120
    assert custom_year_length(4, config) == 121
    assert custom_year_length(100, config) == 120  # cycle_skip
    assert custom_year_length(400, config) == 121  # cycle_keep wins


def test_custom_leap_month_extends_year() -> None:
    config = CustomCalendarConfig(
        months=[
            {"name": "A", "days": 30},
            {"name": "B", "days": 30},  # type: ignore[list-item]
            {"name": "C", "days": 30},
        ],  # type: ignore[list-item]
        leap_rule=LeapRule(
            kind=LeapRuleKind.LEAP_MONTH,
            cycle_years=3,
            leap_years_in_cycle=[3],
            leap_month_name="Intercalary",
            leap_month_days=15,
            leap_month_position=2,
        ),
        epoch_jdn=2400000,
    )
    cal = Calendar(id="z", name="Z", system=CalendarSystem.CUSTOM, custom=config)
    eng = engine_for(cal)
    from grimoire.world.calendars.custom import custom_year_length

    # Year 3 is leap (offset 3 in cycle 3).
    assert custom_year_length(1, config) == 90
    assert custom_year_length(3, config) == 105  # +15 leap month
    # The leap month is at position 2 — after that month, day 1 is the leap
    # month's first day.
    assert eng.to_jdn(DateParts(3, 2, 1)) == 2400000 + 2 * 90 + 30
    # Reverse
    parts = eng.from_jdn(2400000 + 2 * 90 + 30)
    assert parts.year == 3 and parts.month == 2 and parts.day == 1


# ---------------------------------------------------------------------------
# Holiday resolution
# ---------------------------------------------------------------------------


def test_us_federal_thanksgiving_in_known_years() -> None:
    us = BUILTIN_HOLIDAY_SETS["us-federal"]
    out = {o.holiday_id: o for o in occurrences_in_year(us, 2024)}
    thx = out["thanksgiving"]
    # Nov 28 2024 is the 4th Thursday.
    y, m, d = gregorian_from_jdn(thx.jdn_start)
    assert (y, m, d) == (2024, 11, 28)


def test_us_federal_thanksgiving_2025() -> None:
    us = BUILTIN_HOLIDAY_SETS["us-federal"]
    out = {o.holiday_id: o for o in occurrences_in_year(us, 2025)}
    thx = out["thanksgiving"]
    y, m, d = gregorian_from_jdn(thx.jdn_start)
    assert (y, m, d) == (2025, 11, 27)


def test_christian_western_easter_2024() -> None:
    christmas_set = BUILTIN_HOLIDAY_SETS["christian-western"]
    out = {o.holiday_id: o for o in occurrences_in_year(christmas_set, 2024)}
    easter = out["easter"]
    y, m, d = gregorian_from_jdn(easter.jdn_start)
    # Easter 2024 was March 31.
    assert (y, m, d) == (2024, 3, 31)


def test_orthodox_easter_2024() -> None:
    s = BUILTIN_HOLIDAY_SETS["christian-orthodox"]
    out = {o.holiday_id: o for o in occurrences_in_year(s, 2024)}
    pascha = out["pascha"]
    y, m, d = gregorian_from_jdn(pascha.jdn_start)
    # Orthodox Pascha 2024 was May 5.
    assert (y, m, d) == (2024, 5, 5)


def test_jewish_rosh_hashanah_falls_at_hebrew_year_start() -> None:
    s = BUILTIN_HOLIDAY_SETS["jewish"]
    out = {o.holiday_id: o for o in occurrences_in_year(s, 5785)}
    rosh = out["rosh-hashanah"]
    # Rosh Hashanah 5785 = sundown 2 Oct 2024 -> calendar day 3 Oct.
    y, m, _ = gregorian_from_jdn(rosh.jdn_start)
    assert (y, m) == (2024, 10)


def test_chinese_traditional_lunar_new_year_2025() -> None:
    """Spring Festival in 2025 resolves to the correct Gregorian date.

    Regression: an earlier version of _lunar_new_year_jdn returned the
    leap-month indicator from the _YEAR_DATA tuple instead of the JDN,
    sending every Lunar-anchored holiday back to 4713 BCE.
    """
    s = BUILTIN_HOLIDAY_SETS["chinese-traditional"]
    out = {o.holiday_id: o for o in occurrences_in_year(s, 2025)}
    lny = out["lunar-new-year"]
    y, m, d = gregorian_from_jdn(lny.jdn_start)
    # CNY 2025 was 29 January 2025.
    assert (y, m, d) == (2025, 1, 29)
    # Lantern Festival is +14 days = 12 Feb 2025.
    lantern = out["lantern-festival"]
    y, m, d = gregorian_from_jdn(lantern.jdn_start)
    assert (y, m, d) == (2025, 2, 12)
