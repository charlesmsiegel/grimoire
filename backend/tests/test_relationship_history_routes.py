"""GET /campaigns/{cid}/relationships/history (#63).

The ledger's relationships section answers where two people stand *now*; this
route answers what moved them, which `relationships.json` overwrites and cannot
say. Three contracts are checked here: the pair filter matches unordered (a
directed feeling comes back in both directions, with the bond), names and scene
labels are resolved server-side, and a hand-edited file costs the row's fields
rather than the request.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


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


def _char(cid, name):
    root = store.campaigns.campaign_root(cid)
    return store.characters.create_character(root, name, "main",
                                             store.characters.blank_card(name))[0]


def _feeling(cid, frm, to, trust, affection, tension, note=""):
    cur = store.relationships.get_feeling(cid, frm, to)
    payload = {"from": frm, "to": to, "trust": trust, "affection": affection,
               "tension": tension, "note": note}
    return {"id": f"feeling:{frm}->{to}", "kind": "relationship",
            "target": {"kind": "relationships", "id": f"{frm}->{to}"},
            "field": "feeling", "label": "a → b",
            "before": store.relationships._render_feeling(cur) if cur else "",
            "after": store.relationships._render_feeling(payload), "payload": payload}


def test_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/relationships/history").status_code == 404


def test_empty_campaign_has_an_empty_timeline(client, cid):
    assert client.get(f"/api/campaigns/{cid}/relationships/history").json() == []


def test_rows_carry_names_and_the_scene_label_newest_first(client, cid):
    mara, winifred = _char(cid, "Mara"), _char(cid, "Winifred")
    a, b = f"characters:{mara}", f"characters:{winifred}"
    sid = store.scenes.create_scene(cid, "The crypt")
    store.absorb.apply_edits(cid, [_feeling(cid, a, b, 1, 1, 4, "wary")], sid)
    store.absorb.apply_edits(cid, [_feeling(cid, a, b, 4, 3, 1, "warm")], sid)

    rows = client.get(f"/api/campaigns/{cid}/relationships/history").json()
    assert [r["after"] for r in rows] == [
        "trust 4, affection 3, tension 1 (warm)", "trust 1, affection 1, tension 4 (wary)"]
    assert rows[0]["before"] == "trust 1, affection 1, tension 4 (wary)"
    assert rows[0]["a_name"] == "Mara" and rows[0]["b_name"] == "Winifred"
    assert rows[0]["kind"] == "feeling" and rows[0]["source"] == "absorb"
    assert rows[0]["scene"] == {"id": sid, "title": "The crypt", "date": ""}
    assert rows[0]["id"] and rows[0]["ts"]


def test_pair_filter_matches_unordered_and_returns_both_directions(client, cid):
    mara, winifred, seraphine = _char(cid, "Mara"), _char(cid, "Winifred"), _char(cid, "Seraphine")
    a, b, c = f"characters:{mara}", f"characters:{winifred}", f"characters:{seraphine}"
    sid = store.scenes.create_scene(cid, "S")
    store.absorb.apply_edits(cid, [
        _feeling(cid, a, b, 1, 1, 4),
        _feeling(cid, b, a, 3, 3, 0),
        _feeling(cid, a, c, 5, 0, 0),
        {"id": "bond", "kind": "bond", "target": {"kind": "relationships", "id": "x"},
         "field": "bond", "label": "Mara & Winifred", "before": "", "after": "allies",
         "payload": {"a": a, "b": b, "type": "allies"}}], sid)

    rows = client.get(f"/api/campaigns/{cid}/relationships/history",
                      params={"a": b, "b": a}).json()
    assert [(r["kind"], r["a"], r["b"]) for r in rows] == [
        ("bond", a, b), ("feeling", b, a), ("feeling", a, b)]
    assert all(r["a_name"] and r["b_name"] for r in rows)
    # Half a pair names no pair, so it falls back to the whole timeline rather
    # than filtering on one actor — which is a different question.
    assert len(client.get(f"/api/campaigns/{cid}/relationships/history",
                          params={"a": a}).json()) == 4


def test_an_undone_delta_shows_as_its_own_row(client, cid):
    mara, winifred = _char(cid, "Mara"), _char(cid, "Winifred")
    a, b = f"characters:{mara}", f"characters:{winifred}"
    sid = store.scenes.create_scene(cid, "S")
    store.absorb.apply_edits(cid, [_feeling(cid, a, b, 1, 1, 4, "wary")], sid)
    jid = store.journal.read(cid)[-1]["id"]
    assert client.post(f"/api/campaigns/{cid}/journal/{jid}/undo").status_code == 200

    rows = client.get(f"/api/campaigns/{cid}/relationships/history").json()
    assert [(r["source"], r["before"], r["after"]) for r in rows] == [
        ("undo", "trust 1, affection 1, tension 4 (wary)", ""),
        ("absorb", "", "trust 1, affection 1, tension 4 (wary)")]


def test_a_deleted_character_costs_a_name_not_the_row(client, cid):
    mara, winifred = _char(cid, "Mara"), _char(cid, "Winifred")
    a, b = f"characters:{mara}", f"characters:{winifred}"
    sid = store.scenes.create_scene(cid, "S")
    store.absorb.apply_edits(cid, [_feeling(cid, a, b, 1, 1, 4)], sid)
    store.characters.delete_character(store.campaigns.campaign_root(cid), winifred)

    rows = client.get(f"/api/campaigns/{cid}/relationships/history").json()
    assert rows[0]["a_name"] == "Mara" and rows[0]["b_name"] == winifred


def test_a_hand_edited_row_degrades_field_by_field(client, cid):
    """relationship_history.json is a plain JSON file a user can edit or a sync
    can garble, and React refuses an object as a child — so a non-string field
    must come back as "" rather than blanking the view."""
    p = store.campaigns.campaign_root(cid) / "relationship_history.json"
    p.write_text(json.dumps({"seq": 1, "entries": [
        {"id": "rh1", "ts": "t", "kind": "feeling", "a": {"oops": 1}, "b": "characters:winifred",
         "label": ["nope"], "before": None, "after": "trust 1", "scene": 3,
         "source": "absorb"}]}), encoding="utf-8")
    rows = client.get(f"/api/campaigns/{cid}/relationships/history").json()
    assert rows[0]["a"] == "" and rows[0]["label"] == "" and rows[0]["before"] == ""
    assert rows[0]["after"] == "trust 1" and rows[0]["scene"]["id"] == ""


def test_the_listing_is_capped(client, cid, monkeypatch):
    from grimoire.routes import campaigns as campaign_routes
    monkeypatch.setattr(campaign_routes, "RELATIONSHIP_HISTORY_PAGE", 2)
    store.relationship_history.append(cid, [
        store.relationship_history.row("feeling", "characters:a", "characters:b",
                                       label="", before="", after=str(n))
        for n in range(5)])
    rows = client.get(f"/api/campaigns/{cid}/relationships/history").json()
    assert [r["after"] for r in rows] == ["4", "3"]   # newest first, capped


def test_a_malformed_chronicle_costs_the_date_not_the_request(client, cid):
    """`read_chronicle` hands back whatever `json.loads` returned, so a
    hand-edited list parses fine and then raises past the handler that only
    catches a read failure — turning a date label nobody would miss into a 500."""
    p = store.campaigns.campaign_root(cid) / "chronicle.json"
    store.relationship_history.append(cid, [
        store.relationship_history.row("feeling", "characters:mara", "pcs:seraphine",
                                       label="", before="", after="trust 1", scene="s1")])
    for garbled in ("[]", '{"s1": "not an object"}'):
        p.write_text(garbled, encoding="utf-8")
        rows = client.get(f"/api/campaigns/{cid}/relationships/history").json()
        assert rows[0]["scene"] == {"id": "s1", "title": "s1", "date": ""}
