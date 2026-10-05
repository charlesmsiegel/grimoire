"""`store/continuity/effective.py` -- the live resolver and effective projections.

Two promises carry the weight here (spec §7.1):

- **The identity law.** A campaign with no live aliases projects exactly what
  `plot.open_threads` / `commitments.open_commitments` project, byte for byte
  and in order, plus ``aliases: []``. Every current-state reader moves onto
  these projections, so any "improvement" to an unaliased record would change
  prompts, the ledger and the digest for every existing campaign.
- **One meaning of canonical.** `live_canon` follows an alias only to a record
  that is really there, so a dangling or wrong-type alias leaves its source
  standing as its own record instead of redirecting anything into nothing.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import doc, effective

S1, S2, S3 = "001--saltmarch", "002--realm", "003--winifred"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _thread(cid, pid, title, status, beats=(), last=None):
    for scene, text in beats:
        store.plot.set_movement(cid, pid, title, status, text, scene)
    if last is not None or not beats:
        store.plot.set_movement(cid, pid, title, status, "", last or "")


def _commitment(cid, mid, title, status="open", beats=(), due=None, last=None):
    for scene, text in beats:
        store.commitments.set_movement(cid, mid, title, "promise", status, due, text, scene)
    if last is not None or not beats:
        store.commitments.set_movement(cid, mid, title, "promise", status, due, "", last or "")


def _alias(cid, src, to):
    doc.put_alias(cid, src, {"to": to, "created": "", "source": "manual", "note": ""})


def _link(cid, lid, a, b, relation):
    doc.put_link(cid, lid, {"a": a, "b": b, "relation": relation,
                            "created": "", "scene": "", "note": ""})


def _plot_raw(cid, data):
    (_root(cid) / "plot.json").write_text(json.dumps(data), encoding="utf-8")


# ----------------------------------------------------------- the identity law


def test_identity_law_threads(cid):
    # Beats appended out of scene order, and a status-only move newer than
    # every beat -- the two reasons a "tidier" projection would differ.
    _thread(cid, "maras-map", "Mara's map", "advanced",
            beats=[(S2, "Mara found a page."), (S1, "The map was stolen.")], last=S3)
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S1, "Winifred lost it.")])
    _thread(cid, "realm-feud", "The Realm feud", "closed", beats=[(S1, "It ended.")])
    data = store.plot.read(cid)
    data["shouting"] = dict(data["realm-feud"], status="Closed")
    data["garbage"] = "not a record"
    _plot_raw(cid, data)
    for include in (False, True):
        assert effective.threads(cid, include_closed=include) == [
            dict(r, aliases=[]) for r in store.plot.open_threads(cid, include_closed=include)]


def test_identity_law_commitments(cid):
    _commitment(cid, "mara-promise", "Mara's promise",
                beats=[(S2, "She swore it."), (S1, "She hinted.")], due="by midwinter", last=S3)
    _commitment(cid, "old-debt", "The old debt", status="fulfilled", beats=[(S1, "Paid.")])
    for include in (False, True):
        assert effective.commitments(cid, include_resolved=include) == [
            dict(r, aliases=[])
            for r in store.commitments.open_commitments(cid, include_resolved=include)]


# ------------------------------------------------------------------ merging


def test_alias_hides_source_and_merges_beats(cid):
    _thread(cid, "maras-map", "Mara's map", "open",
            beats=[(S1, "The map was stolen."), (S3, "Mara traced the thief.")])
    _thread(cid, "winifreds-chart", "Winifred's chart", "advanced", beats=[(S2, "Winifred lost it.")])
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    rows = effective.threads(cid)
    assert [r["id"] for r in rows] == ["winifreds-chart"]
    row = rows[0]
    assert row["aliases"] == [{"ref": "thread:maras-map", "title": "Mara's map",
                               "status": "open"}]
    assert row["latest_beat"] == "Mara traced the thief."
    assert row["last_scene"] == S3
    assert row["status"] == "advanced"
    rec = effective.records(cid, "thread")["thread:winifreds-chart"]
    assert [b["scene"] for b in rec["beats"]] == [S1, S2, S3]
    assert rec["members"] == ["winifreds-chart", "maras-map"]


def test_duplicate_beat_dropped_across_records_only(cid):
    _thread(cid, "maras-map", "Mara's map", "open",
            beats=[(S1, "The map was stolen.")])
    _thread(cid, "winifreds-chart", "Winifred's chart", "open",
            beats=[(S1, "The map was stolen."), (S2, "Again."), (S2, "Again.")])
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    beats = effective.records(cid, "thread")["thread:winifreds-chart"]["beats"]
    assert [(b["scene"], b["text"]) for b in beats] == [
        (S1, "The map was stolen."), (S2, "Again."), (S2, "Again.")]


def test_uncomparable_beat_keeps_position_after_predecessor(cid):
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S1, "One."), (S3, "Three.")])
    data = store.plot.read(cid)
    data["maras-map"] = {"title": "Mara's map", "status": "open", "last_scene": S2,
                           "beats": [{"scene": S2, "text": "Two."},
                                     {"scene": "", "text": "Two and a half."}]}
    _plot_raw(cid, data)
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    beats = effective.records(cid, "thread")["thread:winifreds-chart"]["beats"]
    assert [b["text"] for b in beats] == ["One.", "Two.", "Two and a half.", "Three."]


def test_hand_edited_beat_shapes_tolerated(cid):
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S1, "One.")])
    data = store.plot.read(cid)
    data["maras-map"] = {"title": "Mara's map", "status": "open", "last_scene": S2,
                           "beats": ["stray", {"scene": S2, "text": ["x"]},
                                     {"scene": S2, "text": "Two."}]}
    data["seraphines-map"] = {"title": "Seraphine's map", "status": "open", "beats": {"x": 1}}
    _plot_raw(cid, data)
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    _alias(cid, "thread:seraphines-map", "thread:winifreds-chart")
    rec = effective.records(cid, "thread")["thread:winifreds-chart"]
    assert [b.get("text") for b in rec["beats"]] == ["One.", ["x"], "Two."]
    assert effective.threads(cid)[0]["latest_beat"] == "Two."


def test_effective_last_scene_includes_status_only_moves(cid):
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S1, "One.")])
    _thread(cid, "maras-map", "Mara's map", "open", beats=[(S1, "Also one.")], last=S3)
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert effective.threads(cid)[0]["last_scene"] == S3


def test_canonical_status_is_authoritative_and_filters(cid):
    _thread(cid, "winifreds-chart", "Winifred's chart", "closed", beats=[(S1, "Ended.")])
    _thread(cid, "maras-map", "Mara's map", "open", beats=[(S2, "Still going.")])
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert effective.threads(cid) == []
    closed = effective.threads(cid, include_closed=True)
    assert [(r["id"], r["status"]) for r in closed] == [("winifreds-chart", "closed")]


def test_transitive_alias_merges_into_final_target(cid):
    for pid in ("a", "b", "c"):
        _thread(cid, pid, pid.upper(), "open", beats=[(S1, f"{pid} beat.")])
    _alias(cid, "thread:a", "thread:b")
    _alias(cid, "thread:b", "thread:c")
    assert effective.live_canon(cid) == {"thread:a": "thread:c", "thread:b": "thread:c"}
    rows = effective.threads(cid)
    assert [r["id"] for r in rows] == ["c"]
    assert [a["ref"] for a in rows[0]["aliases"]] == ["thread:a", "thread:b"]


def test_chain_with_missing_tail_stops_at_last_valid_hop(cid):
    _thread(cid, "a", "A", "open", beats=[(S1, "a.")])
    _thread(cid, "b", "B", "open", beats=[(S2, "b.")])
    _alias(cid, "thread:a", "thread:b")
    _alias(cid, "thread:b", "thread:gone")
    assert effective.live_canon(cid) == {"thread:a": "thread:b"}
    assert [r["id"] for r in effective.threads(cid)] == ["b"]
    dangling = effective.diagnostics(cid)["dangling_aliases"]
    assert dangling == [{"ref": "thread:b", "to": "thread:gone", "reason": "missing_target"}]


def test_missing_target_degrades_source_to_own_record(cid):
    _thread(cid, "maras-map", "Mara's map", "open", beats=[(S1, "One.")])
    _alias(cid, "thread:maras-map", "thread:gone")
    assert effective.live_canon(cid) == {}
    assert [r["id"] for r in effective.threads(cid)] == ["maras-map"]
    assert effective.diagnostics(cid)["dangling_aliases"] == [
        {"ref": "thread:maras-map", "to": "thread:gone", "reason": "missing_target"}]


def test_cycle_degrades_both(cid):
    _thread(cid, "a", "A", "open", beats=[(S1, "a.")])
    _thread(cid, "b", "B", "open", beats=[(S2, "b.")])
    _alias(cid, "thread:a", "thread:b")
    _alias(cid, "thread:b", "thread:a")
    assert effective.live_canon(cid) == {}
    assert [r["id"] for r in effective.threads(cid)] == ["a", "b"]
    assert effective.diagnostics(cid)["dangling_aliases"] == [
        {"ref": "thread:a", "to": "thread:b", "reason": "cycle"},
        {"ref": "thread:b", "to": "thread:a", "reason": "cycle"}]


def test_wrong_type_and_non_dict_alias_records(cid):
    _thread(cid, "a", "A", "open", beats=[(S1, "a.")])
    _thread(cid, "c", "C", "open", beats=[(S1, "c.")])
    _thread(cid, "d", "D", "open", beats=[(S1, "d.")])
    _commitment(cid, "b", "B", beats=[(S1, "b.")])
    _alias(cid, "thread:a", "commitment:b")
    doc.put_alias(cid, "thread:c", "thread:d")
    assert effective.live_canon(cid) == {}
    assert [r["id"] for r in effective.threads(cid)] == ["a", "c", "d"]
    assert effective.diagnostics(cid)["dangling_aliases"] == [
        {"ref": "thread:a", "to": "commitment:b", "reason": "wrong_type"},
        {"ref": "thread:c", "to": "", "reason": "malformed_record"}]


def test_non_dict_canonical_keeps_members_visible(cid):
    _thread(cid, "maras-map", "Mara's map", "open", beats=[(S1, "One.")])
    data = store.plot.read(cid)
    data["winifreds-chart"] = "x"
    _plot_raw(cid, data)
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert effective.live_canon(cid) == {}
    assert [r["id"] for r in effective.threads(cid)] == ["maras-map"]
    assert effective.diagnostics(cid)["dangling_aliases"][0]["reason"] == "missing_target"


def test_missing_source_is_reported(cid):
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S1, "One.")])
    _alias(cid, "thread:gone", "thread:winifreds-chart")
    assert effective.live_canon(cid) == {}
    assert effective.diagnostics(cid)["dangling_aliases"] == [
        {"ref": "thread:gone", "to": "thread:winifreds-chart", "reason": "missing_source"}]


def test_empty_dict_record_exists(cid):
    _thread(cid, "maras-map", "Mara's map", "open", beats=[(S1, "One.")])
    data = store.plot.read(cid)
    data["winifreds-chart"] = {}
    _plot_raw(cid, data)
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert effective.live_canon(cid) == {"thread:maras-map": "thread:winifreds-chart"}
    assert [r["id"] for r in effective.threads(cid)] == ["winifreds-chart"]


def test_removing_alias_restores_two_records(cid):
    _thread(cid, "maras-map", "Mara's map", "open", beats=[(S1, "One.")])
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S2, "Two.")])
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert len(effective.threads(cid)) == 1
    doc.drop_alias(cid, "thread:maras-map")
    assert effective.threads(cid) == [dict(r, aliases=[]) for r in store.plot.open_threads(cid)]


def test_commitment_alias_merges(cid):
    _commitment(cid, "mara-promise", "Mara's promise", beats=[(S1, "She swore.")])
    _commitment(cid, "mara-oath", "Mara's oath", beats=[(S2, "She swore again.")],
                due="by midwinter")
    _alias(cid, "commitment:mara-promise", "commitment:mara-oath")
    rows = effective.commitments(cid)
    assert [r["id"] for r in rows] == ["mara-oath"]
    assert rows[0]["due"] == "by midwinter"
    assert rows[0]["aliases"][0]["ref"] == "commitment:mara-promise"


# -------------------------------------------------------------------- links


def test_links_canonicalize_dedupe_and_hide(cid):
    _thread(cid, "a", "A", "open", beats=[(S1, "a.")])
    _thread(cid, "b", "B", "open", beats=[(S2, "b.")])
    _commitment(cid, "x", "X", beats=[(S1, "x.")])
    _link(cid, "l1", "thread:a", "commitment:x", "pays_off")
    _link(cid, "l2", "thread:b", "commitment:x", "pays_off")
    _link(cid, "l3", "thread:a", "thread:b", "continues")
    _link(cid, "l4", "thread:a", "event:nope", "before")
    _link(cid, "l5", "commitment:x", "thread:a", "pays_off")
    doc.put_link(cid, "l6", {"a": 3})
    _alias(cid, "thread:a", "thread:b")
    links = effective.links(cid)
    assert [(lk["id"], lk["a"], lk["a_raw"]) for lk in links] == [
        ("l1", "thread:b", "thread:a")]
    diag = effective.diagnostics(cid)
    assert diag["hidden_links"] == [{"id": "l2", "reason": "duplicate"}]
    assert diag["broken_links"] == [
        {"id": "l3", "reason": "self_collapsing"},
        {"id": "l4", "reason": "missing_endpoint"},
        {"id": "l5", "reason": "invalid_relation"},
        {"id": "l6", "reason": "malformed_record"}]


def test_link_through_dangling_alias_stays_effective(cid):
    _thread(cid, "a", "A", "open", beats=[(S1, "a.")])
    _commitment(cid, "x", "X", beats=[(S1, "x.")])
    _alias(cid, "thread:a", "thread:gone")
    _link(cid, "l1", "thread:a", "commitment:x", "pays_off")
    assert [(lk["id"], lk["a"]) for lk in effective.links(cid)] == [("l1", "thread:a")]


def test_link_id_and_endpoints_unchanged_after_later_alias(cid):
    _thread(cid, "a", "A", "open", beats=[(S1, "a.")])
    _thread(cid, "b", "B", "open", beats=[(S2, "b.")])
    _commitment(cid, "x", "X", beats=[(S1, "x.")])
    _link(cid, "l1", "thread:a", "commitment:x", "pays_off")
    _alias(cid, "thread:a", "thread:b")
    assert doc.get_link(cid, "l1")["a"] == "thread:a"
    (link,) = effective.links(cid)
    assert (link["id"], link["a"], link["a_raw"]) == ("l1", "thread:b", "thread:a")


# ------------------------------------------------------------- garbled files


def test_garbled_plot_raises_like_open_threads(cid):
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    with pytest.raises(Exception) as physical:
        store.plot.open_threads(cid)
    with pytest.raises(type(physical.value)):
        effective.threads(cid)


def test_garbled_plot_existence_unknown(cid):
    _commitment(cid, "x", "X", beats=[(S1, "x.")])
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    _alias(cid, "thread:a", "thread:b")
    _link(cid, "l1", "thread:a", "commitment:x", "pays_off")
    diag = effective.diagnostics(cid)
    assert diag["unreadable"] == ["plot"]
    assert diag["dangling_aliases"] == []
    assert diag["broken_links"] == []
    assert [lk["id"] for lk in effective.links(cid)] == ["l1"]


def test_leading_unscened_alias_beat_follows_its_stored_predecessor(cid):
    _thread(cid, "winifreds-chart", "Winifred's chart", "open", beats=[(S1, "c1"), (S3, "c3")])
    data = store.plot.read(cid)
    data["maras-map"] = {"title": "Mara's map", "status": "open", "last_scene": S2,
                           "beats": [{"scene": "", "text": "m-unscened"},
                                     {"scene": S2, "text": "m2"}]}
    data["seraphines-map"] = {"title": "Seraphine's map", "status": "open", "last_scene": "",
                        "beats": [{"scene": "", "text": "t-unscened"}]}
    _plot_raw(cid, data)
    _alias(cid, "thread:maras-map", "thread:winifreds-chart")
    _alias(cid, "thread:seraphines-map", "thread:winifreds-chart")
    beats = effective.records(cid, "thread")["thread:winifreds-chart"]["beats"]
    # In the concatenation the merge sorts, m-unscened follows c3 (the
    # canonical's last beat) and t-unscened follows m2 (maras-map's last).
    assert [b["text"] for b in beats] == ["c1", "m2", "t-unscened", "c3", "m-unscened"]
    assert effective.threads(cid)[0]["latest_beat"] == "m-unscened"


def test_garbled_events_is_unknown_not_missing(cid):
    _commitment(cid, "x", "X", beats=[(S1, "x.")])
    eid = store.events.create(cid, "The coronation", "2026-05-09", "")
    _link(cid, "l1", "commitment:x", f"event:{eid}", "before")
    store.events._path(cid).write_text("{ no", encoding="utf-8")
    diag = effective.diagnostics(cid)
    assert diag["unreadable"] == ["events"]
    assert diag["broken_links"] == []
    assert [lk["id"] for lk in effective.links(cid)] == ["l1"]
