"""`continuity.drivers`: the scene-driver projection (capstone spec §14).

Live canonical threads and commitments, plus every event, holiday and birthday
pressure item, each with one pressure reading, its actors from the shared
involvement projection and its reviewed links -- and the bounded anchor list a
suggestion may date itself against.

The fixture calendar observes only the holidays a test names (`region=""`):
the default block is `region="US"`, and 2026-05-10 is a fortnight before a US
federal holiday, which would otherwise join every exact list below.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import (
    appearances,
    calendars,
    chronicle,
    events,
    notices,
    overlay,
    pcs,
    plot,
)
from grimoire.store.continuity import drivers, pressure
from tests.test_continuity_pressure import (
    CORONATION,
    OATH,
    F,
    _actor,
    _alias,
    _campaign,
    _commitment,
    _link,
    _root,
    _rule,
)

S1, S2, S3 = "001--saltmarch", "002--realm", "003--winifred"
MAP = "thread:mara-s-map"
CHART = "thread:winifred-s-chart"


def _thread(cid, tid="mara-s-map", title="Mara's map", status="open",
            beat="The map turned up in Saltmarch.", scene=S1):
    plot.set_movement(cid, tid, title, status, beat, scene)
    return f"thread:{tid}"


def _drivers(cid, kind=None, **kw):
    out = drivers.snapshot(cid, **kw)["drivers"]
    return [d for d in out if kind is None or d["kind"] == kind]


def _by_ref(cid, **kw):
    return {d["ref"]: d for d in _drivers(cid, **kw)}


def _undated_event(cid, eid="midsummer-rite", name="The Saltmarch rite"):
    path = _root(cid) / "events.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[eid] = {"name": name, "date": "midsummer", "note": "", "fired": None}
    path.write_text(json.dumps(data), encoding="utf-8")
    return f"event:{eid}"


def _stale_after(cid, days):
    cfg = calendars.read_calendar(_root(cid))
    cfg["stale_after_days"] = days
    calendars.write_calendar(_root(cid), cfg)


def _write_chronicle(cid, casts: dict):
    (_root(cid) / "chronicle.json").write_text(json.dumps(
        {sid: {"id": sid, "one_line": "", "cast": cast} for sid, cast in casts.items()}),
        encoding="utf-8")


OK = {"state": "ok", "in_days": None, "friendly": ""}


# ---- the contract -----------------------------------------------------------


def test_driver_kinds_are_pinned():
    assert drivers.DRIVER_KINDS == ("thread", "commitment", "event", "birthday", "holiday")


def test_snapshot_shape_and_matching(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    out = drivers.snapshot(cid)
    assert set(out) == {"now", "friendly", "fixed", "matching", "drivers", "anchors"}
    assert out["matching"] == "basic"
    built = pressure.build(cid)
    assert (out["now"], out["friendly"], out["fixed"]) == (
        built["now"], built["friendly"], built["fixed"])
    assert out["fixed"] == F(cid, "2026-05-10")


# ---- record drivers ---------------------------------------------------------


def test_thread_and_commitment_drivers(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _thread(cid)
    _thread(cid, title="", status="advanced", beat="Mara traced the coast.", scene=S2)
    closed = _thread(cid, "winifred-s-chart", "Winifred's chart", status="closed")
    _commitment(cid)
    vow = _commitment(cid, mid="seraphine-s-vow", title="Seraphine's vow", status="fulfilled")
    found = _by_ref(cid)
    assert found[MAP] == {
        "ref": MAP, "kind": "thread", "label": "Mara's map", "status": "advanced",
        "summary": "Mara traced the coast.", "actors": [], "pressure": OK,
        "time_anchors": [], "links": []}
    assert found[OATH] == {
        "ref": OATH, "kind": "commitment", "label": "Mara's oath", "status": "open",
        "summary": "Mara swore it at the gate.", "actors": [], "pressure": OK,
        "time_anchors": [], "links": []}
    assert closed not in found and vow not in found


def test_aliases_collapse_to_one_driver(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _write_chronicle(cid, {S1: ["characters/mara"], S2: ["characters/winifred"]})
    _thread(cid, scene=S1)
    _thread(cid, "winifred-s-chart", "Winifred's chart", beat="The chart was lost.", scene=S2)
    _alias(cid, MAP, CHART)
    threads = _drivers(cid, "thread")
    assert [d["ref"] for d in threads] == [CHART]
    assert threads[0]["label"] == "Winifred's chart"
    assert threads[0]["actors"] == ["characters:mara", "characters:winifred"]


def test_commitment_pressure_from_its_deadline_item(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7)
    _commitment(cid, due="2026-05-14")
    assert _by_ref(cid)[OATH]["pressure"] == {
        "state": "due_soon", "in_days": 4, "friendly": "14 May 2026"}


def test_stale_comes_from_aging_on_the_merged_last_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _stale_after(cid, 10)
    chronicle.absorb(cid, {"id": S1, "one_line": "", "date": "2026-03-31"})
    chronicle.absorb(cid, {"id": S2, "one_line": "", "date": "2026-05-08"})
    _thread(cid, scene=S1)
    _thread(cid, "winifred-s-chart", "Winifred's chart", beat="The chart was found.", scene=S2)
    before = _by_ref(cid)
    assert before[MAP]["pressure"] == {"state": "stale", "in_days": None, "friendly": ""}
    assert before[CHART]["pressure"] == OK

    _alias(cid, CHART, MAP)
    after = _by_ref(cid)
    assert CHART not in after
    assert after[MAP]["pressure"] == OK


# ---- temporal drivers and anchors -------------------------------------------


def test_temporal_drivers_and_anchors(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    audience = "event:" + events.create(cid, "Mara's audience", "2026-05-13")
    passed = "event:" + events.create(cid, "The coronation", "2026-05-05")
    undated = _undated_event(cid)
    mara = _actor(cid, "Mara", "--05-11")
    winifred = _actor(cid, "Winifred", "--06")
    holiday = notices.holiday_key(F(cid, "2026-05-12"), "Saltmarch Eve")
    out = drivers.snapshot(cid)
    temporal = {d["ref"]: d for d in out["drivers"]}
    [mara_bday] = [r for r, d in temporal.items() if d["actors"] == [f"characters:{mara}"]]
    [win_bday] = [r for r, d in temporal.items() if d["actors"] == [f"characters:{winifred}"]]
    assert {audience, passed, undated, holiday, mara_bday, win_bday} == set(temporal)
    assert temporal[passed]["pressure"]["state"] == "passed"
    assert temporal[undated]["pressure"] == OK
    for d in temporal.values():
        assert (d["summary"], d["status"], d["time_anchors"], d["links"]) == ("", "", [], [])
    assert temporal[audience]["actors"] == [] and temporal[holiday]["actors"] == []
    assert temporal[mara_bday]["kind"] == "birthday"
    assert temporal[mara_bday]["pressure"] == {
        "state": "upcoming", "in_days": 1, "friendly": "11 May 2026"}

    anchors = out["anchors"]
    assert [a["ref"] for a in anchors] == [mara_bday, holiday, audience, win_bday]
    for a in anchors:
        assert set(a) == {"ref", "kind", "label", "native", "friendly", "fixed",
                          "in_days", "precision"}
    assert [a["kind"] for a in anchors] == ["birthday", "holiday", "event", "birthday"]
    assert [a["in_days"] for a in anchors] == [1, 2, 3, None]
    assert anchors[0]["precision"] == "yearless"
    assert (anchors[2]["native"], anchors[2]["fixed"], anchors[2]["precision"]) == (
        "2026-05-13", F(cid, "2026-05-13"), "exact")
    month = anchors[-1]
    assert (month["precision"], month["fixed"], month["in_days"]) == ("month", None, None)
    assert month["label"] == "Winifred"


def test_drivers_order_by_pressure_then_kind(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7, holidays=[_rule("Saltmarch Eve", "05", 12)])
    _stale_after(cid, 10)
    chronicle.absorb(cid, {"id": S1, "one_line": "", "date": "2026-03-31"})
    _commitment(cid, due="2026-05-08")
    _commitment(cid, mid="seraphine-s-vow", title="Seraphine's vow")
    _thread(cid, "winifred-s-chart", "Winifred's chart", scene=S1)
    _thread(cid, scene=S2)
    _thread(cid, "realm-feud", "Realm feud", scene=S3)
    later = "event:" + events.create(cid, "Mara's audience", "2026-05-25")
    sooner = "event:" + events.create(cid, "The coronation", "2026-05-20")
    holiday = notices.holiday_key(F(cid, "2026-05-12"), "Saltmarch Eve")
    found = _drivers(cid)
    assert [d["ref"] for d in found] == [
        OATH,                                  # overdue
        sooner, later,                         # upcoming events, by in_days
        holiday,                               # upcoming, but a later kind than events
        CHART,                                 # stale
        MAP, "thread:realm-feud",              # ok threads, by ref
        "commitment:seraphine-s-vow",          # ok, a later kind than threads
    ]
    states = [d["pressure"]["state"] for d in found]
    assert states == ["overdue", "upcoming", "upcoming", "upcoming", "stale", "ok", "ok", "ok"]
    assert [pressure.PRESSURE_STATES.index(s) for s in states] == sorted(
        pressure.PRESSURE_STATES.index(s) for s in states)


# ---- links ------------------------------------------------------------------


def test_links_attach_with_direction(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _thread(cid)
    _thread(cid, "winifred-s-chart", "Winifred's chart", beat="The chart was found.")
    _commitment(cid)
    assert events.create(cid, "The coronation", "2026-05-20") == "the-coronation"
    _link(cid, "l1", MAP, OATH, "pays_off")
    _link(cid, "l2", CHART, OATH, "related_to")
    _link(cid, "l3", OATH, CORONATION, "before")
    _link(cid, "l4", CHART, CORONATION, "after")
    found = _by_ref(cid)
    assert found[MAP]["links"] == [
        {"id": "l1", "relation": "pays_off", "other": OATH, "direction": "out"}]
    assert found[OATH]["links"] == [
        {"id": "l1", "relation": "pays_off", "other": MAP, "direction": "in"},
        {"id": "l2", "relation": "related_to", "other": CHART, "direction": "both"},
        {"id": "l3", "relation": "before", "other": CORONATION, "direction": "out"}]
    assert found[CHART]["links"] == [
        {"id": "l2", "relation": "related_to", "other": OATH, "direction": "both"},
        {"id": "l4", "relation": "after", "other": CORONATION, "direction": "out"}]
    assert found[CORONATION]["links"] == [
        {"id": "l3", "relation": "before", "other": OATH, "direction": "in"},
        {"id": "l4", "relation": "after", "other": CHART, "direction": "in"}]
    assert found[OATH]["time_anchors"] == [CORONATION]
    assert found[CHART]["time_anchors"] == [CORONATION]
    assert found[MAP]["time_anchors"] == []
    assert found[CORONATION]["time_anchors"] == []


# ---- offscreen --------------------------------------------------------------


def test_offscreen_drops_player_tokens(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    root = _root(cid)
    seraphine, svid = pcs.create_pc(root, "Seraphine", [], persona={
        **pcs.blank_persona("Seraphine"), "birthdate": "--05-12"})
    mara, mvid = overlay.create_character(cid, "Mara")
    winifred, wvid = overlay.create_character(cid, "Winifred")
    appearances.appear(cid, S1, "pcs", seraphine, svid, "player")
    appearances.appear(cid, S1, "characters", mara, mvid, "player")
    appearances.appear(cid, S1, "characters", winifred, wvid, "npc")
    _thread(cid, scene=S1)

    onscreen = _by_ref(cid)
    assert onscreen[MAP]["actors"] == sorted(
        [f"pcs:{seraphine}", f"characters:{mara}", f"characters:{winifred}"])
    [bday] = [d for d in onscreen.values() if d["kind"] == "birthday"]
    assert bday["actors"] == [f"pcs:{seraphine}"]

    offscreen = _by_ref(cid, offscreen=True)
    assert offscreen[MAP]["actors"] == [f"characters:{winifred}"]
    assert offscreen[bday["ref"]]["actors"] == []
    assert offscreen[bday["ref"]]["label"] == "Seraphine"


def _garble_file(path):
    path.write_text("{ no", encoding="utf-8")


def _drop_a_role(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    del next(iter(record.values()))["role"]
    path.write_text(json.dumps(record), encoding="utf-8")


@pytest.mark.parametrize("damage", [_garble_file, _drop_a_role],
                         ids=["garbled-file", "entry-without-role"])
def test_offscreen_fails_closed_on_an_unreadable_roster(monkeypatch, tmp_path, damage):
    """With no roster nobody can say who the player is, so `offscreen` drops
    every actor rather than letting a player-seated character through on the
    chronicle's cast."""
    cid = _campaign(monkeypatch, tmp_path)
    mara, mvid = overlay.create_character(cid, "Mara")
    winifred, wvid = overlay.create_character(cid, "Winifred")
    appearances.appear(cid, S1, "characters", mara, mvid, "player")
    appearances.appear(cid, S1, "characters", winifred, wvid, "npc")
    _write_chronicle(cid, {S1: [f"characters/{mara}"]})
    _thread(cid, scene=S1)
    assert _by_ref(cid, offscreen=True)[MAP]["actors"] == [f"characters:{winifred}"]

    damage(_root(cid) / "appearances.json")
    assert _by_ref(cid)[MAP]["actors"] == [f"characters:{mara}"]   # the chronicle still casts her
    assert _by_ref(cid, offscreen=True)[MAP]["actors"] == []


