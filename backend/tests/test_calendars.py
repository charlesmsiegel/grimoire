import pytest

from grimoire.store import calendars
from grimoire.store.calendars import CalendarError, get_provider, normalize, fixed_of, minutes_of, split_native


def greg(region="US", custom=None, anchor=None):
    return {"provider": "gregorian", "region": region,
            "custom_holidays": custom or [], "anchor": anchor}


def test_gregorian_parse_format_roundtrip():
    p = get_provider(greg())
    fixed = p.parse("2026-06-29")
    assert p.format(fixed) == "2026-06-29"


def test_gregorian_weekday_known_date():
    p = get_provider(greg())
    d = p.describe(p.parse("2026-06-29"))
    assert d["weekday_name"] == "Monday"
    assert d["friendly"] == "29 June 2026"


def test_gregorian_leap_validity():
    p = get_provider(greg())
    assert p.format(p.parse("2000-02-29")) == "2000-02-29"   # 2000 is a leap year
    with pytest.raises(CalendarError):
        p.parse("1900-02-29")                                # 1900 is not


def test_unknown_provider_raises():
    with pytest.raises(CalendarError):
        get_provider({"provider": "nope", "region": "US", "custom_holidays": [], "anchor": None})


def test_split_native_and_minutes():
    assert split_native("2026-06-29T14:30") == ("2026-06-29", "14:30")
    assert split_native("2026-06-29") == ("2026-06-29", None)
    assert minutes_of("2026-06-29T14:30") == 14 * 60 + 30
    assert minutes_of("2026-06-29") is None


def test_normalize_preserves_time_and_canonicalizes_date():
    p = get_provider(greg())
    assert normalize(p, "2026-06-29T14:30") == "2026-06-29T14:30"
    with pytest.raises(CalendarError):
        normalize(p, "2026-13-01")          # bad month
    with pytest.raises(CalendarError):
        normalize(p, "2026-06-29T25:00")    # bad time


def test_fixed_of_ignores_time():
    p = get_provider(greg())
    assert fixed_of(p, "2026-06-29T14:30") == fixed_of(p, "2026-06-29")
