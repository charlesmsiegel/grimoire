import pytest

from grimoire.store.calendars import CalendarError, get_provider


def heb(region=""):
    return {"provider": "hebrew", "region": region, "custom_holidays": [], "anchor": None}


def test_known_conversions_roundtrip():
    p = get_provider(heb())
    g = get_provider({"provider": "gregorian", "region": "", "custom_holidays": [], "anchor": None})
    # 25 Kislev 5786 = 15 Dec 2025; 1 Tishrei 5786 (Rosh Hashanah) = 23 Sep 2025
    assert p.parse("5786-Kislev-25") == g.parse("2025-12-15")
    assert p.parse("5786-Tishrei-01") == g.parse("2025-09-23")
    for native in ("5786-Kislev-25", "5784-Adar2-14", "5786-Nisan-15"):
        assert p.format(p.parse(native)) == native


def test_parse_is_case_insensitive_and_normalizes_adar():
    p = get_provider(heb())
    assert p.format(p.parse("5786-kislev-25")) == "5786-Kislev-25"
    # leap year: plain Adar is accepted and normalized to Adar2 (observance month)
    assert p.format(p.parse("5784-Adar-14")) == "5784-Adar2-14"


def test_bad_dates_raise():
    p = get_provider(heb())
    with pytest.raises(CalendarError):
        p.parse("5786-Adar1-01")        # Adar I doesn't exist in a non-leap year
    with pytest.raises(CalendarError):
        p.parse("5786-Cheshvan-30")     # Cheshvan is short in 5786
    with pytest.raises(CalendarError):
        p.parse("5786-Floof-01")
    with pytest.raises(CalendarError):
        p.parse("5786-Kislev")          # missing day


def test_describe_weekday_and_friendly():
    p = get_provider(heb())
    d = p.describe(p.parse("5786-Tishrei-01"))   # 23 Sep 2025 is a Tuesday
    assert d["weekday_name"] == "Tuesday"
    assert d["friendly"] == "1 Tishrei 5786"
    assert d["month"] == 1                        # civil position
    # Shabbat: 27 Sep 2025 is a Saturday = 5 Tishrei 5786
    s = p.describe(p.parse("5786-Tishrei-05"))
    assert s["weekday_name"] == "Shabbat"
    assert s["weekday_index"] == 6


def test_months_leap_and_common_years():
    p = get_provider(heb())
    common = p.months(5786)
    assert [m["key"] for m in common][:6] == ["Tishrei", "Cheshvan", "Kislev", "Tevet", "Shevat", "Adar"]
    assert len(common) == 12
    leap = p.months(5784)
    assert len(leap) == 13
    keys = [m["key"] for m in leap]
    assert "Adar1" in keys and "Adar2" in keys and "Adar" not in keys
    assert next(m for m in leap if m["key"] == "Adar1")["name"] == "Adar I"
    # composition contract
    for m in leap:
        assert p.format(p.parse(f"5784-{m['key']}-{m['days']:02d}")) == f"5784-{m['key']}-{m['days']:02d}"
