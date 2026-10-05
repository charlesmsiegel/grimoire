"""`continuity.pressure`: one dated item list over events, holidays, birthdays
and deadlines, with a fixed state contract (spec §13).

Every assertion about a date is written on the fixed-day axis of the
campaign's own primary calendar (`F(...)`), never as a Gregorian literal, so
the same contract is checked on Gregorian, Hebrew and a plugin calendar.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from grimoire.store import (
    birthdays,
    calendars,
    campaigns,
    characters,
    clock,
    commitments,
    events,
    notices,
    overlay,
    plot,
    worlds,
)
from grimoire.store.continuity import pressure

#: A plugin calendar on a bare integer axis: 360-day years of twelve 30-day
#: months, and a "Tithe" observance on every tenth day. Reused by Task 6.
_TITHE_PROVIDER_SRC = textwrap.dedent(
    """
    from grimoire.store.calendars.base import CalendarError, CalendarProvider, register

    class _TitheProvider(CalendarProvider):
        def __init__(self, config):
            self.custom_holidays = config.get("custom_holidays", []) or []

        def parse(self, native):
            text = str(native).strip()
            if not text.lstrip("-").isdigit() or not text.lstrip("-").isascii():
                raise CalendarError(f"bad tithe date: {native!r}")
            return int(text)

        def format(self, fixed):
            return str(fixed)

        def describe(self, fixed):
            year, month, day = fixed // 360, fixed % 360 // 30 + 1, fixed % 30 + 1
            return {"year": year, "month": month, "month_name": f"Month {month:02d}",
                    "day": day, "weekday_name": "Tithesday", "weekday_index": 0,
                    "friendly": f"day {day} of month {month:02d}, year {year}"}

        def holidays(self, start_fixed, end_fixed):
            return [{"name": "Tithe", "fixed": f}
                    for f in range(start_fixed, end_fixed + 1) if f % 10 == 0]

        def months(self, year):
            return [{"key": f"{m:02d}", "name": f"Month {m:02d}", "days": 30}
                    for m in range(1, 13)]

    register("tithe-test-calendar", _TitheProvider, "Tithe Test Calendar")
    """
)

#: A plugin whose constructor raises something that is not a CalendarError --
#: the hand-written `<home>/calendars/*.py` of Review Focus 1. Every abstract
#: method is defined, so the RuntimeError (not an abstract-class TypeError) is
#: what reaches the caller.
_BROKEN_PROVIDER_SRC = textwrap.dedent(
    """
    from grimoire.store.calendars.base import CalendarProvider, register

    class _BrokenProvider(CalendarProvider):
        def __init__(self, config):
            raise RuntimeError("this calendar plugin is broken")

        def parse(self, native):
            return 0

        def format(self, fixed):
            return ""

        def describe(self, fixed):
            return {}

        def holidays(self, start_fixed, end_fixed):
            return []

        def months(self, year):
            return []

    register("broken-test-calendar", _BrokenProvider, "Broken Test Calendar")
    """
)


def _block(provider: str) -> dict:
    return {"provider": provider, "region": "", "custom_holidays": [], "anchor": None}


def _campaign(monkeypatch, tmp_path, *, calendar="gregorian", holidays=(), region="",
              warn=None, now="2026-05-10"):
    """A campaign whose calendar observes exactly `holidays` (`region=""`
    switches the Gregorian holiday library off), its clock at `now`."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, calendar=calendar)
    root = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {**cfg["primary"], "region": region, "custom_holidays": list(holidays)}
    if warn is not None:
        cfg["warn_days"] = warn
    calendars.write_calendar(root, cfg)
    if now is not None:
        clock.advance(cid, to=now)
    return cid


def _rule(name, month, day):
    return {"name": name, "month": month, "day": day}


def _root(cid):
    return campaigns.campaign_root(cid)


def _provider(cid):
    return calendars.primary_provider(_root(cid))


def F(cid, native):  # noqa: N802 - reads as the spec's F(...)
    return calendars.fixed_of(_provider(cid), native)


