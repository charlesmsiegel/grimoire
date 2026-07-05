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


def test_holidays_chanukah_and_observance_toggle():
    p = get_provider(heb())          # diaspora
    il = get_provider(heb("IL"))     # Israel
    start, end = p.parse("5786-Kislev-24"), p.parse("5786-Tevet-03")
    names = [h["name"] for h in p.holidays(start, end)]
    assert any("Chanuka" in n for n in names)
    # 22 Nisan is yom tov in the diaspora only (2nd day of the last day of Pesach)
    day = p.parse("5786-Nisan-22")
    assert any("Pesach" in h["name"] for h in p.holidays(day, day))
    assert il.holidays(day, day) == []


def test_holidays_include_fasts_and_customs():
    p = get_provider(heb())
    # 3 Tishrei 5786 is a Thursday (25 Sep 2025) — Tzom Gedaliah, not deferred.
    fast = p.parse("5786-Tishrei-03")
    assert any("Gedalia" in h["name"] for h in p.holidays(fast, fast))
    custom = get_provider({"provider": "hebrew", "region": "",
                           "custom_holidays": [{"name": "Grandma's yahrzeit", "month": "Shevat", "day": 10}],
                           "anchor": None})
    day = custom.parse("5786-Shevat-10")
    assert any(h["name"] == "Grandma's yahrzeit" for h in custom.holidays(day, day))


def test_age_and_anniversary_across_tishrei_and_adar():
    p = get_provider(heb())
    # born 10 Tishrei 5750; year rolls at Rosh Hashanah
    birth = "5750-Tishrei-10"
    from grimoire.store.calendars import age, is_anniversary
    assert age(p, birth, "5786-Tishrei-09") == 35
    assert age(p, birth, "5786-Tishrei-10") == 36
    assert is_anniversary(p, birth, "5786-Tishrei-10") is True
    # born in Adar II of leap 5784 → observed in plain Adar of common 5786
    assert is_anniversary(p, "5784-Adar2-14", "5786-Adar-14") is True
    # born 30 Cheshvan (long 5785) → observed 29 Cheshvan when short (5786)
    assert is_anniversary(p, "5785-Cheshvan-30", "5786-Cheshvan-29") is True


def test_validate_rule_hebrew():
    p = get_provider(heb())
    p.validate_rule({"name": "OK", "month": "Adar", "day": 14})
    p.validate_rule({"name": "OK", "month": "Kislev", "day": 30})
    for bad in ({"name": "X", "month": "Adar", "day": 30},      # Adar caps at 29
                {"name": "X", "month": "Floof", "day": 1},
                {"name": "X", "month": "Elul", "nth": 1, "weekday": 0}):  # nth-weekday off-Gregorian
        with pytest.raises(CalendarError):
            p.validate_rule(bad)
