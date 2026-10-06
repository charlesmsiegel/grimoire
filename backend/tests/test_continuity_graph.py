"""`continuity.graph`: the Story Graph projection (capstone spec §19, §20).

One deterministic, read-only payload of nodes and edges over readers that
already exist. Dates are asserted on the campaign's own fixed-day axis
(`F(cid, ...)`, B's helper), never as Gregorian arithmetic.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from grimoire import store
from grimoire.store import (
    appearances,
    calendars,
    campaigns,
    chronicle,
    clock,
    commitments,
    events,
    overlay,
    plot,
    relationships,
    scene_ideas,
)
from grimoire.store.continuity import (
    candidates,
    canon,
    doc,
    drivers,
    effective,
    graph,
    involvement,
    pending,
    pressure,
    review,
)
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.scenes import paths as scenes_paths
from tests.test_continuity_pressure import (
    _BROKEN_PROVIDER_SRC,
    F,
    _actor,
    _campaign,
    _plugin,
    _primary,
    _provider,
    _rule,
)

#: A plugin that loads but cannot read a date: `__init__` succeeds, so
#: `primary_provider` answers it, and `parse`/`describe` raise something that is
#: not a CalendarError -- the per-row failure no provider read reports.
_UNPARSING_PROVIDER_SRC = textwrap.dedent(
    """
    from grimoire.store.calendars.base import CalendarProvider, register

    class _UnparsingProvider(CalendarProvider):
        def __init__(self, config):
            pass

        def parse(self, native):
            raise RuntimeError("this calendar plugin cannot parse")

        def format(self, fixed):
            return ""

        def describe(self, fixed):
            raise RuntimeError("this calendar plugin cannot describe")

        def holidays(self, start_fixed, end_fixed):
            return []

        def months(self, year):
            return []

    register("unparsing-test-calendar", _UnparsingProvider, "Unparsing Test Calendar")
    """
)


def _scene_nodes(g: dict) -> list[dict]:
    return [n for n in g["nodes"] if n["kind"] == "scene"]


def _node(g: dict, sid: str) -> dict:
    (node,) = [n for n in g["nodes"] if n["id"] == f"scene:{sid}"]
    return node


def _stamp(cid: str, sid: str, native: str) -> str:
    """Set the scene's moment; the first stamp renames, so the new id comes back."""
    return store.scenes.set_datetime(cid, sid, native)["id"]