def _actor(cid, name, birth):
    aid, _vid = overlay.create_character(cid, name)
    characters.set_birthdate(_root(cid), aid, birth)
    return aid


def _plugin(tmp_path, name, src):
    directory = tmp_path / "calendars"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.py").write_text(src, encoding="utf-8")


def _primary(cid, provider_id, secondary=None):
    root = _root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = _block(provider_id)
    cfg["secondary"] = secondary
    calendars.write_calendar(root, cfg)


def _tithe(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, now=None)
    _plugin(tmp_path, "tithe_test", _TITHE_PROVIDER_SRC)
    _primary(cid, "tithe-test-calendar")
    clock.advance(cid, to="735")
    return cid


def _items(cid, kind=None, **kw):
    out = pressure.build(cid, **kw)["items"]
    return [i for i in out if kind is None or i["kind"] == kind]


# ---- the contract -----------------------------------------------------------


def test_pressure_tuples_are_pinned():
    kinds = ("event", "holiday", "birthday", "deadline", "linked_deadline")
    precedence = ("overdue", "passed", "today", "due_soon", "upcoming", "stale", "ok")
    ui_order = ("overdue", "today", "due_soon", "upcoming", "passed", "stale", "ok")
    assert kinds == pressure.SOURCES
    assert kinds == pressure.KINDS
    assert frozenset(kinds) == pressure.ALL
    assert precedence == pressure.PRESSURE_STATES
    assert ui_order == pressure.SORT_ORDER
    assert frozenset({"overdue", "today", "due_soon"}) == pressure.HIGH_PRESSURE


@pytest.mark.parametrize(("kind", "relation", "in_days", "warn", "passed", "reached", "state"), [
    ("deadline", "on", -1, 7, False, False, "overdue"),
    ("deadline", "on", 0, 7, False, False, "today"),
    ("deadline", "on", 7, 7, False, False, "due_soon"),
    ("deadline", "on", 8, 7, False, False, "upcoming"),
    ("deadline", "on", 30, 7, False, False, "upcoming"),
    ("deadline", "on", 31, 7, False, False, "ok"),
    ("deadline", "on", 3, 0, False, False, "upcoming"),
    ("deadline", "on", None, 7, False, False, "ok"),
    ("linked_deadline", "before", -3, 7, False, False, "overdue"),
    ("linked_deadline", "by", 5, 7, False, True, "overdue"),
    ("linked_deadline", "by", 0, 7, False, True, "today"),
    ("linked_deadline", "on", 0, 7, False, True, "today"),
    ("linked_deadline", "before", 0, 7, False, True, "overdue"),
    ("linked_deadline", "by", None, 7, False, True, "overdue"),
    ("linked_deadline", "after", -3, 7, False, False, "ok"),
    ("linked_deadline", "after", 2, 7, False, False, "upcoming"),
    ("event", "on", -2, 7, True, False, "passed"),
    ("event", "on", 0, 7, False, False, "today"),
    ("holiday", "on", 30, 7, False, False, "upcoming"),
    ("birthday", "in_month", None, 7, False, False, "ok"),
])
def test_state_precedence(kind, relation, in_days, warn, passed, reached, state):
    assert pressure.state_of(kind, relation, in_days, warn_days=warn,
                             passed=passed, reached=reached) == state


