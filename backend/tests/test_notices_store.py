"""Warn-once pre-notices (#106): the store half.

Two halves of one feature, and they fail differently. *What is imminent* is
recomputed every call, so its bugs are window bugs — an event a day too far out,
a holiday counted on the day it lands. *Whether the reader has been warned* is
the only state, so its bugs are memory bugs — a dismissal that does not stick,
or one that sticks past the occurrence it was about and swallows next year's.
Both are exercised here; the HTTP surface is `test_notices_routes.py`.
"""

from __future__ import annotations

import json

from grimoire.store import calendars, campaigns, events, notices, worlds

# The campaign's present, and the days around it, in the primary calendar's own
# notation. A fixed literal rather than "today": every assertion below is
# arithmetic on a window, and a test whose answers move with the wall clock
# would pass on most days and fail near a real holiday.
NOW = "2026-05-10"


def _campaign(monkeypatch, tmp_path, warn=None, holidays=(), region=""):
    """A campaign whose calendar observes exactly `holidays` and nothing else.

    `region=""` switches the holiday library off, so the window under test holds
    what this test put in it — otherwise the answers depend on which country's
    observances happen to fall near NOW, and on the library's version.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    cfg["primary"] = {**cfg["primary"], "region": region, "custom_holidays": list(holidays)}
    if warn is not None:
        cfg["warn_days"] = warn
    calendars.write_calendar(campaigns.campaign_root(cid), cfg)
    return cid


def _root(cid):
    return campaigns.campaign_root(cid)


def _pending(cid, native=NOW, **kw):
    return notices.pending(cid, _root(cid), native, **kw)


def _names(rows):
    return [r["name"] for r in rows]


def _rule(name, month, day):
    return {"name": name, "month": month, "day": day}


# ---- the window: what counts as imminent ----------------------------------

def test_a_fresh_campaign_has_nothing_to_warn_about(monkeypatch, tmp_path):
    assert _pending(_campaign(monkeypatch, tmp_path)) == []


def test_a_holiday_inside_the_window_is_a_notice(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    rows = _pending(cid)
    assert _names(rows) == ["Saltmarch Eve"]
    assert rows[0]["kind"] == "holiday"
    assert rows[0]["in_days"] == 3
    assert rows[0]["friendly"]


def test_a_holiday_past_the_window_is_not(monkeypatch, tmp_path):
    """`warn_days` is the warn window, NOT `UPCOMING_WINDOW_DAYS`. A month of
    lead time is what the prompt's "Upcoming:" line reads; a banner that fires
    a month out is noise by the day it is about."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "06", 1)])
    assert _pending(cid) == []
    assert calendars.upcoming_holidays(
        calendars.read_calendar(_root(cid)), NOW)[0]["name"] == "Saltmarch Eve"


def test_the_last_day_of_the_window_still_warns(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7, holidays=[_rule("Saltmarch Eve", "05", 17)])
    assert [r["in_days"] for r in _pending(cid)] == [7]


def test_the_day_after_the_window_does_not(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7, holidays=[_rule("Saltmarch Eve", "05", 18)])
    assert _pending(cid) == []


def test_today_is_not_a_pre_notice(monkeypatch, tmp_path):
    """A thing happening today has no lead time left to warn about, and the
    scene panel already says what today's observances are."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 10)])
    assert _pending(cid) == []
    assert "Saltmarch Eve" in calendars.today_facts(
        calendars.read_calendar(_root(cid)), NOW)["holidays_today"]


def test_a_scheduled_event_inside_the_window_is_a_notice(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "The envoy arrives", "2026-05-12")
    rows = _pending(cid)
    assert _names(rows) == ["The envoy arrives"]
    assert rows[0]["kind"] == "event"
    assert rows[0]["in_days"] == 2


def test_a_fired_event_no_longer_warns(monkeypatch, tmp_path):
    """The clock reaching an event is what stops it being upcoming — and an
    event that already happened must not be warned about from a scene set
    before it."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The envoy arrives", "2026-05-12")
    events.fire(cid, [eid], "2026-05-12")
    assert _pending(cid) == []