# ---- failure ----------------------------------------------------------------


def test_snapshot_survives_garbled_continuity_and_ledgers(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _thread(cid)
    _commitment(cid, due="2026-05-14")
    events.create(cid, "The coronation", "2026-05-20")
    _link(cid, "l1", MAP, OATH, "pays_off")
    for name in ("continuity.json", "plot.json", "events.json"):
        (_root(cid) / name).write_text("{ no", encoding="utf-8")
    out = drivers.snapshot(cid)
    assert [d["ref"] for d in out["drivers"] if d["kind"] == "commitment"] == [OATH]
    assert [d for d in out["drivers"] if d["kind"] == "thread"] == []
    assert out["fixed"] == F(cid, "2026-05-10")


def test_a_continuity_side_failure_falls_back_to_the_physical_records(monkeypatch, tmp_path):
    """Defence in depth past `doc.read`: whatever else makes the effective
    projection raise costs the merge (spec 3.9), never the drivers -- the
    thread and the commitment are still listed, from the physical ledgers."""
    from grimoire.store.continuity import effective

    def _boom(*_a, **_k):
        raise RuntimeError("an unanticipated continuity.json shape")

    cid = _campaign(monkeypatch, tmp_path)
    _thread(cid)
    _commitment(cid)
    monkeypatch.setattr(effective, "threads", _boom)
    monkeypatch.setattr(effective, "commitments", _boom)
    found = _by_ref(cid)
    assert found[MAP]["label"] == "Mara's map"
    assert found[OATH]["label"] == "Mara's oath"

