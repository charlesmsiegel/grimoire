import pytest

from grimoire.store.calendars import (CalendarError, CalendarProvider, get_provider, normalize,
                                       resolve, fixed_of,
                                       minutes_of, split_native)


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


def test_split_native_survives_month_names_containing_T():
    # Hebrew and Harptos month tokens contain a capital T; only a trailing
    # Thh:mm may be treated as a time suffix.
    assert split_native("5786-Tishrei-01") == ("5786-Tishrei-01", None)
    assert split_native("1492-Tarsakh-05") == ("1492-Tarsakh-05", None)
    assert split_native("5786-Tishrei-01T14:30") == ("5786-Tishrei-01", "14:30")
    assert split_native("1492-Tarsakh-05T9:05") == ("1492-Tarsakh-05", "9:05")


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


def test_normalize_zero_pads_time_for_stable_key():
    # normalize output is used as the canonical equality/dedup key — must be stable.
    p = get_provider(greg())
    assert normalize(p, "2026-06-29T9:5") == "2026-06-29T09:05"
    assert normalize(p, "2026-06-29T14:30") == "2026-06-29T14:30"


from grimoire.store.calendars import today_facts  # noqa: E402 - deliberate late import; see the lines above


def test_gregorian_library_holiday():
    p = get_provider(greg(region="US"))
    start = p.parse("2026-11-01")
    end = p.parse("2026-11-30")
    names = {h["name"] for h in p.holidays(start, end)}
    assert any("Thanksgiving" in n for n in names)


def test_custom_fixed_and_nth_weekday_holidays():
    custom = [
        {"name": "Founding Day", "month": 4, "day": 12},
        {"name": "Harvest Moon", "month": 9, "nth": 3, "weekday": 6},  # 3rd Sunday of Sept
    ]
    p = get_provider(greg(region="", custom=custom))
    founding = p.holidays(p.parse("2026-04-01"), p.parse("2026-04-30"))
    assert [h["name"] for h in founding] == ["Founding Day"]
    assert p.format(founding[0]["fixed"]) == "2026-04-12"
    harvest = p.holidays(p.parse("2026-09-01"), p.parse("2026-09-30"))
    assert [h["name"] for h in harvest] == ["Harvest Moon"]
    assert p.format(harvest[0]["fixed"]) == "2026-09-20"  # 3rd Sunday of Sept 2026


def test_today_facts_dateline_and_holiday():
    cfg = {"primary": greg(region="US"), "secondary": None}
    facts = today_facts(cfg, "2026-12-25")
    assert facts["friendly"] == "25 December 2026"
    assert facts["weekday"] == "Friday"
    assert facts["secondary_friendly"] is None
    assert "Christmas Day" in facts["holidays_today"]


def test_today_facts_merges_secondary_holidays():
    # Boxing Day (Dec 26) is a GB holiday, not US — proves the secondary merge.
    cfg = {"primary": greg(region="US"), "secondary": greg(region="GB")}
    facts = today_facts(cfg, "2026-12-26")
    assert facts["secondary_friendly"] == "26 December 2026"
    assert any("Boxing Day" in n for n in facts["holidays_today"])


def test_today_facts_upcoming_within_30_days():
    cfg = {"primary": greg(region="US"), "secondary": None}
    facts = today_facts(cfg, "2026-12-20")
    assert facts["upcoming"] == {"name": "Christmas Day", "in_days": 5}


from grimoire.store.calendars import age, is_anniversary  # noqa: E402 - deliberate late import; see the lines above


def test_age_and_anniversary():
    p = get_provider(greg())
    # born 1990-06-29; as of 2026-06-28 still 35, on 2026-06-29 turns 36
    assert age(p, "1990-06-29", "2026-06-28") == 35
    assert age(p, "1990-06-29", "2026-06-29") == 36
    assert is_anniversary(p, "1990-06-29", "2026-06-29") is True
    assert is_anniversary(p, "1990-06-29", "2026-06-30") is False
    # time-of-day on either side does not change the result
    assert age(p, "1990-06-29", "2026-06-29T08:00") == 36


from grimoire.store.calendars import default_calendar, read_calendar, write_calendar, copy_calendar  # noqa: E402 - deliberate late import; see the lines above


def test_default_calendar_when_absent(tmp_path):
    cfg = read_calendar(tmp_path)
    assert cfg["primary"]["provider"] == "gregorian"
    assert cfg["primary"]["region"] == "US"
    assert cfg["primary"]["custom_holidays"] == []
    assert cfg["primary"]["anchor"] is None
    assert cfg["secondary"] is None