def test_events_and_holidays_share_one_list_soonest_first(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 14)])
    events.create(cid, "The envoy arrives", "2026-05-12")
    assert _names(_pending(cid)) == ["The envoy arrives", "Saltmarch Eve"]


def test_a_day_carrying_both_leads_with_the_authored_event(monkeypatch, tmp_path):
    """The reader wrote one of them down; that is the one a scene is likelier
    to be planned around."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    events.create(cid, "The envoy arrives", "2026-05-12")
    assert [r["kind"] for r in _pending(cid)] == ["event", "holiday"]


def test_warn_days_of_zero_switches_the_warnings_off(monkeypatch, tmp_path):
    """Zero is a setting somebody can mean here, unlike a staleness threshold
    of zero — which is why the config's no-opinion value is None, not 0."""
    cid = _campaign(monkeypatch, tmp_path, warn=0, holidays=[_rule("Saltmarch Eve", "05", 11)])
    assert _pending(cid) == []


def test_an_explicit_window_overrides_the_configured_one(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=2, holidays=[_rule("Saltmarch Eve", "05", 20)])
    assert _pending(cid) == []
    assert _names(_pending(cid, window=30)) == ["Saltmarch Eve"]


# ---- the memory: warn once, and only about this occurrence -----------------

def test_marking_silences_a_notice(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    assert notices.mark(cid, [key], scene="scene-1") == [key]
    assert _pending(cid) == []


def test_once_ness_is_campaign_wide_not_per_scene(monkeypatch, tmp_path):
    """The whole point of the ledger. Per-scene dismissals (the cast
    suggestions' shape) would put the banner back in every new scene, which is
    exactly the nag this feature exists to avoid."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    notices.mark(cid, [_pending(cid)[0]["key"]], scene="scene-1")
    # A second scene, a day later, still inside the window: same occurrence.
    assert _pending(cid, "2026-05-11") == []


def test_next_year_warns_again(monkeypatch, tmp_path):
    """The key names an occurrence, not an event — same name, different day."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    notices.mark(cid, [_pending(cid)[0]["key"]])
    assert _names(_pending(cid, "2027-05-10")) == ["Saltmarch Eve"]


def test_re_dating_an_event_warns_again(monkeypatch, tmp_path):
    """Same reason: the dismissal was about a day, and the day changed."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The envoy arrives", "2026-05-12")
    notices.mark(cid, [_pending(cid)[0]["key"]])
    assert _pending(cid) == []
    events.update(cid, eid, date="2026-05-14")
    assert _names(_pending(cid)) == ["The envoy arrives"]


def test_marking_twice_keeps_the_first_stamp(monkeypatch, tmp_path):
    """The stamp says when the reader was first warned. Two surfaces showing
    one notice must not make the second dismissal rewrite that."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices.mark(cid, [key], scene="scene-1")
    first = notices.read(cid)[key]
    assert notices.mark(cid, [key], scene="scene-2") == []
    assert notices.read(cid)[key] == first


def test_forget_puts_a_notice_back(monkeypatch, tmp_path):
    """`mark` will not overwrite a stamp, so without this one misclick silences
    an occurrence until its day has gone by."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices.mark(cid, [key])
    assert notices.forget(cid, [key]) == [key]
    assert _names(_pending(cid)) == ["Saltmarch Eve"]
    assert notices.forget(cid, [key]) == []


def test_marking_nothing_writes_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert notices.mark(cid, []) == []
    assert notices.mark(cid, ["   "]) == []
    assert not notices._path(cid).exists()


def test_a_key_is_bounded(monkeypatch, tmp_path):
    """The key is stored verbatim — holiday names are not slugs — so the length
    cap is what stops a crafted dismissal writing an unbounded file."""
    cid = _campaign(monkeypatch, tmp_path)
    (key,) = notices.mark(cid, ["holiday:1:" + "x" * 5000])
    assert len(key) == notices.KEY_LIMIT


# ---- the file, which a reader may have edited ------------------------------

def test_a_garbled_ledger_reads_as_no_dismissals(monkeypatch, tmp_path):
    """A reader loses its dismissals — the banner comes back — never the panel
    around it. `events.read` and `plot.read` draw the same line."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    notices.mark(cid, [_pending(cid)[0]["key"]])
    notices._path(cid).write_text("{not json", encoding="utf-8")
    assert notices.read(cid) == {}
    assert _names(_pending(cid)) == ["Saltmarch Eve"]


def test_a_ledger_of_the_wrong_shape_reads_as_no_dismissals(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    notices._path(cid).write_text("[]", encoding="utf-8")
    assert notices.read(cid) == {}


def test_a_hand_written_row_still_silences(monkeypatch, tmp_path):
    """The ledger is a set of strings and knows nothing about events, which is
    what lets another domain's warnings dismiss into the same keyspace."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices._path(cid).write_text(json.dumps({key: {}}), encoding="utf-8")
    assert _pending(cid) == []


def test_the_ledger_is_capped(monkeypatch, tmp_path):
    """It only ever grows, one row per acknowledged occurrence. Eviction is
    safe because a row's key names a day, and `pending` only looks ahead: the
    days evicted first are the ones furthest behind."""
    cid = _campaign(monkeypatch, tmp_path)
    notices.mark(cid, [f"holiday:{n}:Old" for n in range(notices.LEDGER_LIMIT)])
    notices.mark(cid, ["holiday:999999:New"])
    data = notices.read(cid)
    assert len(data) == notices.LEDGER_LIMIT
    assert "holiday:999999:New" in data
    assert "holiday:0:Old" not in data


def test_a_calendar_that_will_not_load_warns_about_nothing(monkeypatch, tmp_path):
    """Every caller is a panel: it must degrade to showing nothing rather than
    failing the page around it."""
    cid = _campaign(monkeypatch, tmp_path)
    root = campaigns.campaign_root(cid)
    (root / "calendar.json").write_text(
        json.dumps({"primary": {"provider": "no-such-calendar"}}), encoding="utf-8")
    assert notices.pending(cid, root, NOW) == []


def test_a_moment_this_calendar_cannot_read_warns_about_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    assert _pending(cid, "the night of the long knives") == []
    assert _pending(cid, "") == []


# ---- the warn window, as a hand-editable setting ---------------------------

def _write_raw_calendar(cid, **fields):
    (_root(cid) / "calendar.json").write_text(
        json.dumps({"primary": {"provider": "gregorian"}, **fields}), encoding="utf-8")


def test_a_missing_warn_days_is_the_default(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _write_raw_calendar(cid)
    assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS


def test_an_unusable_warn_days_is_the_default(monkeypatch, tmp_path):
    """calendar.json is hand-editable and this number reaches fixed-day
    arithmetic — `"soon"` must not raise inside a panel read."""
    cid = _campaign(monkeypatch, tmp_path)
    for bad in ("soon", None, True, -3, {"days": 7}):
        _write_raw_calendar(cid, warn_days=bad)
        assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS


def test_a_warn_days_of_zero_is_kept(monkeypatch, tmp_path):
    """Unlike a staleness threshold of zero, this one is a setting somebody
    means: no warnings in this campaign."""
    cid = _campaign(monkeypatch, tmp_path)
    _write_raw_calendar(cid, warn_days=0)
    assert calendars.warn_days(_root(cid)) == 0


def test_an_absurd_warn_days_is_capped(monkeypatch, tmp_path):
    """Every day of the window is a day the calendar provider — possibly a
    user-authored plugin — is asked to enumerate."""
    cid = _campaign(monkeypatch, tmp_path)
    _write_raw_calendar(cid, warn_days=100000)
    assert calendars.warn_days(_root(cid)) == calendars.MAX_WARN_DAYS


def test_a_corrupt_calendar_answers_the_default_window(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (_root(cid) / "calendar.json").write_text("{not json", encoding="utf-8")
    assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS
