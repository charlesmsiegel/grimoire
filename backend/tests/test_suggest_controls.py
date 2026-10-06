"""The suggestion controls and the rendered driver view (capstone spec §15,
§16.3; Slice E Decisions 8-11).

`driver_view` is pure over a snapshot, so these build their snapshots by hand:
the cap, the pinning and the ordering are arithmetic over rows, and a store
would only make the counts harder to read.
"""

from __future__ import annotations

from grimoire.store import suggest
from grimoire.store.continuity import pressure
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