def _set_updated(cid: str, sid: str, stamp: str) -> None:
    p = scenes_paths._scene_path(cid, sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["updated"] = stamp
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")


# ---- the vocabulary ---------------------------------------------------------


def test_graph_tuples_are_pinned():
    assert graph.NODE_KINDS == ("scene", "character", "pc", "location", "thread",
                                "commitment", "event", "idea", "birthday", "holiday")
    assert graph.EDGE_KINDS == ("appeared_in", "occurred_at", "opened_in", "advanced_in",
                                "touched_in", "closed_in", "resolved_in", "involves",
                                "serves", "anchored_to", "feeling", "bond", "birthday_of",
                                "link", "merged_into", "possible_duplicate",
                                "possible_relation")
    assert graph.EDGE_SOURCES == ("structural", "reviewed", "candidate", "alias")
    assert graph.EVENT_STATUSES == ("scheduled", "fired", "passed", "undated")
    assert graph.PARTS == ("calendar", "scenes", "chronicle", "plot", "commitments",
                           "events", "continuity", "candidates", "relationships",
                           "scene_ideas", "names")
    relations = ("continues", "subthread_of", "pays_off", "before", "on", "after", "by",
                 "related_to")
    assert effective.LINK_RELATIONS == relations == tuple(effective.RELATIONS)
    # The kind->prefix table is stated once; the graph's kinds are its kinds
    # minus the two §19.2 leaves optional.
    assert set(graph.NODE_KINDS) == set(canon.KIND_OF_PREFIX.values()) - {"group", "fact"}
    # §20 as one set: B's and E's tuples, re-pinned beside the graph's.
    assert drivers.DRIVER_KINDS == ("thread", "commitment", "event", "birthday", "holiday")
    assert drivers.DRIVER_ACTIONS == ("advance", "close_candidate", "address",
                                      "fulfill_candidate", "break_candidate",
                                      "expire_candidate", "anchor")
    assert pressure.PRESSURE_STATES == ("overdue", "passed", "today", "due_soon",
                                        "upcoming", "stale", "ok")


def test_edge_id_is_stable_and_kind_scoped():
    eid = graph.edge_id("appeared_in", "characters:mara", "scene:001")
    assert len(eid) == 21 and eid.startswith("e")
    assert eid == graph.edge_id("appeared_in", "characters:mara", "scene:001")
    assert eid != graph.edge_id("occurred_at", "characters:mara", "scene:001")


# ---- the scene spine --------------------------------------------------------


def test_an_empty_campaign_is_an_empty_graph(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    friendly = calendars.friendly(_provider(cid), "2026-05-10")
    assert graph.build(cid) == {
        "now": {"native": "2026-05-10", "friendly": friendly, "fixed": F(cid, "2026-05-10")},
        "nodes": [], "edges": [], "omitted": [],
    }


def test_scenes_follow_play_order_not_recency_or_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1 = store.scenes.create_scene(cid, "Saltmarch harbour")
    s2 = store.scenes.create_scene(cid, "Mara's flashback")
    s3 = store.scenes.create_scene(cid, "Winifred's chart")
    s1 = _stamp(cid, s1, "2026-05-20")
    s2 = _stamp(cid, s2, "2026-04-01")
    s3 = _stamp(cid, s3, "2026-05-12")
    # Recency deliberately out of play order, written rather than left to
    # one-second `now_iso()` stamps that tie.
    _set_updated(cid, s1, "2026-01-01T00:00:03Z")
    _set_updated(cid, s2, "2026-01-01T00:00:01Z")
    _set_updated(cid, s3, "2026-01-01T00:00:02Z")

    nodes = _scene_nodes(graph.build(cid))
    assert [n["id"] for n in nodes] == [f"scene:{s}" for s in sorted([s1, s2, s3])]
    assert [n["id"] for n in nodes] == [f"scene:{s1}", f"scene:{s2}", f"scene:{s3}"]
    assert [n["order"] for n in nodes] == [0, 1, 2]
    assert [n["label"] for n in nodes] == ["Saltmarch harbour", "Mara's flashback",
                                           "Winifred's chart"]

    # The positive control: recency and date both disagree with play order.
    assert [r["id"] for r in store.scenes.list_scenes(cid)] == [s1, s3, s2]
    fixed = {n["id"]: n["fixed"] for n in nodes}
    assert fixed[f"scene:{s2}"] < fixed[f"scene:{s3}"] < fixed[f"scene:{s1}"]


def test_scene_dates_carry_fixed_and_in_days(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    stamped = _stamp(cid, store.scenes.create_scene(cid, "Saltmarch harbour"),
                     "2026-05-12T20:00")
    chronicled = store.scenes.create_scene(cid, "Mara's map")
    chronicle.absorb(cid, {"id": chronicled, "date": "2026-05-08",
                           "location": "Saltmarch harbour"})
    bare = store.scenes.create_scene(cid, "Winifred's chart")

    g = graph.build(cid)
    a = _node(g, stamped)
    assert a["native"] == "2026-05-12T20:00"
    assert a["fixed"] == F(cid, "2026-05-12")
    assert a["in_days"] == 2
    assert a["friendly"] == calendars.friendly(_provider(cid), "2026-05-12T20:00")
    b = _node(g, chronicled)
    assert b["native"] == "2026-05-08" and b["in_days"] == -2
    assert b["place"] == "Saltmarch harbour"
    c = _node(g, bare)
    assert (c["native"], c["friendly"], c["fixed"], c["in_days"]) == ("", "", None, None)
    assert (c["done"], c["pcless"], c["place"]) == (False, False, "")
    assert g["omitted"] == []


def test_a_raising_plugin_leaves_scenes_undated(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _stamp(cid, store.scenes.create_scene(cid, "Saltmarch harbour"), "2026-05-12")
    _plugin(tmp_path, "broken_test", _BROKEN_PROVIDER_SRC)
    _primary(cid, "broken-test-calendar")

    g = graph.build(cid)
    node = _node(g, sid)
    assert node["native"] == "2026-05-12"
    assert node["fixed"] is None and node["in_days"] is None
    assert "calendar" in g["omitted"]


def test_a_plugin_that_loads_but_cannot_parse_is_a_broken_calendar(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _stamp(cid, store.scenes.create_scene(cid, "Saltmarch harbour"), "2026-05-12")
    _plugin(tmp_path, "unparsing_test", _UNPARSING_PROVIDER_SRC)
    _primary(cid, "unparsing-test-calendar")

    g = graph.build(cid)
    node = _node(g, sid)
    assert node["native"] == "2026-05-12"
    assert node["fixed"] is None and node["in_days"] is None
    assert g["now"]["native"] == "2026-05-10" and g["now"]["fixed"] is None
    assert "calendar" in g["omitted"]


def test_build_is_deterministic(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1 = _stamp(cid, store.scenes.create_scene(cid, "Saltmarch harbour"), "2026-05-12")
    store.scenes.create_scene(cid, "Mara's map")
    chronicle.absorb(cid, {"id": s1, "date": "2026-05-12", "location": "Saltmarch harbour"})
    assert graph.build(cid) == graph.build(cid)


def test_a_free_text_scene_date_is_undated_not_a_broken_calendar(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = store.scenes.create_scene(cid, "Saltmarch harbour")
    chronicle.absorb(cid, {"id": sid, "date": "midsummer"})

    g = graph.build(cid)
    node = _node(g, sid)
    assert node["native"] == "midsummer"
    assert node["fixed"] is None and node["in_days"] is None
    assert g["omitted"] == []


def test_a_free_text_seeded_present_is_not_a_broken_calendar(monkeypatch, tmp_path):
    # No clock: the present is seeded from the chronicle's latest date, which is
    # free text -- the provider's CalendarError is data, not a broken plugin.
    cid = _campaign(monkeypatch, tmp_path, now=None)
    sid = store.scenes.create_scene(cid, "Saltmarch harbour")
    chronicle.absorb(cid, {"id": sid, "date": "midsummer"})

    g = graph.build(cid)
    assert g["now"] == {"native": "midsummer", "friendly": "", "fixed": None}
    assert g["omitted"] == []


# ---- actors and locations ---------------------------------------------------


def _edges(g: dict, kind: str) -> list[dict]:
    return [e for e in g["edges"] if e["kind"] == kind]


def _pairs(g: dict, kind: str) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in _edges(g, kind)}


def _by_id(g: dict) -> dict[str, dict]:
    return {n["id"]: n for n in g["nodes"]}


def _seat(cid: str, sid: str, kind: str, aid: str, vid: str) -> None:
    role = "player" if kind == "pcs" else "npc"
    appearances.transitions.appear(cid, sid, kind, aid, vid, role, narrate=False)


def _cast(cid: str, sid: str, cast: list) -> None:
    chronicle.absorb(cid, {"id": sid, "one_line": "", "cast": cast})


def _relationships_file(cid: str):
    return campaigns.campaign_root(cid) / "relationships.json"


def test_appeared_in_unions_roster_and_chronicle_cast(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara, mara_v = overlay.create_character(cid, "Mara")
    winifred, _ = overlay.create_character(cid, "Winifred")
    seraphine, seraphine_v = overlay.create_pc(cid, "Seraphine", [])
    s1 = store.scenes.create_scene(cid, "Saltmarch harbour")
    s2 = store.scenes.create_scene(cid, "Mara's map")
    _seat(cid, s1, "characters", mara, mara_v)
    _cast(cid, s2, [f"characters/{winifred}"])
    _seat(cid, s2, "pcs", seraphine, seraphine_v)

    g = graph.build(cid)
    assert _pairs(g, "appeared_in") == {
        (f"characters:{mara}", f"scene:{s1}"),
        (f"characters:{winifred}", f"scene:{s2}"),
        (f"pcs:{seraphine}", f"scene:{s2}"),
    }
    nodes = _by_id(g)
    assert (nodes[f"characters:{mara}"]["kind"], nodes[f"characters:{mara}"]["label"]) == (
        "character", "Mara")
    assert (nodes[f"characters:{winifred}"]["kind"],
            nodes[f"characters:{winifred}"]["label"]) == ("character", "Winifred")
    assert (nodes[f"pcs:{seraphine}"]["kind"], nodes[f"pcs:{seraphine}"]["label"]) == (
        "pc", "Seraphine")
    edge = _edges(g, "appeared_in")[0]
    assert edge["source"] == "structural"
    assert edge["relation"] is None and edge["candidate_id"] is None
    assert edge["id"] == graph.edge_id("appeared_in", edge["from"], edge["to"])
    assert g["omitted"] == []


def test_occurred_at_follows_location_history(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    harbour = overlay.create_entity(cid, "locations", "Saltmarch harbour")
    docks = overlay.create_entity(cid, "locations", "Saltmarch docks")
    s1 = store.scenes.create_scene(cid, "Mara's map")
    s2 = store.scenes.create_scene(cid, "Winifred's chart")
    store.scenes.set_location(cid, s1, harbour)
    store.scenes.set_location(cid, s1, docks)

    g = graph.build(cid)
    assert [(e["from"], e["to"]) for e in _edges(g, "occurred_at")
            if e["from"] == f"scene:{s1}"] == sorted([
                (f"scene:{s1}", f"locations:{harbour}"),
                (f"scene:{s1}", f"locations:{docks}")])
    assert not [e for e in _edges(g, "occurred_at") if e["from"] == f"scene:{s2}"]
    nodes = _by_id(g)
    assert (nodes[f"locations:{harbour}"]["kind"],
            nodes[f"locations:{harbour}"]["label"]) == ("location", "Saltmarch harbour")
    assert nodes[f"locations:{docks}"]["label"] == "Saltmarch docks"
    assert g["omitted"] == []


def test_relationships_become_feeling_and_bond_edges(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:mara", "characters:winifred", 9, 2, -1, "wary")
    relationships.set_bond(cid, "characters:winifred", "characters:mara", "kin",
                           since_scene="009--gone")
    data = json.loads(_relationships_file(cid).read_text(encoding="utf-8"))
    data["feelings"]["groups:realm->characters:mara"] = {
        "trust": 1, "affection": 1, "tension": 1, "note": ""}
    _relationships_file(cid).write_text(json.dumps(data), encoding="utf-8")

    g = graph.build(cid)
    [feeling] = _edges(g, "feeling")
    assert (feeling["from"], feeling["to"]) == ("characters:mara", "characters:winifred")
    assert (feeling["trust"], feeling["affection"], feeling["tension"]) == (5, 2, 0)
    assert feeling["note"] == "wary"
    assert feeling["source"] == "structural"
    [bond] = _edges(g, "bond")
    assert (bond["from"], bond["to"]) == ("characters:mara", "characters:winifred")
    assert (bond["bond_type"], bond["since_scene"]) == ("kin", "009--gone")
    nodes = _by_id(g)
    assert "groups:realm" not in nodes
    assert nodes["characters:mara"]["kind"] == "character"
    assert g["omitted"] == []


def test_actor_labels_fall_back_to_the_id(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = store.scenes.create_scene(cid, "Saltmarch harbour")
    _cast(cid, sid, ["characters/winifred-gone"])

    g = graph.build(cid)
    node = _by_id(g)["characters:winifred-gone"]
    assert (node["kind"], node["label"]) == ("character", "winifred-gone")
    assert _pairs(g, "appeared_in") == {("characters:winifred-gone", f"scene:{sid}")}


def test_relationships_raising_costs_only_their_edges(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara, mara_v = overlay.create_character(cid, "Mara")
    sid = store.scenes.create_scene(cid, "Saltmarch harbour")
    _seat(cid, sid, "characters", mara, mara_v)
    _relationships_file(cid).write_text("[]", encoding="utf-8")

    g = graph.build(cid)
    assert _edges(g, "feeling") == [] and _edges(g, "bond") == []
    assert "relationships" in g["omitted"]
    assert _pairs(g, "appeared_in") == {(f"characters:{mara}", f"scene:{sid}")}


def test_malformed_cast_tokens_cost_only_their_edges(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    store.scenes.create_scene(cid, "Saltmarch harbour")
    s2 = store.scenes.create_scene(cid, "Mara's map")
    _cast(cid, s2, ["Mara", "characters/", "groups/realm", "characters/winifred"])

    g = graph.build(cid)
    assert [(e["from"], e["to"]) for e in _edges(g, "appeared_in")
            if e["to"] == f"scene:{s2}"] == [("characters:winifred", f"scene:{s2}")]
    node_ids = {n["id"] for n in g["nodes"]}
    assert all({e["from"], e["to"]} <= node_ids for e in g["edges"])


def test_an_unreadable_location_costs_only_location_labels(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara, mara_v = overlay.create_character(cid, "Mara")
    harbour = overlay.create_entity(cid, "locations", "Saltmarch harbour")
    sid = store.scenes.create_scene(cid, "Mara's map")
    _seat(cid, sid, "characters", mara, mara_v)
    store.scenes.set_location(cid, sid, harbour)
    (overlay.croot_of(cid) / "locations" / f"{harbour}.md").write_bytes(
        b"---\nname: \xff\xfe Saltmarch\n---\n\xff\n")

    g = graph.build(cid)
    nodes = _by_id(g)
    assert nodes[f"characters:{mara}"]["label"] == "Mara"
    assert nodes[f"locations:{harbour}"]["label"] == harbour
    assert _pairs(g, "occurred_at") == {(f"scene:{sid}", f"locations:{harbour}")}
    assert "names" in g["omitted"]


@pytest.mark.parametrize("body", ["{garbled", "[]"])
def test_a_garbled_appearance_record_is_reported(monkeypatch, tmp_path, body):
    cid = _campaign(monkeypatch, tmp_path)
    mara, mara_v = overlay.create_character(cid, "Mara")
    s1 = store.scenes.create_scene(cid, "Saltmarch harbour")
    s2 = store.scenes.create_scene(cid, "Mara's map")
    _seat(cid, s1, "characters", mara, mara_v)
    _cast(cid, s2, ["characters/winifred"])
    (campaigns.campaign_root(cid) / "appearances.json").write_text(body, encoding="utf-8")

    g = graph.build(cid)
    assert _pairs(g, "appeared_in") == {("characters:winifred", f"scene:{s2}")}
    assert "chronicle" in g["omitted"]


# ---- threads and commitments ------------------------------------------------


MAP = "thread:mara-s-map"
CHART = "thread:winifred-s-chart"
OATH = "commitment:mara-s-oath"
VOW = "commitment:seraphine-s-vow"

_RECORD_EDGES = ("opened_in", "advanced_in", "touched_in", "closed_in", "resolved_in")


def _scenes(cid: str, *titles: str) -> list[str]:
    return [store.scenes.create_scene(cid, t) for t in titles]


def _thread(cid: str, pid: str, title: str, *scenes: str, status: str = "open") -> None:
    for i, sid in enumerate(scenes):
        plot.set_movement(cid, pid, title, status, f"{title}, beat {i + 1}.", sid)


def _close(cid: str, pid: str, sid: str) -> None:
    plot.set_movement(cid, pid, "", "closed", "", sid)


def _oath(cid: str, mid: str, title: str, *scenes: str, due: str | None = None) -> None:
    for i, sid in enumerate(scenes):
        commitments.set_movement(cid, mid, title, "promise", "open", due,
                                 f"{title}, beat {i + 1}.", sid)


def _from(g: dict, ref: str, kind: str) -> set[str]:
    return {e["to"] for e in _edges(g, kind) if e["from"] == ref}


def _records(g: dict, kind: str) -> list[dict]:
    return [n for n in g["nodes"] if n["kind"] == kind]


@pytest.mark.parametrize("closing", ["closed", "Closed"])
def test_movement_edges_opened_advanced_closed(monkeypatch, tmp_path, closing):
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2, s3 = _scenes(cid, "Saltmarch harbour", "Mara's map", "Winifred's chart")
    _thread(cid, "mara-s-map", "Mara's map", s1, s1, s2, s3)
    _close(cid, "mara-s-map", s3)
    if closing != "closed":
        data = plot.read(cid)
        data["mara-s-map"]["status"] = closing
        plot._path(cid).write_text(json.dumps(data), encoding="utf-8")
    _thread(cid, "winifred-s-chart", "Winifred's chart", s2)

    g = graph.build(cid)
    assert _from(g, MAP, "opened_in") == {f"scene:{s1}"}
    assert _from(g, MAP, "advanced_in") == {f"scene:{s2}", f"scene:{s3}"}
    assert _from(g, MAP, "closed_in") == {f"scene:{s3}"}
    assert _from(g, CHART, "opened_in") == {f"scene:{s2}"}
    assert _from(g, CHART, "closed_in") == set()
    node = _by_id(g)[MAP]
    assert (node["kind"], node["label"], node["status"], node["live"]) == (
        "thread", "Mara's map", closing, False)
    assert node["merged_into"] is None and node["aliases"] == []
    assert node["findings"] == []
    assert _by_id(g)[CHART]["live"] is True
    edge = _edges(g, "opened_in")[0]
    assert edge["source"] == "structural"
    assert edge["id"] == graph.edge_id("opened_in", edge["from"], edge["to"])
    assert g["omitted"] == []


def test_commitment_movements_are_touched_and_resolved(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2 = _scenes(cid, "Saltmarch harbour", "Mara's map")
    _oath(cid, "mara-s-oath", "Mara's oath", s1, s1, s2)
    commitments.set_movement(cid, "mara-s-oath", "", "promise", "fulfilled", None, "", s2)

    g = graph.build(cid)
    assert _from(g, OATH, "opened_in") == {f"scene:{s1}"}
    assert _from(g, OATH, "touched_in") == {f"scene:{s2}"}
    assert _from(g, OATH, "resolved_in") == {f"scene:{s2}"}
    assert _from(g, OATH, "advanced_in") == _from(g, OATH, "closed_in") == set()
    node = _by_id(g)[OATH]
    assert (node["kind"], node["status"], node["live"], node["commitment_kind"]) == (
        "commitment", "fulfilled", False, "promise")
    assert (node["due"], node["native"], node["fixed"], node["in_days"]) == ("", "", None, None)


def test_a_first_beat_in_a_deleted_scene_opens_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2 = _scenes(cid, "Saltmarch harbour", "Mara's map")
    _thread(cid, "mara-s-map", "Mara's map", s1, s2)
    store.scenes.delete_scene(cid, s1)

    g = graph.build(cid)
    assert _from(g, MAP, "opened_in") == set()
    assert _from(g, MAP, "advanced_in") == {f"scene:{s2}"}
    assert not [e for e in g["edges"] if f"scene:{s1}" in (e["from"], e["to"])]


def test_opened_in_follows_play_order_not_save_order(monkeypatch, tmp_path):
    # Two pending reviews can be saved in either order, so a record's stored
    # beats need not be in play order; a merge must not move the opening.
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2, s3 = _scenes(cid, "Saltmarch harbour", "Mara's map", "Winifred's chart")
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Mara's map, later.", s2)
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Mara's map, earlier.", s1)

    g = graph.build(cid)
    assert _from(g, MAP, "opened_in") == {f"scene:{s1}"}
    assert _from(g, MAP, "advanced_in") == {f"scene:{s2}"}

    _thread(cid, "winifred-s-chart", "Winifred's chart", s3)
    review.create_alias(cid, CHART, MAP)
    g = graph.build(cid)
    assert _from(g, MAP, "opened_in") == {f"scene:{s1}"}
    assert _from(g, MAP, "advanced_in") == {f"scene:{s2}", f"scene:{s3}"}


def test_a_play_order_first_beat_in_a_deleted_scene_opens_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2 = _scenes(cid, "Saltmarch harbour", "Mara's map")
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Mara's map, later.", s2)
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Mara's map, earlier.", s1)
    store.scenes.delete_scene(cid, s1)

    g = graph.build(cid)
    assert _from(g, MAP, "opened_in") == set()
    assert _from(g, MAP, "advanced_in") == {f"scene:{s2}"}


def test_an_alias_source_is_a_merged_node_with_a_merged_into_edge(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2, s3 = _scenes(cid, "Saltmarch harbour", "Mara's map", "Winifred's chart")
    _thread(cid, "mara-s-map", "Mara's map", s1, s2)
    _thread(cid, "winifred-s-chart", "Winifred's chart", s3)
    review.create_alias(cid, CHART, MAP)

    g = graph.build(cid)
    nodes = _by_id(g)
    merged = nodes[CHART]
    assert merged["merged_into"] == MAP
    assert merged["focusable"] is False and merged["pressure"] is None
    assert (merged["label"], merged["status"], merged["aliases"], merged["latest_beat"]) == (
        "Winifred's chart", "open", [], "")
    [edge] = _edges(g, "merged_into")
    assert (edge["from"], edge["to"], edge["source"]) == (CHART, MAP, "alias")
    assert edge["id"] == graph.edge_id("merged_into", CHART, MAP)
    assert nodes[MAP]["aliases"] == [{"ref": CHART, "title": "Winifred's chart",
                                      "status": "open"}]
    assert _from(g, MAP, "advanced_in") == {f"scene:{s2}", f"scene:{s3}"}
    assert not [e for e in g["edges"] if e["kind"] in _RECORD_EDGES and e["from"] == CHART]


def test_involves_edges_come_from_involvement_of(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara, mara_v = overlay.create_character(cid, "Mara")
    winifred, winifred_v = overlay.create_character(cid, "Winifred")
    s1, s2, _s3, s4 = _scenes(cid, "Saltmarch harbour", "Mara's map", "Winifred's chart",
                              "The coronation")
    _seat(cid, s2, "characters", mara, mara_v)
    _seat(cid, s4, "characters", winifred, winifred_v)
    _thread(cid, "mara-s-map", "Mara's map", s1, s2)

    g = graph.build(cid)
    assert _from(g, MAP, "involves") == {f"characters:{mara}"}
    refs = [n["id"] for n in g["nodes"] if n["kind"] in ("thread", "commitment")]
    expected = {(ref, actor) for ref, row in involvement.of(cid, refs).items()
                for actor in row["actors"]}
    assert _pairs(g, "involves") == expected
    assert _by_id(g)[f"characters:{mara}"]["label"] == "Mara"


def test_focusable_is_exactly_the_chooser_driver_set(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1, s2 = _scenes(cid, "Saltmarch harbour", "Mara's map")
    _thread(cid, "mara-s-map", "Mara's map", s1)
    _thread(cid, "winifred-s-chart", "Winifred's chart", s1)
    _thread(cid, "saltmarch-eve", "Saltmarch Eve", s1)
    _close(cid, "saltmarch-eve", s2)
    review.create_alias(cid, CHART, MAP)
    _oath(cid, "mara-s-oath", "Mara's oath", s1)
    _oath(cid, "seraphine-s-vow", "Seraphine's vow", s1)
    commitments.set_movement(cid, "seraphine-s-vow", "", "promise", "fulfilled", None, "", s2)

    g = graph.build(cid)
    focusable = {n["id"] for n in g["nodes"]
                 if n["kind"] in ("thread", "commitment") and n["focusable"]}
    chooser = {d["ref"] for d in drivers.snapshot(cid)["drivers"]
               if d["kind"] in ("thread", "commitment")}
    assert focusable == chooser == {MAP, OATH}
    assert {n["id"] for n in g["nodes"] if n["kind"] in ("thread", "commitment")} == {
        MAP, CHART, "thread:saltmarch-eve", OATH, VOW}


def test_record_pressure_is_the_driver_reading(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7)
    (s1,) = _scenes(cid, "Saltmarch harbour")
    _oath(cid, "mara-s-oath", "Mara's oath", s1, due="2026-05-14")
    _thread(cid, "mara-s-map", "Mara's map", s1)
    _close(cid, "mara-s-map", s1)

    g = graph.build(cid)
    oath = _by_id(g)[OATH]
    friendly = calendars.friendly(_provider(cid), "2026-05-14")
    assert oath["pressure"] == {"state": "due_soon", "in_days": 4, "friendly": friendly}
    assert oath["due"] == "2026-05-14"
    assert oath["fixed"] == F(cid, "2026-05-14") and oath["in_days"] == 4
    assert (oath["native"], oath["friendly"]) == ("2026-05-14", friendly)
    assert oath["focusable"] is True
    assert _by_id(g)[MAP]["pressure"] is None


def test_a_garbled_plot_costs_only_threads(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (s1,) = _scenes(cid, "Saltmarch harbour")
    _thread(cid, "mara-s-map", "Mara's map", s1)
    _oath(cid, "mara-s-oath", "Mara's oath", s1)
    plot._path(cid).write_text("{ no", encoding="utf-8")

    g = graph.build(cid)
    assert _records(g, "thread") == []
    assert [n["id"] for n in _records(g, "commitment")] == [OATH]
    assert _from(g, OATH, "opened_in") == {f"scene:{s1}"}
    assert "plot" in g["omitted"]
    assert [n["id"] for n in _scene_nodes(g)] == [f"scene:{s1}"]


# ---- dated nodes and ideas --------------------------------------------------


CORONATION = "event:the-coronation"


def _hand_event(cid: str, eid: str, name: str, date: str) -> None:
    """An unfired event written straight into events.json, as a hand edit
    would: `events.create` normalizes a date and would refuse "midsummer"."""
    p = campaigns.campaign_root(cid) / "events.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data[eid] = {"name": name, "date": date, "note": "", "fired": None}
    p.write_text(json.dumps(data), encoding="utf-8")


def _event_campaign(monkeypatch, tmp_path, **kw) -> str:
    """The clock starts at 05-01 (a blank clock's first advance fires nothing),
    an event on 05-05 fires on the advance to 05-10, then the coronation is
    scheduled for 05-13, one unfired event is dated behind the clock (passed)
    and one is free text (undated)."""
    cid = _campaign(monkeypatch, tmp_path, now="2026-05-01", **kw)
    fired = events.create(cid, "Saltmarch Eve", "2026-05-05")
    clock.advance(cid, to="2026-05-10")
    assert events.get(cid, fired)["fired"] is not None   # the control: it fired
    assert events.create(cid, "The coronation", "2026-05-13") == "the-coronation"
    _hand_event(cid, "mara-s-oath", "Mara's oath", "2026-05-02")
    _hand_event(cid, "winifred-s-chart", "Winifred's chart", "midsummer")
    return cid


def _of_kind(g: dict, *kinds: str) -> list[dict]:
    return [n for n in g["nodes"] if n["kind"] in kinds]


def test_events_are_all_listed_with_their_status(monkeypatch, tmp_path):
    cid = _event_campaign(monkeypatch, tmp_path)

    g = graph.build(cid)
    statuses = {n["id"]: n["status"] for n in _of_kind(g, "event")}
    assert statuses == {"event:saltmarch-eve": "fired", CORONATION: "scheduled",
                        "event:mara-s-oath": "passed", "event:winifred-s-chart": "undated"}
    # The fired event is a node though it is no pressure item.
    assert "event:saltmarch-eve" not in {i["ref"] for i in pressure.build(cid)["items"]}
    nodes = _by_id(g)
    coronation = nodes[CORONATION]
    assert (coronation["label"], coronation["native"], coronation["findings"]) == (
        "The coronation", "2026-05-13", [])
    assert coronation["friendly"] == calendars.friendly(_provider(cid), "2026-05-13")
    assert coronation["pressure"]["in_days"] == 3
    assert nodes["event:saltmarch-eve"]["pressure"] is None
    assert g["omitted"] == []


def test_dated_nodes_carry_fixed_and_in_days_undated_null(monkeypatch, tmp_path):
    cid = _event_campaign(monkeypatch, tmp_path)
    dated = scene_ideas.add(cid, "Mara's map", "Mara reads the map.", date="2026-05-12")
    undated = scene_ideas.add(cid, "Winifred's chart", "Winifred redraws the chart.")

    g = graph.build(cid)
    nodes = _by_id(g)
    coronation = nodes[CORONATION]
    assert coronation["fixed"] == F(cid, "2026-05-13") and coronation["in_days"] == 3
    idea = nodes[f"idea:{dated}"]
    assert (idea["native"], idea["fixed"], idea["in_days"]) == (
        "2026-05-12", F(cid, "2026-05-12"), 2)
    for ref in (f"idea:{undated}", "event:winifred-s-chart"):
        assert (nodes[ref]["fixed"], nodes[ref]["in_days"]) == (None, None), ref
    assert nodes["event:winifred-s-chart"]["native"] == "midsummer"
    # An undated row is data: per-row softening records no part.
    assert g["omitted"] == []


def test_holidays_and_birthdays_come_from_pressure_items(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    mara = _actor(cid, "Mara", "--05-11")

    g = graph.build(cid)
    expected = {i["ref"] for i in pressure.build(cid)["items"]
                if i["kind"] in ("holiday", "birthday")}
    assert expected
    assert {n["id"] for n in _of_kind(g, "holiday", "birthday")} == expected
    [holiday] = _of_kind(g, "holiday")
    assert (holiday["label"], holiday["fixed"], holiday["in_days"]) == (
        "Saltmarch Eve", F(cid, "2026-05-12"), 2)
    assert holiday["pressure"]["in_days"] == 2 and holiday["anchorable"] is True
    [birthday] = _of_kind(g, "birthday")
    assert (birthday["actor"], birthday["precision"], birthday["age"]) == (
        f"characters:{mara}", "yearless", None)
    assert (birthday["label"], birthday["in_days"]) == ("Mara", 1)
    assert _pairs(g, "birthday_of") == {(birthday["id"], f"characters:{mara}")}
    actor = _by_id(g)[f"characters:{mara}"]
    assert (actor["kind"], actor["label"]) == ("character", "Mara")


def test_anchorable_is_exactly_the_chooser_anchor_set(monkeypatch, tmp_path):
    cid = _event_campaign(monkeypatch, tmp_path,
                          holidays=[_rule("Saltmarch Eve", "05", 12)])
    _actor(cid, "Mara", "--05-11")

    g = graph.build(cid)
    anchorable = {n["id"] for n in g["nodes"] if n.get("anchorable")}
    chooser = {a["ref"] for a in drivers.snapshot(cid)["anchors"]}
    assert anchorable == chooser
    assert CORONATION in anchorable
    assert {"holiday", "birthday"} <= {_by_id(g)[r]["kind"] for r in anchorable}
    for ref in ("event:saltmarch-eve", "event:winifred-s-chart", "event:mara-s-oath"):
        assert _by_id(g)[ref]["anchorable"] is False, ref


def test_only_active_ideas_are_nodes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (s1,) = _scenes(cid, "Saltmarch harbour")
    active = scene_ideas.add(cid, "Mara's map", "Mara reads the map.", pcless=True)
    used = scene_ideas.add(cid, "Winifred's chart", "Winifred redraws the chart.")
    dismissed = scene_ideas.add(cid, "The coronation", "Seraphine is crowned.")
    scene_ideas.mark_used(cid, used, s1)
    scene_ideas.set_status(cid, dismissed, scene_ideas.DISMISSED)

    g = graph.build(cid)
    [idea] = _of_kind(g, "idea")
    assert idea["id"] == f"idea:{active}"
    assert (idea["label"], idea["premise"], idea["source"], idea["pcless"]) == (
        "Mara's map", "Mara reads the map.", "user", True)
    assert idea["time_anchor"] is None
    assert (idea["native"], idea["fixed"], idea["in_days"]) == ("", None, None)


def test_ideas_serve_canonical_drivers_and_anchor_to_nodes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (s1,) = _scenes(cid, "Saltmarch harbour")
    _thread(cid, "mara-s-map", "Mara's map", s1)
    _thread(cid, "winifred-s-chart", "Winifred's chart", s1)
    review.create_alias(cid, CHART, MAP)
    events.create(cid, "The coronation", "2026-05-13")
    lid = scene_ideas.add(
        cid, "Saltmarch harbour", "Mara takes the map to the harbour.",
        drivers=[{"ref": CHART, "action": "advance"},
                 {"ref": "thread:gone", "action": "advance"},
                 {"ref": MAP, "action": "close_candidate"}],
        time_anchor={"ref": CORONATION, "relation": "on", "native": "2026-05-13"})
    idea = f"idea:{lid}"

    g = graph.build(cid)
    serves = [e for e in _edges(g, "serves") if e["from"] == idea]
    # Canonicalized through the live canon, deduped by target, first entry wins.
    assert [(e["to"], e["relation"]) for e in serves] == [(MAP, "advance")]
    assert serves[0]["id"] == graph.edge_id("serves", idea, MAP)
    assert not [e for e in g["edges"] if "thread:gone" in (e["from"], e["to"])]
    [anchored] = _edges(g, "anchored_to")
    assert (anchored["from"], anchored["to"], anchored["relation"]) == (
        idea, CORONATION, "on")


def test_an_anchor_beyond_the_horizon_keeps_its_record_but_draws_no_edge(
        monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara = _actor(cid, "Mara", "1990-07-01")
    anchor = {"ref": f"birthday:characters:{mara}:{F(cid, '2026-07-01')}",
              "relation": "on", "native": "2026-07-01"}
    lid = scene_ideas.add(cid, "Mara's map", "Mara reads the map.", time_anchor=anchor)

    g = graph.build(cid)
    assert _edges(g, "anchored_to") == []
    assert _of_kind(g, "birthday") == []
    assert _by_id(g)[f"idea:{lid}"]["time_anchor"] == anchor


def test_a_garbled_ideas_file_costs_only_ideas(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (s1,) = _scenes(cid, "Saltmarch harbour")
    scene_ideas._path(cid).write_text("[1]", encoding="utf-8")

    g = graph.build(cid)
    assert _of_kind(g, "idea") == []
    assert g["omitted"] == ["scene_ideas"]
    assert [n["id"] for n in _scene_nodes(g)] == [f"scene:{s1}"]


def test_a_raising_plugin_leaves_events_and_ideas_undated(monkeypatch, tmp_path):
    cid = _event_campaign(monkeypatch, tmp_path,
                          holidays=[_rule("Saltmarch Eve", "05", 12)])
    lid = scene_ideas.add(cid, "Mara's map", "Mara reads the map.", date="2026-05-12")
    _plugin(tmp_path, "broken_test", _BROKEN_PROVIDER_SRC)
    _primary(cid, "broken-test-calendar")

    g = graph.build(cid)
    assert "calendar" in g["omitted"]
    assert _of_kind(g, "holiday", "birthday") == []
    for ref in (CORONATION, f"idea:{lid}"):
        assert (_by_id(g)[ref]["fixed"], _by_id(g)[ref]["in_days"]) == (None, None), ref
    assert not [n for n in g["nodes"] if n.get("anchorable")]


def test_a_raising_event_list_falls_back_to_undated_events(monkeypatch, tmp_path):
    cid = _event_campaign(monkeypatch, tmp_path)
    real = events.list_events

    def list_events(cid, provider=None, now_fixed=None):
        if provider is not None:
            raise RuntimeError("this calendar plugin cannot date an event")
        return real(cid, provider, now_fixed)

    monkeypatch.setattr(events, "list_events", list_events)
    g = graph.build(cid)
    assert g["omitted"] == ["calendar"]
    statuses = {n["id"]: n["status"] for n in _of_kind(g, "event")}
    # Nothing is dated by guesswork: only the fire stamp survives.
    assert statuses == {"event:saltmarch-eve": "fired", CORONATION: "undated",
                        "event:mara-s-oath": "undated", "event:winifred-s-chart": "undated"}
    assert _by_id(g)[CORONATION]["fixed"] is None


# ---- reviewed links and review candidates -----------------------------------


DUP = "possible_duplicate"
REL = "possible_relation"
CLOSURE = "possible_thread_closure"
EVE = "thread:saltmarch-eve"


def _every_edge_names_two_nodes(g: dict) -> None:
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["from"] in ids and e["to"] in ids, e


def _sourced(g: dict, source: str) -> list[dict]:
    return [e for e in g["edges"] if e["source"] == source]


def _finding(cid: str, kind: str, refs: list[str]) -> tuple[str, dict]:
    """A cached finding whose stored fingerprint is the current one (`live`)."""
    fp = pending.fingerprint(pending.Current.load(cid), kind, refs)
    assert fp is not None
    return canon.candidate_id(kind, refs), {
        "kind": kind, "refs": list(refs), "fingerprint": fp, "signals": {},
        "proposal": None, "created": ""}


def _cache(cid: str, *found: tuple[str, dict]) -> None:
    data = candidates.empty()
    data["records"] = dict(found)
    candidates.write(cid, data)


def _review_campaign(monkeypatch, tmp_path) -> str:
    cid = _campaign(monkeypatch, tmp_path)
    (s1,) = _scenes(cid, "Saltmarch harbour")
    _thread(cid, "mara-s-map", "Mara's map", s1)
    _thread(cid, "winifred-s-chart", "Winifred's chart", s1)
    _thread(cid, "saltmarch-eve", "Saltmarch Eve", s1)
    _oath(cid, "mara-s-oath", "Mara's oath", s1)
    events.create(cid, "The coronation", "2026-05-13")
    return cid


def test_reviewed_links_are_link_edges(monkeypatch, tmp_path):
    cid = _review_campaign(monkeypatch, tmp_path)
    lid = review.create_link(cid, MAP, OATH, "pays_off")["link"]["id"]

    g = graph.build(cid)
    [edge] = _edges(g, "link")
    assert edge == {"id": lid, "kind": "link", "from": MAP, "to": OATH,
                    "source": "reviewed", "relation": "pays_off", "candidate_id": None}
    assert g["omitted"] == []


def test_a_dangling_link_draws_nothing(monkeypatch, tmp_path):
    cid = _review_campaign(monkeypatch, tmp_path)
    lid = canon.link_id("before", OATH, "event:gone")
    doc.put_link(cid, lid, {"a": OATH, "b": "event:gone", "relation": "before",
                            "created": "", "scene": "", "note": ""})

    g = graph.build(cid)
    assert _edges(g, "link") == []
    assert lid in {b["id"] for b in effective.diagnostics(cid)["broken_links"]}
    _every_edge_names_two_nodes(g)


@pytest.mark.parametrize("a, relation, b, ledger", [
    (MAP, "pays_off", OATH, "plot.json"),
    (OATH, "before", CORONATION, "events.json"),
])
def test_a_link_to_an_unreadable_ledger_draws_nothing(monkeypatch, tmp_path, a, relation, b,
                                                       ledger):
    cid = _review_campaign(monkeypatch, tmp_path)
    lid = review.create_link(cid, a, b, relation)["link"]["id"]
    (campaigns.campaign_root(cid) / ledger).write_text("{ no", encoding="utf-8")

    # The positive control: existence unknown is not existence false, so the
    # effective view keeps the link.
    assert lid in {link["id"] for link in effective.links(cid)}
    g = graph.build(cid)
    assert _edges(g, "link") == []
    _every_edge_names_two_nodes(g)


def test_visible_pair_findings_are_candidate_edges(monkeypatch, tmp_path):
    cid = _review_campaign(monkeypatch, tmp_path)
    dup_id, dup = _finding(cid, DUP, [MAP, CHART])
    closure_id, closure = _finding(cid, CLOSURE, [MAP])
    rel_id, rel = _finding(cid, REL, [OATH, CORONATION])
    _cache(cid, (dup_id, dup), (closure_id, closure), (rel_id, rel))

    g = graph.build(cid)
    candidate_edges = {e["id"]: e for e in _sourced(g, "candidate")}
    assert set(candidate_edges) == {dup_id, rel_id}
    assert candidate_edges[dup_id] == {
        "id": dup_id, "kind": DUP, "from": MAP, "to": CHART, "source": "candidate",
        "relation": None, "candidate_id": dup_id}
    rel_edge = candidate_edges[rel_id]
    assert (rel_edge["kind"], rel_edge["from"], rel_edge["to"], rel_edge["candidate_id"]) == (
        REL, OATH, CORONATION, rel_id)
    nodes = _by_id(g)
    assert nodes[MAP]["findings"] == sorted([
        {"id": dup_id, "kind": DUP, "other": CHART},
        {"id": closure_id, "kind": CLOSURE, "other": None},
    ], key=lambda f: f["id"])
    assert nodes[CHART]["findings"] == [{"id": dup_id, "kind": DUP, "other": MAP}]
    # The temporal relation lands on the commitment and on the event.
    assert nodes[OATH]["findings"] == [{"id": rel_id, "kind": REL, "other": CORONATION}]
    assert nodes[CORONATION]["findings"] == [{"id": rel_id, "kind": REL, "other": OATH}]
    assert g["omitted"] == []
    _every_edge_names_two_nodes(g)

    # A suppressed duplicate is the reader's own "no": no edge, no finding.
    doc.put_suppression(cid, dup["fingerprint"], {"kind": DUP, "refs": [MAP, CHART],
                                                  "decision": "dismiss", "created": ""})
    g = graph.build(cid)
    assert {e["id"] for e in _sourced(g, "candidate")} == {rel_id}
    assert _by_id(g)[MAP]["findings"] == [{"id": closure_id, "kind": CLOSURE, "other": None}]
    assert _by_id(g)[CHART]["findings"] == []


def test_a_pair_finding_on_a_merged_source_is_dropped(monkeypatch, tmp_path):
    cid = _review_campaign(monkeypatch, tmp_path)
    fid, record = _finding(cid, DUP, [CHART, EVE])
    _cache(cid, (fid, record))
    review.create_alias(cid, CHART, MAP)

    # D's verdict for an alias source is `gone`.
    assert [(i, v) for i, _, v in pending.findings(cid)] == [(fid, "gone")]
    g = graph.build(cid)
    assert _sourced(g, "candidate") == []
    nodes = _by_id(g)
    for ref in (MAP, CHART, EVE):
        assert nodes[ref]["findings"] == [], ref


def test_a_lifecycle_finding_on_the_canonical_lands_on_it(monkeypatch, tmp_path):
    cid = _review_campaign(monkeypatch, tmp_path)
    review.create_alias(cid, CHART, MAP)
    fid, record = _finding(cid, CLOSURE, [MAP])
    _cache(cid, (fid, record))

    assert [(i, v) for i, _, v in pending.findings(cid)] == [(fid, "live")]
    g = graph.build(cid)
    nodes = _by_id(g)
    assert nodes[MAP]["findings"] == [{"id": fid, "kind": CLOSURE, "other": None}]
    assert nodes[CHART]["findings"] == []
    assert not [e for e in _sourced(g, "candidate") if CHART in (e["from"], e["to"])]


def test_a_malformed_cache_costs_only_candidates(monkeypatch, tmp_path):
    cid = _review_campaign(monkeypatch, tmp_path)
    lid = review.create_link(cid, MAP, OATH, "pays_off")["link"]["id"]
    candidates._path(cid).write_text("{ no", encoding="utf-8")

    g = graph.build(cid)
    assert _sourced(g, "candidate") == []
    assert "candidates" in g["omitted"]
    assert [e["id"] for e in _edges(g, "link")] == [lid]
