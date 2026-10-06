"""Saved scene ideas carry driver provenance, and derive staleness from it
(continuity capstone spec §17, §17.1; Slice E Decisions 16-18).

The write side (`suggest.idea_provenance`) tests existence, never activity: a
card anchored before the coronation and saved after the clock fired it keeps
its anchor, because that anchor is the evidence the read needs to say the idea
went stale. The read side (`suggest.validate_ideas`) classifies each stored
ref as live, finished or dangling, and never lets one bad read hide an idea:
unknown is never stale.
"""

from __future__ import annotations

import json

from grimoire.store import (
    atomic,
    calendars,
    campaigns,
    clock,
    commitments,
    events,
    notices,
    overlay,
    plot,
    scene_ideas,
    suggest,
)
from grimoire.store.continuity import drivers as continuity_drivers
from grimoire.store.continuity import effective, pressure, review
from tests.test_suggest_store import (
    _NAIVE_PROVIDER_SRC,
    _break_calendar,
    _map,
    _oath,
    _pressure_campaign,
    _recorder,
)

MAP = "thread:mara-s-map"
LEDGER = "thread:find-the-ledger"
MIDNIGHT = "thread:the-midnight-deadline"
OATH = "commitment:mara-s-oath"
CORONATION = "event:the-coronation"

_GREGORIAN = calendars.get_provider({"provider": "gregorian"})


def _fx(native: str) -> int:
    return calendars.fixed_of(_GREGORIAN, native)


def _d(ref: str, action: str) -> dict:
    return {"ref": ref, "action": action}


def _campaign(monkeypatch, tmp_path) -> str:
    """Task 2's campaign shape, the clock at 2026-05-10: Mara's map and Find
    the ledger open, Mara's oath due 05-14, the coronation on 05-13."""
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open",
                      "The ledger left Saltmarch.", "001--gate")
    assert events.create(cid, "The coronation", "2026-05-13") == "the-coronation"
    return cid


def _midnight(cid: str, status: str = "open") -> None:
    plot.set_movement(cid, "the-midnight-deadline", "The midnight deadline", status,
                      "The bells of Saltmarch.", "001--gate")


def _save(cid: str, title: str, drivers=(), anchor=None, **kw) -> str:
    """A save the way the route makes one: provenance validated, then stored."""
    prov = suggest.idea_provenance(cid, list(drivers), anchor)
    return scene_ideas.add(cid, title, "P", drivers=prov["drivers"],
                           time_anchor=prov["time_anchor"], **kw)


def _read(cid: str) -> dict[str, dict]:
    return {i["id"]: i for i in suggest.validate_ideas(cid, scene_ideas.records(cid))}


def _ideas_path(cid: str):
    return campaigns.campaign_root(cid) / "scene_ideas.json"


def _get(client, cid: str) -> dict[str, dict]:
    r = client.get(f"/api/campaigns/{cid}/scene-ideas?greetings=false")
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()}


# ---- the write side: existence, not activity --------------------------------
def test_write_drops_unknown_and_mismatched_refs(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _midnight(cid, "closed")
    review.create_alias(cid, MAP, LEDGER)
    later = f"holiday:{_fx('2026-05-20')}:Saltmarch Eve"
    prov = suggest.idea_provenance(cid, [
        _d(MAP, "advance"),                          # an alias source: stored canonical
        _d(LEDGER, "close_candidate"),               # the same ref again: first wins
        _d("thread:ghost", "advance"),               # no such thread
        _d(OATH, "advance"),                         # wrong action for a commitment
        _d(MIDNIGHT, "advance"),                     # closed, but it exists
        _d(OATH, "address"),
        _d(OATH, "fulfill_candidate"),               # the same ref again
        _d("holiday:x:Saltmarch Eve", "anchor"),     # a malformed occurrence ref
        _d(later, "anchor"),                         # an anchor that is not the anchor
        _d(CORONATION, "anchor"),
        "not an entry",
    ], {"ref": CORONATION, "relation": "sideways"})
    assert prov["drivers"] == [_d(LEDGER, "advance"), _d(MIDNIGHT, "advance"),
                               _d(OATH, "address"), _d(CORONATION, "anchor")]
    assert prov["time_anchor"] == {"ref": CORONATION, "relation": "on",
                                   "native": "2026-05-13"}

    month = "birthday:pcs:winifred:month:2026-06"
    assert suggest.idea_provenance(cid, [], {"ref": month, "relation": "before"}) == {
        "drivers": [], "time_anchor": {"ref": month, "relation": "on", "native": ""}}


def test_write_never_raises_for_a_malformed_shape(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    empty = {"drivers": [], "time_anchor": None}
    for drivers, anchor in (("x", "x"), (None, []), ([{"ref": 3}], {"ref": 3}),
                            ([], {"ref": "thread:mara-s-map"}), ([], {"relation": "on"}),
                            ([], {"ref": "holiday:garbage"})):
        assert suggest.idea_provenance(cid, drivers, anchor) == empty, (drivers, anchor)


def test_write_keeps_an_unreadable_ledgers_refs(monkeypatch, tmp_path):
    """An unreadable plot.json makes existence unknown, and unknown keeps the
    ref: the read side reclassifies it once the ledger reads again."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    prov = suggest.idea_provenance(cid, [_d(MAP, "advance")], None)
    assert prov == {"drivers": [_d(MAP, "advance")], "time_anchor": None}


def test_write_copies_the_anchor_native(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    event = suggest.idea_provenance(cid, [], {"ref": CORONATION, "relation": "before"})
    assert event["time_anchor"] == {"ref": CORONATION, "relation": "before",
                                    "native": "2026-05-13"}

    day = _fx("2026-05-20")
    for ref in (f"holiday:{day}:Saltmarch Eve", f"birthday:characters:mara:{day}"):
        anchor = suggest.idea_provenance(cid, [], {"ref": ref, "relation": "on"})["time_anchor"]
        assert anchor == {"ref": ref, "relation": "on", "native": "2026-05-20"}
    month = "birthday:pcs:winifred:month:2026-06"
    assert suggest.idea_provenance(
        cid, [], {"ref": month, "relation": "on"})["time_anchor"]["native"] == ""

    events.delete(cid, "the-coronation")
    gone = suggest.idea_provenance(cid, [_d(CORONATION, "anchor"), _d(MAP, "advance")],
                                   {"ref": CORONATION, "relation": "on"})
    assert gone == {"drivers": [_d(MAP, "advance")], "time_anchor": None}


def test_a_day_occurrence_anchor_needs_a_calendar(monkeypatch, tmp_path):
    """No provider, no native: a day occurrence anchor is dropped rather than
    stored with a date nobody computed. An event keeps its stored text."""
    cid = _campaign(monkeypatch, tmp_path)
    _break_calendar(cid, tmp_path)
    holiday = f"holiday:{_fx('2026-05-20')}:Saltmarch Eve"
    assert suggest.idea_provenance(cid, [_d(holiday, "anchor")],
                                   {"ref": holiday, "relation": "on"}) == {
        "drivers": [], "time_anchor": None}
    assert suggest.idea_provenance(cid, [], {"ref": CORONATION, "relation": "on"})[
        "time_anchor"] == {"ref": CORONATION, "relation": "on", "native": "2026-05-13"}


def test_a_passed_anchor_is_stored_and_reads_as_passed(monkeypatch, tmp_path):
    """Decision 16, Review Focus 5: the card was still on screen when the clock
    moved. Existence is the write test, so the anchor is kept -- and it is the
    evidence the read needs to say why the idea went stale."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-14")
    assert events.get(cid, "the-coronation")["fired"] is not None
    lid = _save(cid, "Before the crown", [_d(CORONATION, "anchor")],
                {"ref": CORONATION, "relation": "before"})
    assert scene_ideas.read(cid)[lid]["time_anchor"] == {
        "ref": CORONATION, "relation": "before", "native": "2026-05-13"}
    assert _read(cid)[lid]["stale_reason"] == "The coronation has passed"


def test_saves_do_no_driver_or_pressure_work(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snapshots = _recorder(monkeypatch, continuity_drivers, "snapshot")
    builds = _recorder(monkeypatch, pressure, "build")
    holiday = f"holiday:{_fx('2026-05-20')}:Saltmarch Eve"
    first = _save(cid, "The map", [_d(MAP, "advance")])
    second = _save(cid, "Eve", [_d(holiday, "anchor")], {"ref": holiday, "relation": "on"})
    stored = scene_ideas.read(cid)
    assert stored[first]["drivers"] == [_d(MAP, "advance")]
    assert stored[second]["time_anchor"]["ref"] == holiday
    assert snapshots == []
    assert builds == []


# ---- the read side: classification -------------------------------------------
def test_read_classifies_live_finished_dangling(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _midnight(cid)
    lid = _save(cid, "Everything", [_d(MAP, "advance"), _d(LEDGER, "advance"),
                                    _d(OATH, "address"), _d(MIDNIGHT, "advance"),
                                    _d(CORONATION, "anchor")],
                {"ref": CORONATION, "relation": "after"})
    plot.set_movement(cid, "mara-s-map", "", "closed", "Found.", "002--road")
    commitments.set_movement(cid, "mara-s-oath", "", "", "fulfilled", None, "Kept.",
                             "002--road")
    plot.restore(cid, "the-midnight-deadline", None)
    clock.advance(cid, to="2026-05-14")

    row = _read(cid)[lid]
    assert row["drivers"] == [
        {"ref": MAP, "action": "advance", "kind": "thread", "label": "Mara's map",
         "state": "finished"},
        {"ref": LEDGER, "action": "advance", "kind": "thread", "label": "Find the ledger",
         "state": "live"},
        {"ref": OATH, "action": "address", "kind": "commitment", "label": "Mara's oath",
         "state": "finished"},
        {"ref": CORONATION, "action": "anchor", "kind": "event", "label": "The coronation",
         "state": "finished"},
    ]
    assert row["time_anchor"]["state"] == "finished"
    assert row["stale_reason"] == ""          # Find the ledger is still open


def test_read_canonicalizes_and_dedupes_a_merged_ref(client, monkeypatch, tmp_path):
    """Decision 17, "Canonical on read". `_union` imports nothing from
    continuity, so the file keeps both spellings; the read is what shows one."""
    cid = _campaign(monkeypatch, tmp_path)
    url = f"/api/campaigns/{cid}/scene-ideas"
    body = {"title": "The ledger room", "premise": "Dust and ink.", "source": "llm"}
    lid = client.post(url, json={**body, "drivers": [_d(MAP, "advance")]}).json()["id"]
    review.create_alias(cid, MAP, LEDGER)
    again = client.post(url, json={**body, "drivers": [_d(LEDGER, "close_candidate")]})
    assert again.json()["id"] == lid
    assert scene_ideas.read(cid)[lid]["drivers"] == [_d(MAP, "advance"),
                                                     _d(LEDGER, "close_candidate")]

    before = _ideas_path(cid).read_bytes()
    row = _get(client, cid)[lid]
    assert row["drivers"] == [{"ref": LEDGER, "action": "advance", "kind": "thread",
                               "label": "Find the ledger", "state": "live"}]
    assert row["stale_reason"] == ""
    _get(client, cid)
    assert _ideas_path(cid).read_bytes() == before


# ---- the read side: stale_reason ---------------------------------------------
def test_stale_reason_after_resolution(monkeypatch, tmp_path):
    """§28.6: a saved idea's stale_reason derives after resolution."""
    cid = _campaign(monkeypatch, tmp_path)
    _midnight(cid)
    owed = _save(cid, "Owed", [_d(OATH, "address")])
    mapped = _save(cid, "Mapped", [_d(MAP, "advance")])
    gone = _save(cid, "Gone", [_d(MIDNIGHT, "advance")])
    mixed = _save(cid, "Mixed", [_d(MAP, "advance"), _d(OATH, "address")])
    assert {r["stale_reason"] for r in _read(cid).values()} == {""}

    commitments.set_movement(cid, "mara-s-oath", "", "", "fulfilled", None, "Kept.",
                             "002--road")
    plot.set_movement(cid, "mara-s-map", "", "closed", "Found.", "002--road")
    plot.restore(cid, "the-midnight-deadline", None)
    rows = _read(cid)
    assert rows[owed]["stale_reason"] == "Every commitment it was about is resolved"
    assert rows[mapped]["stale_reason"] == "Every thread it was about is closed"
    assert rows[gone]["stale_reason"] == "Its drivers no longer exist"
    assert rows[gone]["drivers"] == []
    assert rows[mixed]["stale_reason"] == "Nothing it was about is still open"


def test_stale_reason_from_a_past_anchor(monkeypatch, tmp_path):
    """§28.6. A finished `after` anchor is what the idea was waiting for."""
    cid = _campaign(monkeypatch, tmp_path)
    before = _save(cid, "Before", [_d(CORONATION, "anchor")],
                   {"ref": CORONATION, "relation": "before"})
    after = _save(cid, "After", [_d(CORONATION, "anchor")],
                  {"ref": CORONATION, "relation": "after"})
    clock.advance(cid, to="2026-05-14")
    rows = _read(cid)
    assert rows[before]["stale_reason"] == "The coronation has passed"
    assert rows[before]["time_anchor"]["state"] == "finished"
    assert rows[after]["stale_reason"] == ""


def test_an_on_anchor_on_its_own_day_is_live(monkeypatch, tmp_path):
    """§13.5's own-day carve-out, read the way occurrences are: reaching the
    coronation's day fires it, and an idea anchored on (or by, or before) it
    is still for today -- as a holiday on the same day is. The day after, it
    has passed."""
    cid = _campaign(monkeypatch, tmp_path)
    eve = f"holiday:{_fx('2026-05-13')}:Saltmarch Eve"
    lids = {rel: _save(cid, rel, [_d(CORONATION, "anchor")],
                       {"ref": CORONATION, "relation": rel})
            for rel in ("on", "by", "before")}
    holiday = _save(cid, "Eve", [_d(eve, "anchor")], {"ref": eve, "relation": "on"})
    clock.advance(cid, to="2026-05-13")
    assert events.get(cid, "the-coronation")["fired"]
    rows = _read(cid)
    for rel, lid in lids.items():
        assert rows[lid]["stale_reason"] == "", rel
        assert rows[lid]["time_anchor"]["state"] == "live", rel
    assert rows[lids["on"]]["anchor_date"] == "2026-05-13"
    assert rows[holiday]["anchor_date"] == "2026-05-13"
    assert rows[holiday]["stale_reason"] == ""

    clock.advance(cid, to="2026-05-14")
    rows = _read(cid)
    for rel, lid in lids.items():
        assert rows[lid]["stale_reason"] == "The coronation has passed", rel
        assert rows[lid]["anchor_date"] == "", rel
    assert rows[holiday]["stale_reason"] == "Saltmarch Eve has passed"


def test_a_fired_event_whose_day_cannot_be_placed_is_live(monkeypatch, tmp_path):
    """Decision 17 meets the own-day carve-out: fired alone does not finish an
    event, so with no calendar or no `now` to say whether its day is today,
    an idea anchored on it is unknown -- and unknown is never stale."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = _save(cid, "On", [_d(CORONATION, "anchor")],
                {"ref": CORONATION, "relation": "on"})
    clock.advance(cid, to="2026-05-13")
    assert events.get(cid, "the-coronation")["fired"]
    assert _read(cid)[lid]["stale_reason"] == ""

    croot = campaigns.campaign_root(cid)
    calendar = (croot / "calendar.json").read_text(encoding="utf-8")
    _break_calendar(cid, tmp_path)
    row = _read(cid)[lid]
    assert row["stale_reason"] == ""
    assert row["time_anchor"]["state"] == "live"
    (croot / "calendar.json").write_text(calendar, encoding="utf-8")

    def _no_present(_cid):
        raise RuntimeError("clock unreadable")

    monkeypatch.setattr(clock, "now", _no_present)
    row = _read(cid)[lid]
    assert row["stale_reason"] == ""
    assert row["time_anchor"]["state"] == "live"


def test_a_fired_event_off_its_day_is_finished(monkeypatch, tmp_path):
    """Fired still finishes an event whose day is not today -- one fired by
    hand ahead of its date, or whose date the calendar cannot read."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = _save(cid, "Before", [_d(CORONATION, "anchor")],
                {"ref": CORONATION, "relation": "before"})
    events.fire(cid, ["the-coronation"], "2026-05-10")
    assert _read(cid)[lid]["stale_reason"] == "The coronation has passed"


def test_a_rescheduled_anchor_reads_as_moved(monkeypatch, tmp_path):
    """§28.6, Review Focus 5."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = _save(cid, "Crown", [_d(MAP, "advance"), _d(CORONATION, "anchor")],
                {"ref": CORONATION, "relation": "before"})
    events.update(cid, "the-coronation", date="2026-05-20")
    row = _read(cid)[lid]
    assert row["stale_reason"] == "The coronation moved to 20 May 2026"
    assert row["time_anchor"] is None
    assert [d["ref"] for d in row["drivers"]] == [MAP]


def test_a_reused_event_slug_reads_as_moved(monkeypatch, tmp_path):
    """Review Focus 5: deleted and re-created under the same slug on another
    day is indistinguishable from a reschedule, and §17 accepts reading it so."""
    cid = _campaign(monkeypatch, tmp_path)
    lid = _save(cid, "Crown", [], {"ref": CORONATION, "relation": "on"})
    events.delete(cid, "the-coronation")
    assert events.create(cid, "The coronation", "2026-05-25") == "the-coronation"
    assert _read(cid)[lid]["stale_reason"] == "The coronation moved to 25 May 2026"


def test_a_deleted_anchor_is_dropped_not_fatal(monkeypatch, tmp_path):
    """Review Focus 5: the anchor leaves the returned idea, and the idea is
    stale only when nothing else it stored is live."""
    cid = _campaign(monkeypatch, tmp_path)
    anchor = {"ref": CORONATION, "relation": "before"}
    kept = _save(cid, "With the map", [_d(MAP, "advance")], anchor)
    alone = _save(cid, "Alone", [], anchor)
    events.delete(cid, "the-coronation")
    rows = _read(cid)
    assert rows[kept]["time_anchor"] is None
    assert rows[kept]["stale_reason"] == ""
    assert rows[alone]["time_anchor"] is None
    assert rows[alone]["stale_reason"] == "Its drivers no longer exist"


def test_an_upcoming_on_anchor_supplies_its_date(monkeypatch, tmp_path):
    """§17.1's "server-supplied anchor date": only an `on` anchor that is live
    and dated has one."""
    cid = _campaign(monkeypatch, tmp_path)
    on = _save(cid, "On", [], {"ref": CORONATION, "relation": "on"})
    before = _save(cid, "Before", [], {"ref": CORONATION, "relation": "before"})
    rows = _read(cid)
    assert rows[on]["anchor_date"] == "2026-05-13"
    assert rows[on]["time_anchor"] == {"ref": CORONATION, "relation": "on",
                                       "native": "2026-05-13", "kind": "event",
                                       "label": "The coronation", "friendly": "13 May 2026",
                                       "state": "live"}
    assert rows[before]["anchor_date"] == ""


def test_a_past_holiday_occurrence_is_finished(monkeypatch, tmp_path):
    """A past occurrence is past, even though the holiday recurs (§17.1); the
    name comes from the ref, since nothing stores a label."""
    cid = _campaign(monkeypatch, tmp_path)
    ref = f"holiday:{_fx('2026-05-01')}:Saltmarch Eve"
    lid = _save(cid, "Eve", [_d(ref, "anchor")], {"ref": ref, "relation": "on"})
    row = _read(cid)[lid]
    assert row["time_anchor"] == {"ref": ref, "relation": "on", "native": "2026-05-01",
                                  "kind": "holiday", "label": "Saltmarch Eve",
                                  "friendly": "1 May 2026", "state": "finished"}
    assert row["drivers"][0]["state"] == "finished"
    assert row["stale_reason"] == "Saltmarch Eve has passed"
    assert row["anchor_date"] == ""


def test_a_past_month_occurrence_is_finished(monkeypatch, tmp_path):
    """By month order (`_month_of`, then the month's index in its year), not
    by key equality: December of last year is behind May, June is not."""
    cid = _campaign(monkeypatch, tmp_path)
    states = {}
    for month in ("2026-04", "2025-12", "2026-05", "2026-06"):
        ref = f"birthday:pcs:winifred:month:{month}"
        states[_save(cid, month, [], {"ref": ref, "relation": "on"})] = month
    rows = _read(cid)
    got = {month: rows[lid]["time_anchor"]["state"] for lid, month in states.items()}
    assert got == {"2026-04": "finished", "2025-12": "finished", "2026-05": "live",
                   "2026-06": "live"}
    april = next(rows[lid] for lid, month in states.items() if month == "2026-04")
    assert april["time_anchor"]["friendly"] == "April 2026"
    assert april["stale_reason"] == "winifred's birthday has passed"
    assert all(rows[lid]["anchor_date"] == "" for lid in states)


def test_occurrence_labels_come_from_the_ref_and_the_actor(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara, _version = overlay.create_character(cid, "Mara", "main")
    day = _fx("2026-05-20")
    long_name = "Saltmarch Eve " + "of the long tide " * 10
    bounded = notices.holiday_key(day, long_name)
    assert "~" in bounded
    refs = {"named": f"birthday:characters:{mara}:{day}",
            "gone": f"birthday:characters:seraphine:{day}",
            "bounded": bounded}
    lids = {key: _save(cid, key, [], {"ref": ref, "relation": "on"})
            for key, ref in refs.items()}
    rows = _read(cid)
    labels = {key: rows[lid]["time_anchor"]["label"] for key, lid in lids.items()}
    assert labels == {"named": "Mara", "gone": "seraphine",
                      "bounded": f"{long_name.strip()[:notices.NAME_BUDGET]}…"}
    assert rows[lids["named"]]["anchor_date"] == "2026-05-20"


def test_a_past_birthday_reads_as_a_birthday(monkeypatch, tmp_path):
    """The chip label is the actor's bare name; the stale sentence names the
    birthday, so it never reads as a death notice."""
    cid = _campaign(monkeypatch, tmp_path)
    mara, _version = overlay.create_character(cid, "Mara", "main")
    ref = f"birthday:characters:{mara}:{_fx('2026-05-01')}"
    lid = _save(cid, "Gift", [_d(ref, "anchor")], {"ref": ref, "relation": "before"})
    row = _read(cid)[lid]
    assert row["time_anchor"]["label"] == "Mara"
    assert row["drivers"][0]["label"] == "Mara"
    assert row["stale_reason"] == "Mara's birthday has passed"


# ---- cost and failure ----------------------------------------------------------
def test_ideas_without_provenance_read_nothing_extra(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_ideas.add(cid, "The map", "x")
    scene_ideas.add(cid, "The oath", "y")
    recorders = [_recorder(monkeypatch, effective.Ledgers, "load"),
                 _recorder(monkeypatch, effective, "live_canon"),
                 _recorder(monkeypatch, continuity_drivers, "snapshot"),
                 _recorder(monkeypatch, pressure, "build"),
                 _recorder(monkeypatch, events, "read")]
    providers = _recorder(monkeypatch, calendars, "primary_provider")
    rows = suggest.validate_ideas(cid, scene_ideas.records(cid))
    assert [(r["stale_reason"], r["anchor_date"], r["drivers"], r["time_anchor"])
            for r in rows] == [("", "", [], None)] * 2
    assert all(calls == [] for calls in recorders)
    assert len(providers) == 1               # today's ref_validator call, and no other


def test_a_damaged_read_never_hides_ideas(client, monkeypatch, tmp_path):
    """Decision 17: one bad read can never empty the saved list, and unknown
    is never stale."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    mapped = _save(cid, "Mapped", [_d(MAP, "advance")], date="2026-05-12")
    crowned = _save(cid, "Crowned", [_d(CORONATION, "anchor")],
                    {"ref": CORONATION, "relation": "before"})

    # a calendar plugin that raises: every idea lists, undated, none stale
    calendar_path = croot / "calendar.json"
    calendar = calendar_path.read_text(encoding="utf-8")
    _break_calendar(cid, tmp_path)
    rows = _get(client, cid)
    assert set(rows) == {mapped, crowned}
    assert {r["date"] for r in rows.values()} == {""}
    assert {r["stale_reason"] for r in rows.values()} == {""}
    calendar_path.write_text(calendar, encoding="utf-8")
    assert _get(client, cid)[mapped]["date"] == "2026-05-12"

    plot.set_movement(cid, "mara-s-map", "", "closed", "Found.", "002--road")
    assert _get(client, cid)[mapped]["stale_reason"] == "Every thread it was about is closed"

    # a garbled ledger: the closed thread can no longer be read, so it is live
    plot_path = croot / "plot.json"
    healthy = plot_path.read_text(encoding="utf-8")
    plot_path.write_text("{ no", encoding="utf-8")
    rows = _get(client, cid)
    assert set(rows) == {mapped, crowned}
    assert {r["stale_reason"] for r in rows.values()} == {""}
    plot_path.write_text(healthy, encoding="utf-8")

    # one stored anchor nothing can parse: that idea falls back, the rest annotate
    data = json.loads(_ideas_path(cid).read_text(encoding="utf-8"))
    data[crowned]["time_anchor"] = {"ref": "holiday:garbage", "relation": "on", "native": ""}
    _ideas_path(cid).write_text(json.dumps(data), encoding="utf-8")
    rows = _get(client, cid)
    assert rows[mapped]["stale_reason"] == "Every thread it was about is closed"
    assert rows[mapped]["drivers"][0]["label"] == "Mara's map"
    fallback = rows[crowned]
    assert fallback["stale_reason"] == "" and fallback["anchor_date"] == ""
    assert fallback["time_anchor"] == {"ref": "holiday:garbage", "relation": "on",
                                       "native": "", "kind": "holiday",
                                       "label": "holiday:garbage", "friendly": "",
                                       "state": "live"}
    assert fallback["drivers"] == [{"ref": CORONATION, "action": "anchor", "kind": "event",
                                    "label": CORONATION, "state": "live"}]


def test_a_plugin_parse_raising_never_hides_ideas(client, monkeypatch, tmp_path):
    """Decision 17 and §24/§26 under a plugin that constructs fine but whose
    `parse` raises something other than CalendarError: a stored date it cannot
    read blanks rather than emptying the saved list, and a dated save still
    lands, undated."""
    cid = _campaign(monkeypatch, tmp_path)
    mapped = _save(cid, "Mapped", [_d(MAP, "advance")], date="2026-05-12")
    plugins = tmp_path / "calendars"
    plugins.mkdir(exist_ok=True)
    (plugins / "naive_test.py").write_text(_NAIVE_PROVIDER_SRC, encoding="utf-8")
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "naive-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)

    rows = _get(client, cid)
    assert set(rows) == {mapped}
    assert rows[mapped]["date"] == ""

    r = client.post(f"/api/campaigns/{cid}/scene-ideas", json={
        "title": "Seraphine's errand", "premise": "P", "date": "2026-05-12"})
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    assert scene_ideas.read(cid)[lid]["date"] == ""
    assert set(_get(client, cid)) == {mapped, lid}


def test_reads_never_write(client, monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _save(cid, "Crown", [_d(MAP, "advance"), _d(CORONATION, "anchor")],
          {"ref": CORONATION, "relation": "on"})
    _save(cid, "Plain", [])
    writes = _recorder(monkeypatch, atomic, "write_text")
    rows = _get(client, cid)
    assert len(rows) == 2
    assert writes == []


# ---- the route ------------------------------------------------------------------
def test_post_scene_idea_round_trips_provenance(client, monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    r = client.post(f"/api/campaigns/{cid}/scene-ideas", json={
        "title": "The crown", "premise": "Mara at the coronation.", "source": "llm",
        "drivers": [_d(MAP, "advance"), _d("thread:ghost", "advance"),
                    _d(CORONATION, "anchor")],
        "time_anchor": {"ref": CORONATION, "relation": "on"}})
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    assert scene_ideas.read(cid)[lid]["drivers"] == [_d(MAP, "advance"),
                                                     _d(CORONATION, "anchor")]
    row = _get(client, cid)[lid]
    assert row["drivers"] == [
        {"ref": MAP, "action": "advance", "kind": "thread", "label": "Mara's map",
         "state": "live"},
        {"ref": CORONATION, "action": "anchor", "kind": "event", "label": "The coronation",
         "state": "live"}]
    assert row["time_anchor"] == {"ref": CORONATION, "relation": "on", "native": "2026-05-13",
                                  "kind": "event", "label": "The coronation",
                                  "friendly": "13 May 2026", "state": "live"}
    assert (row["stale_reason"], row["anchor_date"]) == ("", "2026-05-13")
