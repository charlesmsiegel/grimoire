"""Scheduled events (#101): dated campaign plans, and the clock reaching them.

The store half — CRUD, the tolerance a hand-editable file needs, the crossing
predicates, and the firing rules `clock` drives. The route half is
`test_events_routes.py`; the firing an advance does end to end is exercised in
both, because the two failures are different: a predicate that picks the wrong
events, and a mover that never asks.
"""

from __future__ import annotations

import json

import pytest
from grimoire.store import calendars, campaigns, clock, events, worlds


def _campaign(monkeypatch, tmp_path, calendar="gregorian"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid, calendar=calendar)


def _provider(cid):
    return calendars.primary_provider(campaigns.campaign_root(cid))


def _fixed(cid, native):
    return calendars.fixed_of(_provider(cid), native)


# ---- reading: a hand-editable file ----------------------------------------

def test_missing_file_reads_as_no_events(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert events.read(cid) == {}
    assert events.list_events(cid) == []


def test_garbled_file_reads_as_no_events(monkeypatch, tmp_path):
    """A reader must lose the list, never the turn — `clock.read`'s policy."""
    cid = _campaign(monkeypatch, tmp_path)
    events._path(cid).write_text("{not json", encoding="utf-8")
    assert events.read(cid) == {}


def test_a_writer_refuses_the_file_a_reader_tolerates(monkeypatch, tmp_path):
    """The asymmetry is the point: tolerating a corrupt file in a MUTATOR would
    publish `{}` over whatever the reader actually authored."""
    cid = _campaign(monkeypatch, tmp_path)
    events._path(cid).write_text("{not json", encoding="utf-8")
    with pytest.raises(events.EventError):
        events.create(cid, "The Envoy arrives", "2026-05-09")
    assert events._path(cid).read_text(encoding="utf-8") == "{not json"


def test_a_file_of_the_wrong_shape_is_refused_by_a_writer(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events._path(cid).write_text("[]", encoding="utf-8")
    with pytest.raises(events.EventError):
        events.delete(cid, "x")


def test_a_hand_edited_record_projects_as_text(monkeypatch, tmp_path):
    """React refuses an object as a child, so the projection makes types true."""
    cid = _campaign(monkeypatch, tmp_path)
    events._path(cid).write_text(json.dumps(
        {"x": {"name": {}, "date": [], "note": 3, "fired": "yes"}}), encoding="utf-8")
    assert events.list_events(cid) == [
        {"id": "x", "name": "x", "date": "", "friendly": "", "note": "", "fired": None,
         # No clock was handed in, so "has the campaign gone by this" is
         # unanswerable and answered False rather than guessed.
         "passed": False}]


# ---- creating, editing, deleting -------------------------------------------

def test_create_normalizes_the_date_and_returns_a_slug(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The Envoy Arrives", "2026-5-9", "From Saltmarch.")
    assert eid == "the-envoy-arrives"
    assert events.get(cid, eid)["date"] == "2026-05-09"
    assert events.get(cid, eid)["note"] == "From Saltmarch."


def test_two_events_may_share_a_name(monkeypatch, tmp_path):
    """A story can plan the same-sounding moment twice; the ids uniquify."""
    cid = _campaign(monkeypatch, tmp_path)
    first = events.create(cid, "The eclipse", "2026-05-09")
    second = events.create(cid, "The eclipse", "2027-05-09")
    assert first != second and len(events.list_events(cid)) == 2


def test_create_refuses_a_date_this_calendar_cannot_read(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(calendars.CalendarError):
        events.create(cid, "Nonsense", "the third of never")
    assert events.read(cid) == {}


def test_update_leaves_omitted_fields_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09", "In the old hall.")
    assert events.update(cid, eid, note="Moved to the new hall.")
    row = events.get(cid, eid)
    assert row["name"] == "Coronation" and row["date"] == "2026-05-09"
    assert row["note"] == "Moved to the new hall."


def test_update_of_an_unknown_event_is_false(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert events.update(cid, "nope", name="x") is False


def test_re_dating_does_not_clear_the_fire_stamp(monkeypatch, tmp_path):
    """The stamp records that the clock reached the day the event was on. It
    did; deciding the event should have been later does not un-happen it."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    events.fire(cid, [eid], "2026-05-09")
    events.update(cid, eid, date="2026-06-09")
    assert events.get(cid, eid)["fired"] is not None


def test_unfire_is_the_way_back(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    events.fire(cid, [eid], "2026-05-09")
    assert events.unfire(cid, eid) is True
    assert events.get(cid, eid)["fired"] is None


def test_delete_reports_whether_there_was_one(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    assert events.delete(cid, eid) is True
    assert events.delete(cid, eid) is False


# ---- ordering and labels ---------------------------------------------------

def test_list_orders_by_date_and_labels_it(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Later", "2026-12-01")
    events.create(cid, "Sooner", "2026-05-09")
    rows = events.list_events(cid, _provider(cid))
    assert [r["name"] for r in rows] == ["Sooner", "Later"]
    assert rows[0]["friendly"]


def test_an_unreadable_date_sorts_last_and_keeps_its_row(monkeypatch, tmp_path):
    """The row is the only place the reader can see, and fix, the bad date."""
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Real", "2026-05-09")
    data = events.read(cid)
    data["broken"] = {"name": "Broken", "date": "1492-Hammer-05", "note": "", "fired": None}
    events._path(cid).write_text(json.dumps(data), encoding="utf-8")
    rows = events.list_events(cid, _provider(cid))
    assert [r["name"] for r in rows] == ["Real", "Broken"]
    assert rows[-1]["friendly"] == ""


# ---- the crossing predicates ----------------------------------------------

def test_crossed_is_half_open_at_the_start(monkeypatch, tmp_path):
    """The day being left has already been lived through, so an event dated to
    it is not crossed by leaving it — `clock._holidays` draws the same line."""
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "On the day left", "2026-05-01")
    events.create(cid, "On the day reached", "2026-05-09")
    crossed = events.crossed(cid, _provider(cid), _fixed(cid, "2026-05-01"),
                             _fixed(cid, "2026-05-09"))
    assert [e["name"] for e in crossed] == ["On the day reached"]
    assert crossed[0]["in_days"] == 8


def test_crossed_skips_what_has_already_fired(monkeypatch, tmp_path):
    """Otherwise a correction backwards and a re-advance fires it twice."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    events.fire(cid, [eid], "2026-05-09")
    assert events.crossed(cid, _provider(cid), _fixed(cid, "2026-05-01"),
                          _fixed(cid, "2026-05-31")) == []


def test_upcoming_looks_ahead_of_the_moment(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Soon", "2026-05-09")
    events.create(cid, "Far off", "2027-05-09")
    ahead = events.upcoming(cid, _provider(cid), _fixed(cid, "2026-05-01"))
    assert [e["name"] for e in ahead] == ["Soon"]


def test_on_day_includes_a_fired_event(monkeypatch, tmp_path):
    """"What is today" is a different question from "what fired": an event that
    fired this morning is still today's."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    events.fire(cid, [eid], "2026-05-09")
    assert [e["name"] for e in events.on_day(cid, _provider(cid), _fixed(cid, "2026-05-09"))] \
        == ["Coronation"]


def test_sooner_takes_the_nearer_notice(monkeypatch, tmp_path):
    holiday = {"name": "Founding Day", "in_days": 5}
    event = {"name": "Coronation", "in_days": 2}
    assert events.sooner(holiday, event) == event
    assert events.sooner(holiday, None) == holiday
    assert events.sooner(None, event) == event
    # Ties go to the holiday: it is the one nobody had to write down.
    assert events.sooner(holiday, {"name": "Tie", "in_days": 5}) == holiday


def test_day_facts_merges_today_and_the_next_thing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Coronation", "2026-05-09")
    events.create(cid, "The Regatta", "2026-05-12")
    facts = events.day_facts(cid, campaigns.campaign_root(cid), "2026-05-09")
    assert facts == {"events_today": ["Coronation"],
                     "upcoming": {"name": "The Regatta", "in_days": 3}}


def test_day_facts_of_an_unreadable_moment_is_blank(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Coronation", "2026-05-09")
    assert events.day_facts(cid, campaigns.campaign_root(cid), "not a date") == {
        "events_today": [], "upcoming": None}


# ---- firing ----------------------------------------------------------------

def test_fire_stamps_both_reckonings(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    assert events.fire(cid, [eid], "2026-05-09") == [eid]
    stamp = events.get(cid, eid)["fired"]
    assert stamp["moment"] == "2026-05-09" and stamp["at"]


def test_fire_skips_the_already_fired_and_the_unknown(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    events.fire(cid, [eid], "2026-05-09")
    assert events.fire(cid, [eid, "nope"], "2026-05-10") == []


def test_fire_over_an_unreadable_file_writes_nothing(monkeypatch, tmp_path):
    """It runs after a move that already landed; it must not raise into it."""
    cid = _campaign(monkeypatch, tmp_path)
    events._path(cid).write_text("{not json", encoding="utf-8")
    assert events.fire(cid, ["x"], "2026-05-09") == []


# ---- what the clock does with all of it ------------------------------------

def test_an_advance_fires_what_it_crosses(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    eid = events.create(cid, "Coronation", "2026-05-09")
    result = clock.advance(cid, days=30, reason="a month passes")
    assert [e["id"] for e in result["digest"]["events"]] == [eid]
    # The rows that actually took the stamp, not a copy of the digest compared
    # against itself: `fire` skips what a concurrent advance got to first, and
    # this is the assertion that would notice if it skipped everything.
    assert [e["id"] for e in result["fired"]] == [eid]
    assert events.get(cid, eid)["fired"]["moment"] == "2026-05-31"


def test_a_preview_fires_nothing(monkeypatch, tmp_path):
    """What a preview lists is exactly what confirming it will fire — which is
    only true if the preview itself writes nothing."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    eid = events.create(cid, "Coronation", "2026-05-09")
    assert [e["id"] for e in clock.preview(cid, days=30)["events"]] == [eid]
    assert events.get(cid, eid)["fired"] is None


def test_a_backward_correction_reports_but_does_not_fire(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-31", reason="start")
    eid = events.create(cid, "Coronation", "2026-05-09")
    result = clock.advance(cid, to="2026-05-01", reason="that was wrong")
    assert [e["id"] for e in result["digest"]["events"]] == [eid]
    assert result["fired"] == []
    assert events.get(cid, eid)["fired"] is None


def test_a_long_skip_still_fires_a_distant_event(monkeypatch, tmp_path):
    """Holidays and birthdays are dropped past `SCAN_LIMIT_DAYS` — a per-day
    scan nobody reads. Events are a handful of authored rows, and one the skip
    walked past unfired would stay unfired forever."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    eid = events.create(cid, "The comet returns", "2031-05-09")
    result = clock.advance(cid, to="2036-05-01", reason="five years pass")
    assert result["digest"]["truncated"] is True
    assert result["fired"] and result["fired"][0]["id"] == eid


def test_a_scene_date_fires_what_it_carries_the_clock_past(monkeypatch, tmp_path):
    """Most campaigns move time only by dating scenes; an event only
    `POST /advance` could fire would sit through the session it was set for."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    eid = events.create(cid, "Coronation", "2026-05-09")
    sid = scenes.create_scene(cid, "A scene")
    scenes.set_datetime(cid, sid, "2026-05-12")
    observed = clock.observe(cid, "2026-05-12", f"scene {sid}")
    assert [e["id"] for e in observed["fired"]] == [eid]


def test_a_flashback_fires_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-31", reason="start")
    events.create(cid, "Coronation", "2026-05-09")
    observed = clock.observe(cid, "2026-05-02", "a flashback")
    assert observed["moved"] is False and observed["fired"] == []


def test_a_campaign_taking_its_first_date_fires_nothing(monkeypatch, tmp_path):
    """There is no span behind a first moment, only a present."""
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Coronation", "2026-05-09")
    assert clock.observe(cid, "2026-05-31", "the first scene")["fired"] == []
    assert events.get(cid, "coronation")["fired"] is None


# ---- the day the clock has already gone by ---------------------------------

def test_an_event_behind_the_clock_is_reported_as_passed(monkeypatch, tmp_path):
    """The state nothing used to mention: scheduled for a day the campaign is
    already past, and no move can ever cross it, because a span starting at
    "now" cannot contain a day behind it."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-06-01", reason="start")
    events.create(cid, "Mistyped", "2026-05-09")
    events.create(cid, "Still ahead", "2026-07-01")
    rows = {e["name"]: e for e in
            events.list_events(cid, _provider(cid), _fixed(cid, "2026-06-01"))}
    assert rows["Mistyped"]["passed"] is True
    assert rows["Still ahead"]["passed"] is False


def test_a_fired_event_is_never_reported_as_passed(monkeypatch, tmp_path):
    """`passed` is the unfired half of "behind the clock" — the half that still
    wants something from the reader."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "Coronation", "2026-05-09")
    events.fire(cid, [eid], "2026-05-09")
    row = events.list_events(cid, _provider(cid), _fixed(cid, "2026-06-01"))[0]
    assert row["passed"] is False and row["fired"] is not None


def test_an_event_on_the_clock_s_own_day_has_not_been_passed(monkeypatch, tmp_path):
    """Today is not behind: the day is still being lived through, and an advance
    out of it crosses nothing (`crossed` is half-open at the start)."""
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "Today", "2026-06-01")
    row = events.list_events(cid, _provider(cid), _fixed(cid, "2026-06-01"))[0]
    assert row["passed"] is False


def test_re_dating_a_passed_event_forward_lets_it_fire_again(monkeypatch, tmp_path):
    """The repair the label points at, end to end."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-06-01", reason="start")
    eid = events.create(cid, "Mistyped", "2026-05-09")
    events.update(cid, eid, date="2026-06-10")
    result = clock.advance(cid, to="2026-06-30", reason="a month passes")
    assert [e["id"] for e in result["fired"]] == [eid]


def test_a_long_name_and_note_are_truncated_rather_than_refused(monkeypatch, tmp_path):
    """A paste that is too long should still record the event it describes —
    `clock.REASON_LIMIT`'s rule, and the only thing bounding a file every turn
    reads and a public endpoint writes."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "N" * 500, "2026-05-09", "x" * 5000)
    row = events.get(cid, eid)
    assert len(row["name"]) == events.NAME_LIMIT
    assert len(row["note"]) == events.NOTE_LIMIT
    assert events.update(cid, eid, name="M" * 500, note="y" * 5000)
    row = events.get(cid, eid)
    assert len(row["name"]) == events.NAME_LIMIT and len(row["note"]) == events.NOTE_LIMIT


def test_a_blank_name_still_leaves_a_readable_row(monkeypatch, tmp_path):
    """The id is the fallback, so an unnamed event is still something a reader
    can find and fix rather than an empty line."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "   ", "2026-05-09")
    assert events.get(cid, eid)["name"] == eid
