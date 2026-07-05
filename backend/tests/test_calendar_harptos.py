import pytest

from grimoire.store.calendars import CalendarError, get_provider


def har(custom=None):
    return {"provider": "harptos", "region": "", "custom_holidays": custom or [], "anchor": None}


def test_epoch_and_roundtrip():
    p = get_provider(har())
    assert p.parse("1-Hammer-01") == 1
    for native in ("1492-Mirtul-05", "1492-Midsummer-01", "1492-Shieldmeet-01",
                   "1491-Nightal-30", "1492-FeastOfTheMoon-01"):
        assert p.format(p.parse(native)) == native


def test_year_lengths_and_festival_ordering():
    p = get_provider(har())
    # 1491 is common (365), 1492 is leap (366): Shieldmeet exists
    assert p.parse("1492-Hammer-01") - p.parse("1491-Hammer-01") == 365
    assert p.parse("1493-Hammer-01") - p.parse("1492-Hammer-01") == 366
    # Midwinter sits between Hammer 30 and Alturiak 1
    assert p.parse("1492-Midwinter-01") == p.parse("1492-Hammer-30") + 1
    assert p.parse("1492-Alturiak-01") == p.parse("1492-Midwinter-01") + 1
    with pytest.raises(CalendarError):
        p.parse("1491-Shieldmeet-01")   # not a leap year
    with pytest.raises(CalendarError):
        p.parse("1492-Mirtul-31")
    with pytest.raises(CalendarError):
        p.parse("1492-Floof-01")


def test_describe_tenday_and_festivals():
    p = get_provider(har())
    d = p.describe(p.parse("1492-Mirtul-05"))
    assert d["weekday_name"] == "5th day of the tenday"
    assert d["weekday_index"] == 4
    assert d["month_name"] == "Mirtul"
    assert d["month"] == 7                      # stable index: Mirtul is 7th slot
    f = p.describe(p.parse("1492-Midsummer-01"))
    assert f["weekday_name"] == "festival day"
    assert f["weekday_index"] is None
    assert f["friendly"].startswith("Midsummer, 1492 DR")
    # stable month indices: Eleasis is slot 12 in leap AND common years
    assert p.describe(p.parse("1492-Eleasis-01"))["month"] == 12
    assert p.describe(p.parse("1491-Eleasis-01"))["month"] == 12


def test_months_lists_and_age():
    p = get_provider(har())
    common, leap = p.months(1491), p.months(1492)
    assert len(common) == 17 and len(leap) == 18
    assert [m["key"] for m in leap][9:12] == ["Midsummer", "Shieldmeet", "Eleasis"]
    assert next(m for m in leap if m["key"] == "FeastOfTheMoon")["name"] == "Feast of the Moon"
    from grimoire.store.calendars import age, is_anniversary
    assert age(p, "1450-Eleasis-05", "1492-Eleasis-04") == 41
    assert age(p, "1450-Eleasis-05", "1492-Eleasis-05") == 42
    # birthday works across the Shieldmeet insertion (born common year, asof leap)
    assert is_anniversary(p, "1451-Eleasis-05", "1492-Eleasis-05") is True


def test_negative_and_zero_years():
    p = get_provider(har())
    assert p.format(p.parse("0-Hammer-01")) == "0-Hammer-01"
    assert p.format(p.parse("-100-Nightal-30")) == "-100-Nightal-30"
    assert p.parse("1-Hammer-01") - p.parse("0-Hammer-01") == 366  # year 0 is leap (0 % 4 == 0)
    # deep negative years: the year-estimate drift must not corrupt dates
    for native in ("-488-Nightal-30", "-1000-Hammer-01", "-3000-Mirtul-15"):
        assert p.format(p.parse(native)) == native


def test_friendly_includes_roll_of_years_name():
    from grimoire.store.calendars.harptos_years import YEAR_NAMES
    assert YEAR_NAMES[1492] == "Year of Three Ships Sailing"
    assert YEAR_NAMES[1372] == "Year of Wild Magic"
    assert len(YEAR_NAMES) > 1000
    p = get_provider(har())
    d = p.describe(p.parse("1492-Mirtul-05"))
    assert d["friendly"] == "5 Mirtul, 1492 DR (Year of Three Ships Sailing)"
    # unnamed years render without the suffix
    assert "(" not in p.describe(p.parse("9999-Hammer-01"))["friendly"]


def test_builtin_holidays_and_customs():
    p = get_provider(har())
    year_start, year_end = p.parse("1492-Hammer-01"), p.parse("1492-Nightal-30")
    names = [h["name"] for h in p.holidays(year_start, year_end)]
    for expected in ("Midwinter", "Greengrass", "Midsummer", "Shieldmeet",
                     "Highharvestide", "Feast of the Moon", "Spring Equinox",
                     "Summer Solstice", "Autumn Equinox", "Winter Solstice"):
        assert expected in names
    assert "Shieldmeet" not in [h["name"] for h in
                                get_provider(har()).holidays(p.parse("1491-Hammer-01"),
                                                             p.parse("1491-Nightal-30"))]
    custom = get_provider(har(custom=[{"name": "Founders' Day", "month": "Uktar", "day": 3}]))
    day = custom.parse("1492-Uktar-03")
    assert any(h["name"] == "Founders' Day" for h in custom.holidays(day, day))


def test_validate_rule_harptos():
    p = get_provider(har())
    p.validate_rule({"name": "OK", "month": "Uktar", "day": 3})
    p.validate_rule({"name": "OK", "month": "Shieldmeet", "day": 1})
    for bad in ({"name": "X", "month": "Uktar", "day": 31},
                {"name": "X", "month": "Midsummer", "day": 2},
                {"name": "X", "month": "Floof", "day": 1},
                {"name": "X", "month": "Uktar", "nth": 1, "weekday": 0}):
        with pytest.raises(CalendarError):
            p.validate_rule(bad)