def test_write_then_read_roundtrip(tmp_path):
    cfg = default_calendar()
    cfg["primary"]["region"] = "GB"
    cfg["secondary"] = {"provider": "gregorian", "region": "IL", "custom_holidays": [],
                        "anchor": {"native": "2026-06-29", "gregorian": "2026-06-29"}}
    write_calendar(tmp_path, cfg)
    got = read_calendar(tmp_path)
    assert got["primary"]["region"] == "GB"
    assert got["secondary"]["region"] == "IL"
    assert got["secondary"]["anchor"]["gregorian"] == "2026-06-29"


def test_read_fills_missing_keys(tmp_path):
    # a hand-written partial file still normalizes to the full shape
    (tmp_path / "calendar.json").write_text('{"primary": {"provider": "gregorian"}}', encoding="utf-8")
    cfg = read_calendar(tmp_path)
    assert cfg["primary"]["region"] == "US"
    assert cfg["primary"]["custom_holidays"] == []
    assert cfg["secondary"] is None


def test_read_calendar_tolerates_corrupt_json(tmp_path):
    (tmp_path / "calendar.json").write_text("{ this is not json", encoding="utf-8")
    cfg = read_calendar(tmp_path)  # must not raise JSONDecodeError
    assert cfg == default_calendar()


def test_validate_calendar_rejects_bad_custom_rules():
    from grimoire.store.calendars import validate_calendar
    validate_calendar(default_calendar())  # clean default is fine
    good = default_calendar()
    good["primary"]["custom_holidays"] = [
        {"name": "Founding Day", "month": 4, "day": 12},
        {"name": "Harvest", "month": 9, "nth": 3, "weekday": 6}]
    validate_calendar(good)  # both rule shapes valid
    bad_provider = default_calendar()
    bad_provider["primary"]["provider"] = "bogus"
    with pytest.raises(CalendarError):
        validate_calendar(bad_provider)
    for bad_rule in ({"name": "X", "month": 13},          # bad month
                     {"name": "X", "month": 4},            # neither day nor nth/weekday
                     {"month": 4, "day": 12},              # missing name
                     {"name": "X", "month": 2, "day": 30}):  # impossible day
        cfg = default_calendar()
        cfg["primary"]["custom_holidays"] = [bad_rule]
        with pytest.raises(CalendarError):
            validate_calendar(cfg)


def test_copy_calendar_copies_world_file(tmp_path):
    wroot, croot = tmp_path / "w", tmp_path / "c"
    wroot.mkdir(); croot.mkdir()
    cfg = default_calendar(); cfg["primary"]["region"] = "FR"
    write_calendar(wroot, cfg)
    copy_calendar(wroot, croot)
    assert read_calendar(croot)["primary"]["region"] == "FR"


def test_confirmed_defaults_false_and_roundtrips(tmp_path):
    assert default_calendar()["confirmed"] is False
    assert read_calendar(tmp_path)["confirmed"] is False  # no file yet
    cfg = default_calendar(); cfg["confirmed"] = True
    write_calendar(tmp_path, cfg)
    assert read_calendar(tmp_path)["confirmed"] is True


def test_copy_calendar_preserves_confirmed(tmp_path):
    wroot, croot = tmp_path / "w", tmp_path / "c"
    wroot.mkdir(); croot.mkdir()
    cfg = default_calendar(); cfg["confirmed"] = True
    write_calendar(wroot, cfg)
    copy_calendar(wroot, croot)
    assert read_calendar(croot)["confirmed"] is True


def test_gregorian_months_shape_and_leap_february():
    p = get_provider(greg())
    ms = p.months(2024)
    assert len(ms) == 12
    assert ms[0] == {"key": "01", "name": "January", "days": 31}
    assert ms[1]["days"] == 29                      # leap February
    assert p.months(2026)[1]["days"] == 28
    # composition contract: year-key-day parses
    assert p.format(p.parse("2026-02-28")) == "2026-02-28"
    with pytest.raises(CalendarError):
        p.months("nope")


def test_validate_rule_is_provider_aware():
    p = get_provider(greg())
    p.validate_rule({"name": "Founding Day", "month": 4, "day": 12})     # int month (legacy)
    p.validate_rule({"name": "Founding Day", "month": "04", "day": 12})  # key form
    p.validate_rule({"name": "Leap", "month": 2, "day": 29})             # Feb 29 allowed
    p.validate_rule({"name": "Harvest", "month": 9, "nth": 3, "weekday": 6})
    for bad in ({"name": "X", "month": 13, "day": 1},
                {"name": "X", "month": 4},
                {"month": 4, "day": 12},
                {"name": "X", "month": 2, "day": 30}):
        with pytest.raises(CalendarError):
            p.validate_rule(bad)


def test_today_facts_with_hebrew_primary():
    heb_cfg = {"primary": {"provider": "hebrew", "region": "", "custom_holidays": [],
                           "anchor": None}, "secondary": None}
    facts = today_facts(heb_cfg, "5786-Kislev-25")
    assert facts["friendly"] == "25 Kislev 5786"
    assert any("Chanuka" in n for n in facts["holidays_today"])


