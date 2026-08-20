"""GET /campaigns/{cid}/journal and POST .../journal/{jid}/undo (#31).

The listing is the history behind the rolling Changes panel; the undo route is
the one place a reader can take a write back. Two contracts matter here and are
both checked: `undoable` is the server's answer (the client must never re-derive
it), and a record that has moved since gets a 409 rather than a silent
overwrite.
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
    return TestClient(create_app())


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _lore(cid, body="old body"):
    store.entities.create_entity(store.campaigns.campaign_root(cid), "lore", "Pact",
                                 body=body)


def _absorb_lore_edit(cid, sid, before="old body", after="new body"):
    store.absorb.apply_edits(cid, [{
        "id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
        "label": "The Pact — lore", "field": "body",
        "before": before, "after": after, "authored": False}], sid)


def test_unknown_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/journal").status_code == 404
    assert client.post("/api/campaigns/nope/journal/j1/undo").status_code == 404


def test_empty_campaign_has_an_empty_history(client, cid):
    assert client.get(f"/api/campaigns/{cid}/journal").json() == []


def test_listing_resolves_the_record_scene_and_diff(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    [row] = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["id"] == "j1" and row["source"] == "absorb"
    assert row["ref"] == {"kind": "lore", "id": "pact"}
    assert row["name"] == "Pact" and row["label"] == "The Pact — lore"
    assert row["scene"]["id"] == sid and row["scene"]["title"] == "The blockade"
    assert row["diff"] == [{"op": "delete", "text": "old body"},
                           {"op": "insert", "text": "new body"}]
    assert row["undoable"] is True and row["why"] == "" and row["undone"] is None


def test_listing_is_newest_first(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid, "old body", "second")
    _absorb_lore_edit(cid, sid, "second", "third")
    assert [r["id"] for r in client.get(f"/api/campaigns/{cid}/journal").json()] == ["j2", "j1"]


def test_undo_puts_the_record_back_and_returns_the_reversal(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    res = client.post(f"/api/campaigns/{cid}/journal/j1/undo")
    assert res.status_code == 200
    assert res.json()["entry"]["id"] == "j2"
    assert res.json()["entry"]["source"] == "undo"
    got = client.get(f"/api/campaigns/{cid}/lore/pact").json()
    assert got["body"].strip() == "old body"


def test_undone_entries_stop_being_undoable(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    client.post(f"/api/campaigns/{cid}/journal/j1/undo")
    rows = {r["id"]: r for r in client.get(f"/api/campaigns/{cid}/journal").json()}
    assert rows["j1"]["undoable"] is False and rows["j1"]["undone"]["by"] == "j2"
    # The reversal is itself undoable: that is redo, and it needs no second
    # mechanism.
    assert rows["j2"]["undoable"] is True


def test_a_second_undo_of_the_same_entry_is_409(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    client.post(f"/api/campaigns/{cid}/journal/j1/undo")
    res = client.post(f"/api/campaigns/{cid}/journal/j1/undo")
    assert res.status_code == 409 and "already been undone" in res.json()["detail"]


def test_a_record_that_moved_since_is_409_and_writes_nothing(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    client.put(f"/api/campaigns/{cid}/lore/pact",
               json={"name": "Pact", "body": "somebody else's edit", "keys": ""})
    res = client.post(f"/api/campaigns/{cid}/journal/j1/undo")
    assert res.status_code == 409
    got = client.get(f"/api/campaigns/{cid}/lore/pact").json()
    assert got["body"].strip() == "somebody else's edit"


def test_an_unknown_entry_is_404(client, cid):
    res = client.post(f"/api/campaigns/{cid}/journal/j9/undo")
    assert res.status_code == 404


def test_a_kind_with_no_reversal_is_400_and_says_why(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    store.absorb.apply_edits(cid, [{
        "id": "new:lore", "kind": "new_lore", "target": {"kind": "lore", "id": ""},
        "label": "The Tithe — new lore", "field": "body", "before": "",
        "after": "A debt owed each spring.", "authored": False,
        "payload": {"name": "The Tithe", "keys": ""}}], sid)
    [row] = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["undoable"] is False and "deleting" in row["why"]
    res = client.post(f"/api/campaigns/{cid}/journal/{row['id']}/undo")
    assert res.status_code == 400 and "deleting" in res.json()["detail"]


def test_a_hand_edit_through_the_entity_route_is_journalled(client, cid):
    _lore(cid)
    client.put(f"/api/campaigns/{cid}/lore/pact",
               json={"name": "Pact", "body": "typed by hand", "keys": ""})
    [row] = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["source"] == "manual" and row["scene"]["id"] == ""
    assert row["undoable"] is True
    client.post(f"/api/campaigns/{cid}/journal/{row['id']}/undo")
    got = client.get(f"/api/campaigns/{cid}/lore/pact").json()
    assert got["body"].strip() == "old body"


def test_a_hand_edit_to_group_state_is_journalled(client, cid):
    croot = store.campaigns.campaign_root(cid)
    gid = store.entities.create_entity(croot, "groups", "The Harbourmen")
    body = {"goals": "hold the pier", "resources": "", "focus": "",
            "public_perception": "", "secrets": ""}
    client.put(f"/api/campaigns/{cid}/groups/{gid}/state", json=body)
    client.put(f"/api/campaigns/{cid}/groups/{gid}/state",
               json={**body, "goals": "take the pier"})
    rows = client.get(f"/api/campaigns/{cid}/journal").json()
    assert [r["source"] for r in rows] == ["manual", "manual"]
    assert client.post(f"/api/campaigns/{cid}/journal/{rows[0]['id']}/undo").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/groups/{gid}/state").json()["goals"] == "hold the pier"


def test_a_failed_entity_edit_journals_nothing(client, cid):
    assert client.put(f"/api/campaigns/{cid}/lore/nope",
                      json={"name": "N", "body": "x", "keys": ""}).status_code == 404
    assert client.get(f"/api/campaigns/{cid}/journal").json() == []


def test_a_scene_rename_follows_into_the_history(client, cid):
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    new_sid = store.scenes.rename_scene(cid, sid, "The reckoning")
    [row] = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["scene"]["id"] == new_sid and row["scene"]["title"] == "The reckoning"


def test_a_garbled_journal_costs_the_history_not_the_page(client, cid):
    (store.campaigns.campaign_root(cid) / "journal.json").write_text("{not json",
                                                                     encoding="utf-8")
    res = client.get(f"/api/campaigns/{cid}/journal")
    assert res.status_code == 200 and res.json() == []


def test_a_deleted_record_still_has_its_history(client, cid):
    """Unlike the rolling panel, which drops a change whose record is gone: this
    is a log of what happened, and a deletion does not un-happen it."""
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    store.overlay.delete_entity(cid, "lore", "pact")
    [row] = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["name"] == "" and row["label"] == "The Pact — lore"
    # ...and undoing it is refused rather than resurrecting the record.
    assert client.post(f"/api/campaigns/{cid}/journal/j1/undo").status_code == 409


def test_a_plan_that_lost_its_target_is_not_offered(client, cid):
    """journal.json is hand-editable. `undoable` is the same predicate the store
    refuses on, so a mangled plan cannot render an enabled button that 400s."""
    sid = store.scenes.create_scene(cid, "The blockade")
    _lore(cid)
    _absorb_lore_edit(cid, sid)
    p = store.campaigns.campaign_root(cid) / "journal.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["entries"][0]["undo"] = {"restore": "old body"}      # no target
    p.write_text(json.dumps(doc), encoding="utf-8")
    [row] = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["undoable"] is False
    assert client.post(f"/api/campaigns/{cid}/journal/j1/undo").status_code == 400