def test_unknown_source_is_a_value_error(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        pressure.build(cid, sources={"event", "omen"})


# ---- holidays ---------------------------------------------------------------


def test_several_holidays_are_kept_and_today_included(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[
        _rule("The coronation", "05", 10), _rule("Saltmarch Eve", "05", 12),
        _rule("Mara's map", "05", 20)])
    items = _items(cid, "holiday")
    assert [i["in_days"] for i in items] == [0, 2, 10]
    assert [i["state"] for i in items] == ["today", "upcoming", "upcoming"]
    for i in items:
        assert i["ref"] == notices.holiday_key(i["fixed"], i["label"])
        assert (i["relation"], i["subject"], i["due_text"], i["precision"]) == (
            "on", None, "", "exact")


def test_holiday_ref_matches_the_notice_key_for_an_untrimmed_name(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve ", "05", 12)])
    [item] = _items(cid, "holiday")
    fixed = F(cid, "2026-05-12")
    assert item["ref"] == notices.holiday_key(fixed, "Saltmarch Eve ")
    assert item["ref"].endswith(":Saltmarch Eve")


def test_one_holiday_item_per_ref(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[
        _rule("Saltmarch Eve", "05", 12), _rule("Saltmarch Eve ", "05", 12)])
    ref = notices.holiday_key(F(cid, "2026-05-12"), "Saltmarch Eve")
    assert [i["ref"] for i in _items(cid, "holiday")] == [ref]


def test_holiday_in_days_counts_from_now(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    [item] = _items(cid, "holiday")
    assert item["in_days"] == 2
    assert item["fixed"] == F(cid, "2026-05-12")
    assert item["native"] == "2026-05-12"


def test_holidays_and_birthdays_stop_at_the_horizon(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[
        _rule("Saltmarch Eve", "06", 20), _rule("The coronation", "06", 9),
        _rule("Mara's map", "06", 10)])
    _actor(cid, "Mara", "1985-06-20")
    by_default = {i["label"] for i in _items(cid) if i["kind"] in {"holiday", "birthday"}}
    assert by_default == {"The coronation"}
    wider = {(i["kind"], i["label"]) for i in _items(cid, horizon=45)}
    assert {("holiday", "Saltmarch Eve"), ("birthday", "Mara")} <= wider


def test_a_negative_horizon_is_clamped_to_today(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[
        _rule("The coronation", "05", 10), _rule("Saltmarch Eve", "05", 12)])
    assert [i["label"] for i in _items(cid, "holiday", horizon=-5)] == ["The coronation"]


def test_a_raising_secondary_costs_only_holidays(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    events.create(cid, "The coronation", "2026-05-20")
    _actor(cid, "Mara", "1985-05-12")
    before = _items(cid)
    assert {i["kind"] for i in before} == {"event", "holiday", "birthday"}

    _plugin(tmp_path, "broken_test", _BROKEN_PROVIDER_SRC)
    root = _root(cid)
    cfg = calendars.read_calendar(root)
    cfg["secondary"] = _block("broken-test-calendar")
    calendars.write_calendar(root, cfg)

    after = _items(cid)
    assert [i for i in after if i["kind"] == "holiday"] == []
    assert after == [i for i in before if i["kind"] != "holiday"]


# ---- events -----------------------------------------------------------------


def test_events_listed_regardless_of_horizon_and_ordered_on_the_fixed_axis(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    coronation = events.create(cid, "The coronation", "2026-08-01")
    audience = events.create(cid, "Mara's audience", "2026-05-12")
    items = _items(cid)
    assert [i["ref"] for i in items] == [
        f"event:{audience}", notices.holiday_key(F(cid, "2026-05-12"), "Saltmarch Eve"),
        f"event:{coronation}"]
    last = items[-1]
    assert (last["state"], last["in_days"], last["fixed"]) == ("ok", 83, F(cid, "2026-08-01"))
    for i in (items[0], last):
        assert (i["kind"], i["relation"], i["subject"], i["due_text"], i["precision"]) == (
            "event", "on", None, "", "exact")
        assert (i["actor"], i["age"]) == (None, None)
    assert items[0]["label"] == "Mara's audience"
    assert items[0]["state"] == "upcoming"


def test_a_passed_unfired_event_is_passed_and_a_fired_one_is_gone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, now="2026-05-01")
    fired = events.create(cid, "The coronation", "2026-05-08")
    clock.advance(cid, to="2026-05-10")
    assert events.get(cid, fired)["fired"] is not None
    passed = events.create(cid, "Mara's audience", "2026-05-05")
    items = _items(cid, "event")
    assert [i["ref"] for i in items] == [f"event:{passed}"]
    assert (items[0]["state"], items[0]["in_days"]) == ("passed", -5)


def test_an_event_with_a_time_is_day_level(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-12T21:00")
    eid = events.create(cid, "The coronation", "2026-05-12T20:00")
    [item] = _items(cid, "event")
    assert item["ref"] == f"event:{eid}"
    assert item["fixed"] == F(cid, "2026-05-12")
    assert (item["in_days"], item["state"], item["native"]) == (0, "today", "2026-05-12T20:00")
    assert item["friendly"] == calendars.friendly(_provider(cid), "2026-05-12")


def test_a_fired_event_on_today_is_today(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The coronation", "2026-05-12")
    clock.advance(cid, to="2026-05-12")
    assert events.get(cid, eid)["fired"] is not None
    [item] = _items(cid, "event")
    assert (item["ref"], item["state"], item["in_days"]) == (f"event:{eid}", "today", 0)


def test_an_undated_event_is_listed_last_and_ok(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The coronation", "2026-05-12")
    path = _root(cid) / "events.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["midsummer-rite"] = {"name": "The Saltmarch rite", "date": "midsummer",
                              "note": "", "fired": None}
    path.write_text(json.dumps(data), encoding="utf-8")
    items = _items(cid, "event")
    assert [i["ref"] for i in items] == [f"event:{eid}", "event:midsummer-rite"]
    assert (items[-1]["fixed"], items[-1]["in_days"], items[-1]["state"],
            items[-1]["precision"], items[-1]["native"]) == (None, None, "ok", None, "midsummer")


# ---- birthdays --------------------------------------------------------------


def test_birthday_items_carry_precision_actor_and_age(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara = _actor(cid, "Mara", "1985-05-12")
    winifred = _actor(cid, "Winifred", "--06")
    items = _items(cid)
    [m] = [i for i in items if i["actor"] == f"characters:{mara}"]
    assert (m["kind"], m["relation"], m["in_days"], m["age"], m["precision"], m["state"]) == (
        "birthday", "on", 2, 41, "exact", "upcoming")
    assert m["fixed"] == F(cid, "2026-05-12")
    assert m["label"] == "Mara"
    assert (m["subject"], m["due_text"]) == (None, "")
    [w] = [i for i in items if i["actor"] == f"characters:{winifred}"]
    assert (w["relation"], w["fixed"], w["in_days"], w["state"], w["precision"]) == (
        "in_month", None, None, "ok", "month")
    dated = [n for n, i in enumerate(items) if i["fixed"] is not None]
    assert dated and items.index(w) > max(dated)


# ---- other calendars --------------------------------------------------------


def test_hebrew_axis_keeps_every_chanukah_day(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew", now="5786-Kislev-24")
    chanuka = [i for i in _items(cid, "holiday") if i["label"] == "Chanuka"]
    assert len(chanuka) >= 3
    assert len({i["ref"] for i in chanuka}) == len(chanuka)
    assert [i["fixed"] for i in chanuka] == sorted(i["fixed"] for i in chanuka)


def test_hebrew_birthday_and_event_ordering(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew", now="5786-Kislev-24")
    provider = _provider(cid)
    mara = _actor(cid, "Mara", "5745-Kislev-26")
    eid = events.create(cid, "The coronation", "5786-Kislev-26")
    day = F(cid, "5786-Kislev-26")
    items = _items(cid)

    [b] = [i for i in items if i["kind"] == "birthday"]
    assert (b["actor"], b["precision"], b["relation"], b["in_days"], b["fixed"]) == (
        f"characters:{mara}", "exact", "on", 2, day)
    assert b["age"] == provider.age(F(cid, "5745-Kislev-26"), day) == 41

    on_day = [i for i in items if i["fixed"] == day]
    kinds = [i["kind"] for i in on_day]
    assert on_day[0]["ref"] == f"event:{eid}"
    assert kinds == ["event"] + ["holiday"] * (len(kinds) - 2) + ["birthday"]
    assert "Chanuka" in {i["label"] for i in on_day if i["kind"] == "holiday"}
    first = items.index(on_day[0])
    last = items.index(on_day[-1])
    assert any(i["fixed"] == F(cid, "5786-Kislev-25") and i["label"] == "Chanuka"
               for i in items[:first])
    assert all(i["fixed"] is not None and i["fixed"] < day for i in items[:first])
    assert all(i["fixed"] is None or i["fixed"] > day for i in items[last + 1:])


def test_fake_provider_axis(monkeypatch, tmp_path):
    cid = _tithe(monkeypatch, tmp_path)
    eid = events.create(cid, "The coronation", "736")
    out = pressure.build(cid)
    assert out["fixed"] == 735
    assert out["now"] == "735"
    holidays = [i for i in out["items"] if i["kind"] == "holiday"]
    assert [i["in_days"] for i in holidays] == [5, 15, 25]
    assert [i["native"] for i in holidays] == ["740", "750", "760"]
    assert out["items"][0]["ref"] == f"event:{eid}"
    assert out["items"][0]["in_days"] == 1


def test_fake_provider_birthdays(monkeypatch, tmp_path):
    cid = _tithe(monkeypatch, tmp_path)
    mara = _actor(cid, "Mara", "378")
    winifred = _actor(cid, "Winifred", "--02")
    seraphine = _actor(cid, "Seraphine", "--01-20")
    items = _items(cid, "birthday")
    [m] = [i for i in items if i["actor"] == f"characters:{mara}"]
    assert (m["in_days"], m["age"], m["precision"], m["relation"]) == (3, 1, "exact", "on")
    [w] = [i for i in items if i["actor"] == f"characters:{winifred}"]
    assert (w["relation"], w["fixed"], w["in_days"]) == ("in_month", None, None)
    assert not [i for i in items if i["actor"] == f"characters:{seraphine}"]


# ---- degradation ------------------------------------------------------------


def test_no_now_means_no_dates_are_invented(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, now=None,
                    holidays=[_rule("Saltmarch Eve", "05", 12)])
    _actor(cid, "Mara", "1985-05-12")
    eid = events.create(cid, "The coronation", "2026-05-12")
    out = pressure.build(cid)
    assert (out["fixed"], out["friendly"]) == (None, "")
    [item] = out["items"]
    assert item["ref"] == f"event:{eid}"
    assert item["fixed"] == F(cid, "2026-05-12")
    assert (item["in_days"], item["state"]) == (None, "ok")


def test_a_raising_plugin_degrades_to_undated_items(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    eid = events.create(cid, "The coronation", "2026-05-12")
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "2026-05-14", "Mara swore it at the gate.", "001--gate")
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                      "The map turned up in Saltmarch.", "001--gate")
    _actor(cid, "Mara", "1985-05-12")

    _plugin(tmp_path, "broken_test", _BROKEN_PROVIDER_SRC)
    _primary(cid, "broken-test-calendar")
    with pytest.raises(RuntimeError):
        calendars.primary_provider(_root(cid))

    out = pressure.build(cid)
    assert (out["fixed"], out["friendly"]) == (None, "")
    [item] = out["items"]
    assert (item["ref"], item["kind"]) == (f"event:{eid}", "event")
    assert (item["fixed"], item["in_days"], item["state"]) == (None, None, "ok")
    assert not [i for i in out["items"]
                if i["kind"] in {"holiday", "birthday", "deadline", "linked_deadline"}]


def test_sources_restrict_the_work_done(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(birthdays, "occurrences",
                        lambda *a, **k: calls.append("birthday") or [])
    monkeypatch.setattr(calendars, "upcoming_holidays",
                        lambda *a, **k: calls.append("holiday") or [])
    pressure.build(cid, sources={"deadline", "linked_deadline"})
    assert calls == []
    pressure.build(cid)
    assert sorted(calls) == ["birthday", "holiday"]
