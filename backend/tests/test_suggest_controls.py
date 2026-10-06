"""The suggestion controls and the rendered driver view (capstone spec §15,
§16.3; Slice E Decisions 8-11).

`driver_view` is pure over a snapshot, so these build their snapshots by hand:
the cap, the pinning and the ordering are arithmetic over rows, and a store
would only make the counts harder to read.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import calendars, campaigns, suggest, worlds
from grimoire.store.continuity import doc as continuity_doc
from grimoire.store.continuity import effective, pressure
from grimoire.store.continuity.drivers import DRIVER_KINDS
from grimoire.store.suggest import NO_CONTROLS, Controls


def _driver(ref: str, state: str = "ok", dormancy: int | None = None,
            in_days: int | None = None, label: str = "") -> dict:
    return {"ref": ref, "kind": ref.split(":", 1)[0], "label": label or ref,
            "summary": "", "actors": [], "status": "",
            "pressure": {"state": state, "in_days": in_days, "friendly": ""},
            "time_anchors": [], "links": [], "dormancy": dormancy}


def _threads(n: int) -> list[dict]:
    """`n` ok threads, the first the coldest, so the sort keeps their order."""
    return [_driver(f"thread:t{i:02d}", dormancy=100 - i) for i in range(n)]


def _item(ref: str, kind: str, in_days: int | None, state: str, *,
          subject: str | None = None, precision: str | None = "exact",
          relation: str = "on", label: str = "") -> dict:
    return {"ref": ref, "kind": kind, "label": label or ref,
            "native": "" if in_days is None else f"n{in_days}",
            "friendly": "" if in_days is None else f"day {in_days}",
            "fixed": None if in_days is None else 1000 + in_days, "in_days": in_days,
            "relation": relation, "state": state, "subject": subject, "due_text": "",
            "precision": precision if in_days is not None else None,
            "actor": None, "age": None}


def test_controls_tuples_are_pinned():
    assert suggest.DRIVER_PROMPT_CAP == 40
    assert suggest.MUST_CAP == 3
    assert suggest.TIME_MODES == ("auto", "near", "move", "anchor")
    assert suggest.ANCHOR_RELATIONS == ("before", "on", "after", "by")
    assert suggest.MUST_KINDS == ("thread", "commitment")
    assert suggest.TEMPORAL_KINDS == ("event", "birthday", "holiday")


def test_no_controls_is_inactive():
    assert Controls() == NO_CONTROLS
    assert NO_CONTROLS.active is False
    assert Controls(time_mode="near").active is True
    assert Controls(focus=("thread:a",)).active is True
    assert Controls(avoid=("thread:a",)).active is True
    assert Controls(must=("thread:a",)).active is True


def test_pinned_is_must_then_focus_then_anchor_once_each():
    c = Controls(focus=("thread:b", "thread:a"), must=("thread:a", "commitment:c"),
                 time_mode="anchor", anchor="event:e", relation="on")
    assert c.pinned == ("thread:a", "commitment:c", "thread:b", "event:e")
    assert Controls().pinned == ()


def test_view_caps_at_forty_and_counts_the_rest():
    view = suggest.driver_view({"driver_index": _threads(45)}, NO_CONTROLS)
    assert len(view["index"]) == 40
    assert view["more"] == 5
    assert [r["ref"] for r in view["index"]] == [f"thread:t{i:02d}" for i in range(40)]
    assert set(view["index"][0]) == {"ref", "label", "kind", "state", "when"}


def test_a_snapshot_without_driver_keys_yields_an_empty_view():
    view = suggest.driver_view({}, NO_CONTROLS)
    assert (view["index"], view["more"], view["timeline"], view["timeline_more"],
            view["links"]) == ([], 0, [], 0, [])
    assert (view["focus"], view["avoid"], view["must"], view["anchor"]) == ([], [], [], None)
    assert view["active"] is False


def test_pinned_refs_are_always_rendered():
    rows = [*_threads(43), _driver("commitment:c", dormancy=0),
            _driver("event:e", in_days=30)]
    snap = {"driver_index": rows,
            "anchors": [{"ref": "event:e", "kind": "event", "label": "The coronation",
                         "native": "n30", "friendly": "day 30", "fixed": 1030,
                         "in_days": 30, "precision": "exact"}]}
    controls = Controls(focus=("thread:t42",), must=("commitment:c",), time_mode="anchor",
                        anchor="event:e", relation="before")
    view = suggest.driver_view(snap, controls)
    refs = [r["ref"] for r in view["index"]]
    assert {"thread:t42", "commitment:c", "event:e"} <= set(refs)
    assert len(refs) == 40
    assert view["more"] == 5
    assert view["anchor"] == {"ref": "event:e", "label": "The coronation",
                              "friendly": "day 30", "relation": "before"}

    many = suggest.driver_view({"driver_index": _threads(45)},
                               Controls(focus=tuple(f"thread:t{i:02d}" for i in range(4, 45))))
    assert len(many["index"]) == 41
    assert many["more"] == 4


def test_view_orders_by_pressure_then_dormancy():
    rows = [_driver("thread:quiet", "ok", dormancy=1),
            _driver("event:gone", "passed", in_days=-3),
            _driver("thread:cold", "ok", dormancy=9),
            _driver("commitment:soon", "due_soon", dormancy=0, in_days=2),
            _driver("thread:stale", "stale", dormancy=5),
            _driver("commitment:late", "overdue", dormancy=0, in_days=-1)]
    view = suggest.driver_view({"driver_index": rows}, NO_CONTROLS)
    assert [r["ref"] for r in view["index"]] == [
        "commitment:late", "commitment:soon", "event:gone", "thread:stale",
        "thread:cold", "thread:quiet"]


def test_view_rows_carry_the_when_phrase():
    rows = [_driver("commitment:late", "overdue", dormancy=0, in_days=-1),
            _driver("commitment:soon", "due_soon", dormancy=0, in_days=4),
            _driver("event:undated", "ok"),
            _driver("thread:quiet", "ok", dormancy=1)]
    when = {r["ref"]: r["when"] for r in
            suggest.driver_view({"driver_index": rows}, NO_CONTROLS)["index"]}
    assert when == {"commitment:late": "1 day ago", "commitment:soon": "in 4 days",
                    "event:undated": "undated", "thread:quiet": ""}


def test_view_links_only_between_rendered_drivers():
    rows = _threads(41)   # thread:t40 is the one cut
    link = {"id": "l-1", "a": "thread:t00", "b": "thread:t01", "relation": "pays_off"}
    snap = {"driver_index": rows,
            "links": [link, dict(link),
                      {"id": "l-2", "a": "thread:t00", "b": "thread:t40",
                       "relation": "pays_off"}]}
    view = suggest.driver_view(snap, NO_CONTROLS)
    assert "thread:t40" not in {r["ref"] for r in view["index"]}
    assert view["links"] == [{"a": "thread:t00", "relation": "pays_off", "b": "thread:t01"}]


def _capped_timeline() -> tuple[list[dict], str, str]:
    overdue = [_item(f"commitment:c{i:02d}", "deadline", i - 41, "overdue",
                     subject=f"commitment:c{i:02d}") for i in range(41)]   # -41 .. -1
    sooner = _item("holiday:1002:Saltmarch Eve", "holiday", 2, "upcoming")
    later = [_item(f"event:fair-{d}", "event", d, "upcoming") for d in (20, 21, 22)]
    anchor = _item("event:the-coronation", "event", 30, "upcoming")
    return [*overdue, sooner, *later, anchor], sooner["ref"], anchor["ref"]


def test_timeline_is_capped_but_keeps_sooner_and_anchors():
    items, sooner, anchor = _capped_timeline()
    assert len(items) == 46
    snap = {"timeline": items, "sooner_ref": sooner}
    controls = Controls(time_mode="anchor", anchor=anchor, relation="before")
    view = suggest.driver_view(snap, controls)
    refs = [r["ref"] for r in view["timeline"]]
    assert len(refs) == 40
    assert view["timeline_more"] == 6
    assert sooner in refs and anchor in refs
    assert refs == [i["ref"] for i in items if i["ref"] in set(refs)]   # snapshot order
    cut = [i["ref"] for i in items if i["ref"] not in set(refs)]
    assert cut == ["commitment:c38", "commitment:c39", "commitment:c40",
                   "event:fair-20", "event:fair-21", "event:fair-22"]
    rows = {r["ref"]: r for r in view["timeline"]}
    assert rows[sooner]["when"] == "in 2 days"
    assert rows["commitment:c00"]["when"] == "41 days ago"

    focused = suggest.driver_view(snap, Controls(focus=("commitment:c40",), time_mode="anchor",
                                                 anchor=anchor, relation="before"))
    frefs = [r["ref"] for r in focused["timeline"]]
    assert "commitment:c40" in frefs and sooner in frefs and anchor in frefs
    assert len(frefs) == 40
    assert focused["timeline_more"] == 6

    small = items[:5] + items[41:46]
    whole = suggest.driver_view({"timeline": small, "sooner_ref": sooner}, NO_CONTROLS)
    assert [r["ref"] for r in whole["timeline"]] == [i["ref"] for i in small]
    assert whole["timeline_more"] == 0


def test_a_pinned_commitment_keeps_its_linked_deadline():
    """Decision 11 pins on `subject` as well as `ref`: a linked deadline's ref
    is its event, so a focus or must on the commitment reaches that row only
    through its subject."""
    items, sooner, _anchor = _capped_timeline()
    linked = _item("event:saltmarch-fair-25", "linked_deadline", 25, "upcoming",
                   subject="commitment:mara-s-oath", relation="before",
                   label="Mara's oath")
    snap = {"timeline": [*items, linked], "sooner_ref": sooner}

    def rendered(controls: Controls) -> list[tuple[str, str]]:
        return [(r["ref"], r["kind"]) for r in suggest.driver_view(snap, controls)["timeline"]]

    row = ("event:saltmarch-fair-25", "linked_deadline")
    assert row not in rendered(NO_CONTROLS)      # past the cap, cut without a control
    for controls in (Controls(focus=("commitment:mara-s-oath",)),
                     Controls(must=("commitment:mara-s-oath",))):
        refs = rendered(controls)
        assert row in refs, controls
        assert len(refs) == suggest.DRIVER_PROMPT_CAP


def test_view_is_dated_only_with_a_now():
    assert suggest.driver_view({"now": "2026-05-10"}, NO_CONTROLS)["dated"] is True
    assert suggest.driver_view({"now": ""}, Controls(time_mode="near"))["dated"] is False
    assert suggest.driver_view({}, Controls(time_mode="move"))["dated"] is False


def test_timeline_when_phrases():
    items = [_item("event:a", "event", 0, "today"),
             _item("event:b", "event", 1, "upcoming"),
             _item("event:c", "event", 5, "upcoming"),
             _item("event:d", "event", -1, "passed"),
             _item("event:e", "event", -3, "passed"),
             _item("birthday:characters:mara:month:2026-05", "birthday", None, "ok",
                   precision="month"),
             _item("event:f", "event", None, "ok")]
    items[5]["precision"] = "month"
    view = suggest.driver_view({"timeline": items}, NO_CONTROLS)
    assert [r["when"] for r in view["timeline"]] == [
        "today", "in 1 day", "in 5 days", "1 day ago", "3 days ago", "day unknown",
        "undated"]


def test_view_actions_come_from_the_tuple():
    view = suggest.driver_view({}, NO_CONTROLS)
    assert [a["kind"] for a in view["actions"]] == list(DRIVER_KINDS)
    assert view["actions"][0] == {"kind": "thread", "actions": ["advance", "close_candidate"]}
    assert view["high_pressure"] == [s for s in pressure.SORT_ORDER
                                     if s in pressure.HIGH_PRESSURE]
    assert view["high_pressure"] == ["overdue", "today", "due_soon"]


def test_view_controls_carry_labels():
    rows = [_driver("thread:a", label="Mara's map"), _driver("commitment:b", label="Mara's oath")]
    view = suggest.driver_view({"driver_index": rows, "near_days": 9},
                               Controls(focus=("thread:a",), must=("commitment:b",),
                                        avoid=("thread:gone",), time_mode="near"))
    assert view["focus"] == [{"ref": "thread:a", "label": "Mara's map"}]
    assert view["must"] == [{"ref": "commitment:b", "label": "Mara's oath"}]
    assert view["avoid"] == [{"ref": "thread:gone", "label": "thread:gone"}]
    assert (view["time_mode"], view["near_days"], view["active"]) == ("near", 9, True)


# ---- Task 4: claims, anchors, date derivation, constraint misses, card order ----
#
# Pure over a hand-built snapshot and the real Gregorian provider, the clock at
# NOW. The anchors carry exactly `drivers._ANCHOR_FIELDS`; timeline items carry
# no `year`/`month_key`, as Slice B's do not.

GREG = calendars.get_provider({"provider": "gregorian", "region": "", "custom_holidays": [],
                               "anchor": None})
NOW = "2026-05-10"
MAP = "thread:mara-s-map"
CHART = "thread:winifred-s-chart"
OATH = "commitment:mara-s-oath"
CORONATION = "event:the-coronation"
DEBT = "event:the-debt"                     # passed: a driver, never an anchor
MIDNIGHT = "event:the-midnight-deadline"    # undated: a driver, never an anchor
EVE = "holiday:739746:Saltmarch Eve"
MARA_JUNE = "birthday:characters:mara:month:2026-06"


def _fx(native: str) -> int:
    return calendars.fixed_of(GREG, native)


def _day(fixed: int) -> str:
    return GREG.format(fixed)


def _anchor(ref: str, native: str = "", *, label: str = "", fixed: int | None = None) -> dict:
    if fixed is None and native:
        fixed = _fx(native)
    month = suggest._month_of(ref) is not None
    return {"ref": ref, "kind": ref.split(":", 1)[0], "label": label or ref, "native": native,
            "friendly": GREG.describe(fixed)["friendly"] if fixed is not None else "June 2026",
            "fixed": fixed, "in_days": None if fixed is None else fixed - _fx(NOW),
            "precision": "month" if month else "exact"}


def _claim_snap(*, now: str = NOW, anchors: list[dict] | None = None, **over) -> dict:
    index = [_driver(MAP, "ok", dormancy=1, label="Mara's map"),
             _driver(OATH, "overdue", dormancy=0, in_days=-1, label="Mara's oath"),
             _driver(CORONATION, "upcoming", in_days=3, label="The coronation"),
             _driver(DEBT, "passed", in_days=-2, label="The debt"),
             _driver(MIDNIGHT, "ok", label="The midnight deadline"),
             _driver(EVE, "today", in_days=0, label="Saltmarch Eve"),
             _driver(MARA_JUNE, "ok", label="Mara")]
    if anchors is None:
        anchors = [_anchor(EVE, "2026-05-10", label="Saltmarch Eve"),
                   _anchor(CORONATION, "2026-05-13", label="The coronation"),
                   _anchor(MARA_JUNE, label="Mara")]
    base = {"now": now, "fixed": _fx(now) if now else None, "near_days": 7,
            "open_threads": [{"ref": MAP, "aliases": []}],
            "commitments": [{"ref": OATH, "aliases": []}],
            "timeline": [{"ref": MARA_JUNE, "kind": "birthday", "label": "Mara",
                          "native": "", "friendly": "", "fixed": None, "in_days": None,
                          "relation": "in_month", "state": "ok", "subject": None,
                          "due_text": "", "precision": "month", "actor": "characters:mara",
                          "age": None}],
            "driver_index": index, "anchors": anchors, "links": []}
    return {**base, **over}


def test_unknown_driver_is_dropped():
    entry = {"drivers": [{"ref": "thread:ghost", "action": "advance"},
                         {"ref": OATH, "action": "advance"},
                         "not a dict",
                         {"ref": MAP, "action": "advance"},
                         {"ref": MAP, "action": "close_candidate"},
                         {"ref": OATH, "action": "address"},
                         {"ref": 7, "action": "advance"},
                         {"action": "advance"}]}
    got = suggest.claim(entry, _claim_snap(), NO_CONTROLS)
    assert got == {"drivers": [{"ref": MAP, "action": "advance"},
                               {"ref": OATH, "action": "address"}],
                   "time_anchor": None, "unmet_must": [], "avoided": []}
    assert suggest.claim({"drivers": "x"}, _claim_snap(), NO_CONTROLS)["drivers"] == []
    assert suggest.claim({}, {}, NO_CONTROLS) == {"drivers": [], "time_anchor": None,
                                                  "unmet_must": [], "avoided": []}


def test_anchor_is_validated_and_auto_added():
    snap = _claim_snap()
    got = suggest.claim({"drivers": [{"ref": MAP, "action": "advance"}],
                         "time_anchor": {"ref": CORONATION, "relation": "before"}},
                        snap, NO_CONTROLS)
    assert got["time_anchor"] == {"ref": CORONATION, "relation": "before"}
    assert got["drivers"] == [{"ref": MAP, "action": "advance"},
                              {"ref": CORONATION, "action": "anchor"}]
    # listed already: not added twice
    again = suggest.claim({"drivers": [{"ref": CORONATION, "action": "anchor"}],
                           "time_anchor": {"ref": CORONATION, "relation": "by"}},
                          snap, NO_CONTROLS)
    assert again["drivers"] == [{"ref": CORONATION, "action": "anchor"}]
    for ref in (DEBT, MIDNIGHT, "event:ghost", MAP):
        got = suggest.claim({"drivers": [{"ref": MAP, "action": "advance"}],
                             "time_anchor": {"ref": ref, "relation": "before"}},
                            snap, NO_CONTROLS)
        assert got["time_anchor"] is None, ref
        assert got["drivers"] == [{"ref": MAP, "action": "advance"}], ref
    # an invalid model relation reads as `on`; a month anchor only ever takes `on`
    assert suggest.claim({"time_anchor": {"ref": CORONATION, "relation": "around"}},
                         snap, NO_CONTROLS)["time_anchor"] == {"ref": CORONATION,
                                                               "relation": "on"}
    assert suggest.claim({"time_anchor": {"ref": MARA_JUNE, "relation": "before"}},
                         snap, NO_CONTROLS)["time_anchor"] == {"ref": MARA_JUNE,
                                                               "relation": "on"}
    # every valid temporal pair is kept (§15.2 drops only unknown refs and
    # invalid pairs): a passed or undated event is a driver, never an anchor,
    # and claims through `drivers` alone; the time anchor is still auto-added
    stray = suggest.claim({"drivers": [{"ref": DEBT, "action": "anchor"},
                                       {"ref": EVE, "action": "anchor"}],
                           "time_anchor": {"ref": CORONATION, "relation": "on"}},
                          snap, NO_CONTROLS)
    assert stray["drivers"] == [{"ref": DEBT, "action": "anchor"},
                                {"ref": EVE, "action": "anchor"},
                                {"ref": CORONATION, "action": "anchor"}]
    for ref in (DEBT, MIDNIGHT, EVE):
        alone = suggest.claim({"drivers": [{"ref": ref, "action": "anchor"}]}, snap, NO_CONTROLS)
        assert alone["drivers"] == [{"ref": ref, "action": "anchor"}], ref
        assert alone["time_anchor"] is None, ref


def test_batch_anchor_forces_every_suggestion():
    snap = _claim_snap()
    model = {"drivers": [{"ref": MAP, "action": "advance"}],
             "time_anchor": {"ref": EVE, "relation": "after"}}
    forced = suggest.claim(model, snap, Controls(time_mode="anchor", anchor=CORONATION,
                                                 relation="before"))
    assert forced["time_anchor"] == {"ref": CORONATION, "relation": "before"}
    assert forced["drivers"] == [{"ref": MAP, "action": "advance"},
                                 {"ref": CORONATION, "action": "anchor"}]

    open_relation = Controls(time_mode="anchor", anchor=CORONATION)
    assert suggest.claim(model, snap, open_relation)["time_anchor"] == {
        "ref": CORONATION, "relation": "after"}
    invalid = {**model, "time_anchor": {"ref": EVE, "relation": "sideways"}}
    assert suggest.claim(invalid, snap, open_relation)["time_anchor"] == {
        "ref": CORONATION, "relation": "on"}
    assert suggest.claim({}, snap, open_relation)["time_anchor"] == {
        "ref": CORONATION, "relation": "on"}
    month = Controls(time_mode="anchor", anchor=MARA_JUNE)
    assert suggest.claim(model, snap, month)["time_anchor"] == {
        "ref": MARA_JUNE, "relation": "on"}


def test_focus_avoid_must_report_misses(monkeypatch, tmp_path):
    controls = Controls(must=(OATH,), avoid=(MAP,))
    entry = {"title": "Midnight at Saltmarch", "premise": "P", "cast": [], "location": "",
             "drivers": [{"ref": MAP, "action": "advance"}]}
    got = suggest.claim(entry, _claim_snap(), controls)
    assert got["unmet_must"] == [OATH]
    assert got["avoided"] == [MAP]
    assert got["drivers"] == []

    # an avoided anchor the model chose itself stays the anchor, and is reported
    anchored = suggest.claim({"time_anchor": {"ref": CORONATION, "relation": "on"}},
                             _claim_snap(), Controls(avoid=(CORONATION,)))
    assert anchored["time_anchor"] == {"ref": CORONATION, "relation": "on"}
    assert anchored["avoided"] == [CORONATION]
    assert anchored["drivers"] == []

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("Realm"))
    rows = suggest.parse_output(json.dumps({"suggestions": [entry]}), cid,
                                snapshot=_claim_snap(), controls=controls)
    assert [r["title"] for r in rows] == ["Midnight at Saltmarch"]
    assert rows[0]["unmet_must"] == [{"ref": OATH, "label": "Mara's oath"}]
    assert rows[0]["avoided"] == [{"ref": MAP, "label": "Mara's map"}]
    assert rows[0]["drivers"] == []


def test_a_canonical_alias_prevents_duplicate_drivers():
    snap = _claim_snap(open_threads=[{"ref": CHART, "aliases": [
        {"ref": MAP, "title": "Mara's map"}]}],
        driver_index=[_driver(CHART, "ok", dormancy=1, label="Winifred's chart")])
    got = suggest.claim({"drivers": [{"ref": MAP, "action": "advance"},
                                     {"ref": CHART, "action": "advance"}]},
                        snap, NO_CONTROLS)
    assert got["drivers"] == [{"ref": CHART, "action": "advance"}]


def test_on_derives_the_date():
    on = {"ref": CORONATION, "relation": "on"}
    for model_date in ("2026-06-01", "2026-05-11", ""):
        assert suggest.check_date(GREG, _claim_snap(), NO_CONTROLS, on, model_date) == (
            "2026-05-13", False)


@pytest.mark.parametrize(("relation", "bad", "good"), [
    ("before", "2026-05-13", "2026-05-12"),     # d = D, then D - 1
    ("by", "2026-05-14", "2026-05-13"),         # d = D + 1, then D
    ("after", "2026-05-13", "2026-05-14"),      # d = D, then D + 1
    ("before", "2026-05-09", "2026-05-10"),     # d < now, then now
], ids=["before-D", "by-D+1", "after-D", "before-past"])
def test_invalid_relation_dates_are_blanked_and_kept(relation, bad, good):
    anchor = {"ref": CORONATION, "relation": relation}
    snap = _claim_snap()
    assert suggest.check_date(GREG, snap, NO_CONTROLS, anchor, bad) == ("", True)
    assert suggest.check_date(GREG, snap, NO_CONTROLS, anchor, good) == (good, False)


def test_anchor_day_boundaries_ignore_the_time_of_day():
    evening = _anchor(CORONATION, "2026-05-12T20:00", label="The coronation")
    assert evening["fixed"] == _fx("2026-05-12")
    snap = _claim_snap(anchors=[evening])
    f = evening["fixed"]

    def check(relation: str, date: str) -> tuple[str, bool]:
        return suggest.check_date(GREG, snap, NO_CONTROLS,
                                  {"ref": CORONATION, "relation": relation}, date)

    assert check("on", "") == ("2026-05-12", False)
    assert check("before", "2026-05-12") == ("", True)
    assert check("before", "2026-05-11") == ("2026-05-11", False)
    assert check("by", "2026-05-12") == ("2026-05-12", False)
    assert check("after", "2026-05-12") == ("", True)
    assert check("after", "2026-05-13") == ("2026-05-13", False)
    assert check("after", _day(f + 400)) == (_day(f + 400), False)
    assert check("after", _day(f + 401)) == ("", True)


def test_near_and_move_blank_out_of_range_dates():
    snap = _claim_snap()
    n = _fx(NOW)
    near, move = Controls(time_mode="near"), Controls(time_mode="move")
    assert suggest.check_date(GREG, snap, near, None, NOW) == (NOW, False)
    assert suggest.check_date(GREG, snap, near, None, _day(n + 7)) == (_day(n + 7), False)
    assert suggest.check_date(GREG, snap, near, None, _day(n + 8)) == ("", True)
    assert suggest.check_date(GREG, snap, near, None, _day(n - 1)) == ("", True)
    assert suggest.check_date(GREG, snap, move, None, NOW) == ("", True)
    assert suggest.check_date(GREG, snap, move, None, _day(n + 1)) == (_day(n + 1), False)
    # auto checks nothing
    assert suggest.check_date(GREG, snap, NO_CONTROLS, None, _day(n - 30)) == (
        _day(n - 30), False)


def test_an_absent_date_is_not_rejected():
    snap = _claim_snap()
    before = {"ref": CORONATION, "relation": "before"}
    assert suggest.check_date(GREG, snap, NO_CONTROLS, before, "") == ("", False)
    assert suggest.check_date(GREG, snap, Controls(time_mode="near"), None, "") == ("", False)
    month_on = {"ref": MARA_JUNE, "relation": "on"}
    assert suggest.check_date(GREG, snap, NO_CONTROLS, month_on, "") == ("", False)
    # no calendar: nothing is derived or checked
    assert suggest.check_date(None, snap, NO_CONTROLS, {"ref": CORONATION, "relation": "on"},
                              "2026-05-12") == ("", False)


def test_no_now_skips_the_lower_bound():
    snap = _claim_snap(now="")
    before = {"ref": CORONATION, "relation": "before"}
    assert suggest.check_date(GREG, snap, NO_CONTROLS, before, "2026-01-01") == (
        "2026-01-01", False)
    assert suggest.check_date(GREG, snap, NO_CONTROLS, before, "2026-05-13") == ("", True)
    assert suggest.check_date(GREG, snap, Controls(time_mode="near"), None, "2027-01-01") == (
        "2027-01-01", False)
    assert suggest.check_date(GREG, snap, Controls(time_mode="move"), None, "2020-01-01") == (
        "2020-01-01", False)


def test_month_only_birthday_on_keeps_only_its_month():
    snap = _claim_snap()
    item = snap["timeline"][0]
    assert "year" not in item and "month_key" not in item
    on = {"ref": MARA_JUNE, "relation": "on"}
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2026-06-20") == (
        "2026-06-20", False)
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2026-07-01") == ("", True)
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2027-06-20") == ("", True)
    # the birthday month is the present one: a day of it already behind `now`
    # fails the same lower bound `before` and `by` apply (§15.3)
    may = "birthday:pcs:winifred:month:2026-05"
    snap = _claim_snap(now="2026-05-20", anchors=[_anchor(may, label="Winifred")])
    on = {"ref": may, "relation": "on"}
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2026-05-02") == ("", True)
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2026-05-20") == (
        "2026-05-20", False)
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2026-05-31") == (
        "2026-05-31", False)
    # with no `now` the lower bound is skipped, as elsewhere
    snap = _claim_snap(now="", anchors=[_anchor(may, label="Winifred")])
    assert suggest.check_date(GREG, snap, NO_CONTROLS, on, "2026-05-02") == (
        "2026-05-02", False)


def test_month_of_parses_refs():
    assert suggest._month_of("birthday:characters:mara:month:2026-06") == (2026, "06")
    assert suggest._month_of("birthday:characters:mara:month:5786-Adar-I") == (5786, "Adar-I")
    assert suggest._month_of("birthday:pcs:winifred:month:-12-06") == (-12, "06")
    assert suggest._month_of("birthday:characters:month:month:2026-06") == (2026, "06")
    assert suggest._month_of("birthday:characters:mara:739780") is None
    assert suggest._month_of("event:the-coronation") is None
    assert suggest._month_of("birthday:characters:mara:month:june") is None
    assert suggest._month_of("") is None


def test_on_date_is_canonical():
    handwritten = _anchor(CORONATION, fixed=GREG.parse("2026-5-13"), label="The coronation")
    handwritten["native"] = "2026-5-13"
    snap = _claim_snap(anchors=[handwritten])
    assert suggest.check_date(GREG, snap, NO_CONTROLS, {"ref": CORONATION, "relation": "on"},
                              "") == ("2026-05-13", False)


def _row(title: str, *refs: str, anchor: str | None = None) -> dict:
    return {"title": title, "drivers": [{"ref": r} for r in refs],
            "time_anchor": {"ref": anchor} if anchor else None}


def test_high_pressure_card_first():
    snap = _claim_snap(driver_index=[*_claim_snap()["driver_index"],
                                     _driver(CHART, "ok", dormancy=2)])
    rows = [_row("ok", MAP), _row("overdue", OATH), _row("focus", CHART)]
    assert [r["title"] for r in suggest.order_cards(rows, snap, NO_CONTROLS)] == [
        "overdue", "ok", "focus"]
    # only one card is promoted; a time anchor counts as a claim
    rows = [_row("ok", MAP), _row("eve", anchor=EVE), _row("overdue", OATH)]
    assert [r["title"] for r in suggest.order_cards(rows, snap, NO_CONTROLS)] == [
        "eve", "ok", "overdue"]
    # a `today` holiday claimed through `drivers` alone, with no time anchor
    claimed = suggest.claim({"drivers": [{"ref": EVE, "action": "anchor"}]}, snap, NO_CONTROLS)
    rows = [{"title": "ok", "drivers": [{"ref": MAP, "action": "advance"}], "time_anchor": None},
            {"title": "eve", **claimed}]
    assert [r["title"] for r in suggest.order_cards(rows, snap, NO_CONTROLS)] == ["eve", "ok"]
    assert suggest.order_cards([], snap, NO_CONTROLS) == []


def test_distinct_focus_cards_come_next():
    snap = _claim_snap(driver_index=[_driver(MAP, "ok"), _driver(CHART, "ok")])
    controls = Controls(focus=(MAP, CHART))
    rows = [_row("a1", MAP), _row("a2", MAP), _row("b", CHART)]
    assert [r["title"] for r in suggest.order_cards(rows, snap, controls)] == ["a1", "b", "a2"]
    # a high-pressure card's focus refs count as covered
    snap = _claim_snap(driver_index=[_driver(MAP, "ok"), _driver(CHART, "ok"),
                                     _driver(OATH, "overdue")])
    controls = Controls(focus=(MAP, CHART, OATH))
    rows = [_row("map", MAP), _row("oath", OATH), _row("oath2", OATH), _row("chart", CHART)]
    assert [r["title"] for r in suggest.order_cards(rows, snap, controls)] == [
        "oath", "map", "chart", "oath2"]


def test_raw_suggestions_shapes():
    assert suggest.raw_suggestions("no json here at all") is None
    assert suggest.raw_suggestions('{"suggestions": 3}') == []
    assert suggest.raw_suggestions('{"next_date": "2026-05-12"}') == []
    assert suggest.raw_suggestions('[{"title": "T"}, 4]') == [{"title": "T"}, 4]
    assert suggest.raw_suggestions('{"suggestions": [{"title": "T"}]}') == [{"title": "T"}]
    assert suggest.raw_suggestions("42") == []


# ---- Task 5: resolve_controls -----------------------------------------------
#
# Over `_claim_snap`'s hand-built capture: its index is the request-time driver
# set, and its anchors are the only refs a time anchor may name.

LEDGER = "thread:find-the-ledger"


def _real_campaign(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


def test_resolve_controls_dedupes_and_orders(monkeypatch, tmp_path):
    cid = _real_campaign(monkeypatch, tmp_path)
    snap = _claim_snap(driver_index=[*_claim_snap()["driver_index"],
                                     _driver(LEDGER, "ok", dormancy=2, label="Find the ledger")])
    got = suggest.resolve_controls(
        cid, snap, focus_refs=[f" {LEDGER} ", MAP, "", "  ", LEDGER, OATH],
        avoid_refs=[CORONATION, CORONATION], must_refs=[OATH, OATH],
        time_mode="anchor", time_anchor_ref=f" {EVE} ", time_anchor_relation="before")
    # stripped, blanks dropped, de-duplicated in request order; focus loses
    # what must already holds
    assert got == Controls(focus=(LEDGER, MAP), avoid=(CORONATION,), must=(OATH,),
                           time_mode="anchor", anchor=EVE, relation="before")

    assert suggest.resolve_controls(cid, snap) == NO_CONTROLS
    # a relation means nothing without an anchor
    assert suggest.resolve_controls(cid, snap, time_mode="near",
                                    time_anchor_relation="by") == Controls(time_mode="near")


def test_resolve_controls_refusals_carry_their_reason(monkeypatch, tmp_path):
    cid = _real_campaign(monkeypatch, tmp_path)
    snap = _claim_snap()

    def reason(**kw) -> tuple:
        with pytest.raises(suggest.ControlsError) as caught:
            suggest.resolve_controls(cid, snap, **kw)
        e = caught.value
        return e.status, e.kind, e.reason, e.refs

    assert reason(must_refs=[MAP], avoid_refs=[MAP]) == (400, "bad_controls", "must_avoid", None)
    assert reason(must_refs=[MAP, OATH, LEDGER, CHART])[2] == "must_cap"
    assert reason(must_refs=[CORONATION])[2] == "must_kind"
    assert reason(time_mode="anchor")[2] == "anchor_missing"
    assert reason(time_mode="near", time_anchor_ref=CORONATION)[2] == "anchor_without_mode"
    assert reason(time_mode="anchor", time_anchor_ref=MAP)[2] == "anchor_kind"
    assert reason(time_mode="anchor", time_anchor_ref="event")[2] == "anchor_kind"
    assert reason(time_mode="anchor", time_anchor_ref=MARA_JUNE,
                  time_anchor_relation="after")[2] == "anchor_relation"
    # every 400 is decided before staleness: a ghost does not mask one
    assert reason(focus_refs=["thread:ghost"], must_refs=[CORONATION])[2] == "must_kind"
    # the 409 names every ref the capture lacks, in request order
    assert reason(focus_refs=["thread:ghost"], must_refs=["commitment:gone"],
                  time_mode="anchor", time_anchor_ref=DEBT) == (
        409, "stale_drivers", "", ["thread:ghost", "commitment:gone", DEBT])
    # a month anchor missing from the capture is judged by its ref's shape
    assert reason(time_mode="anchor", time_anchor_ref="birthday:characters:mara:month:2027-01",
                  time_anchor_relation="by")[2] == "anchor_relation"
    # `on` (or no relation) is what a month anchor takes
    for relation in ("", "on"):
        got = suggest.resolve_controls(cid, snap, time_mode="anchor", time_anchor_ref=MARA_JUNE,
                                       time_anchor_relation=relation)
        assert (got.anchor, got.relation) == (MARA_JUNE, relation)


def test_resolve_controls_the_anchor_beats_avoid(monkeypatch, tmp_path):
    cid = _real_campaign(monkeypatch, tmp_path)
    got = suggest.resolve_controls(cid, _claim_snap(), focus_refs=[MAP, CORONATION],
                                   avoid_refs=[CORONATION, MAP], time_mode="anchor",
                                   time_anchor_ref=CORONATION)
    # avoid loses the anchor (Decision 26); focus loses only what must and
    # avoid still hold, so the anchor may stay a focus driver
    assert got == Controls(focus=(CORONATION,), avoid=(MAP,), time_mode="anchor",
                           anchor=CORONATION)


def test_resolve_controls_follows_an_alias_over_a_garbled_ledger(monkeypatch, tmp_path):
    cid = _real_campaign(monkeypatch, tmp_path)
    continuity_doc.put_alias(cid, MAP, {"to": LEDGER, "created": "", "source": "manual",
                                        "note": ""})
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    ledgers = effective.Ledgers.load(cid)
    assert "plot" in ledgers.unreadable and ledgers.exists(LEDGER) is None
    snap = _claim_snap(driver_index=[_driver(LEDGER, "ok", dormancy=0, label="Find the ledger")])
    got = suggest.resolve_controls(cid, snap, focus_refs=[MAP])
    assert got.focus == (LEDGER,)
    # two spellings of one driver are one control
    assert suggest.resolve_controls(cid, snap, focus_refs=[MAP, LEDGER]).focus == (LEDGER,)
    with pytest.raises(suggest.ControlsError) as caught:
        suggest.resolve_controls(cid, snap, must_refs=[MAP], avoid_refs=[LEDGER])
    assert caught.value.reason == "must_avoid"


def test_resolve_controls_canonicalization_falls_back_to_identity(monkeypatch, tmp_path):
    cid = _real_campaign(monkeypatch, tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("the continuity doc is unreadable")
    monkeypatch.setattr(effective, "live_canon", boom)
    got = suggest.resolve_controls(cid, _claim_snap(), focus_refs=[MAP], must_refs=[OATH],
                                   avoid_refs=[CORONATION])
    assert got == Controls(focus=(MAP,), avoid=(CORONATION,), must=(OATH,))