# ---- resolve(): accept any form the calendar itself renders ----

def heb():
    return {"provider": "hebrew", "region": "", "custom_holidays": [], "anchor": None}


def test_resolve_passes_canonical_dates_straight_through():
    """No window scan when the native form already parses — `near` is irrelevant."""
    p = get_provider(greg())
    assert resolve(p, "2026-06-29") == "2026-06-29"
    assert resolve(p, "2026-06-29T14:30") == "2026-06-29T14:30"


def test_resolve_accepts_the_friendly_form_the_prompt_displays():
    """The exact miss behind #hebrew-date-suggestions: prompts show `friendly`,
    so that is what a model echoes back."""
    p = get_provider(heb())
    assert resolve(p, "2 Tevet 5786", near="5786-Kislev-25") == "5786-Tevet-02"
    g = get_provider(greg())
    assert resolve(g, "29 June 2026", near="2026-06-01") == "2026-06-29"


def test_resolve_is_forgiving_about_case_spacing_and_commas():
    p = get_provider(heb())
    for written in ("2  tevet 5786", "2 TEVET 5786,", " 2 Tevet, 5786 "):
        assert resolve(p, written, near="5786-Kislev-25") == "5786-Tevet-02"


def test_resolve_refuses_a_date_outside_the_window():
    """Bounded by construction: a match ten years out is not searched for."""
    p = get_provider(heb())
    with pytest.raises(CalendarError):
        resolve(p, "2 Tevet 5796", near="5786-Kislev-25")


def test_resolve_refuses_text_the_calendar_never_renders():
    p = get_provider(heb())
    for junk in ("next Tuesday", "", "2 Smarch 5786"):
        with pytest.raises(CalendarError):
            resolve(p, junk, near="5786-Kislev-25")


def test_resolve_without_an_anchor_is_normalize_only():
    """No `near` means no window to scan, so the friendly form stays unreadable."""
    p = get_provider(heb())
    with pytest.raises(CalendarError):
        resolve(p, "2 Tevet 5786")


def test_resolve_ignores_an_anchor_this_calendar_cannot_read():
    """A garbled stored moment must not turn a bad date into a 500."""
    p = get_provider(heb())
    with pytest.raises(CalendarError):
        resolve(p, "2 Tevet 5786", near="not-a-date")


class _AmbiguousProvider(CalendarProvider):
    """A calendar whose `friendly` repeats every ten days, so a window scan can
    match on both sides of its anchor at once. Contrived, but a plugin is free
    to render anything, and `resolve` must still answer the same day every time."""

    def __init__(self, config=None):
        self.custom_holidays = []

    def parse(self, native):
        try:
            return int(native)
        except ValueError as e:                      # the contract `normalize` relies on
            raise CalendarError(f"bad date: {native!r}") from e

    def format(self, fixed):
        return str(fixed)

    def describe(self, fixed):
        return {"year": fixed, "month": 1, "month_name": "M", "day": 1,
                "weekday_name": "D", "weekday_index": 0, "friendly": f"day {fixed % 10}"}

    def holidays(self, start_fixed, end_fixed):
        return []

    def months(self, year):
        return [{"key": "01", "name": "M", "days": 1}]


def test_resolve_picks_the_forward_day_when_both_sides_match():
    """Deterministic, and forward: the caller is dating the NEXT scene."""
    p = _AmbiguousProvider()
    assert resolve(p, "day 5", near="1000") == "1005"   # 1005 and 995 both render "day 5"
    for _ in range(5):                                  # never hash-order dependent
        assert resolve(p, "day 5", near="1000") == "1005"


def test_resolve_keeps_a_time_of_day_through_the_fallback():
    """`normalize` carries the time; the tolerant path must not quietly drop it."""
    p = get_provider(heb())
    assert resolve(p, "2 Tevet 5786T21:30", near="5786-Kislev-25") == "5786-Tevet-02T21:30"
    assert resolve(p, "2 Tevet 5786T9:05", near="5786-Kislev-25") == "5786-Tevet-02T09:05"


def test_resolve_still_rejects_an_out_of_range_time():
    p = get_provider(heb())
    with pytest.raises(CalendarError):
        resolve(p, "2 Tevet 5786T25:00", near="5786-Kislev-25")


class _BrokenDescribeProvider(_AmbiguousProvider):
    def describe(self, fixed):
        return "not a mapping"


def test_resolve_does_not_swallow_a_provider_whose_describe_is_broken():
    """The rule `clock._holidays` states: row DATA is validated, a provider's
    method CONTRACT is not. A `describe` that cannot be read fails here the way
    it already fails in `friendly` and `today_facts`, rather than 800 times in
    silence."""
    with pytest.raises(TypeError):
        resolve(_BrokenDescribeProvider(), "day 5", near="1000")
