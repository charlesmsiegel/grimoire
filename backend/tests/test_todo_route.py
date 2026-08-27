"""GET /api/todo — everything the app noticed, and the ignore that silences one.

Two properties are the whole feature, and both are the kind that rot quietly:

  * a chore at zero is not in the list, so a label's number is always the one
    this request computed;
  * an ignored chore is counted nowhere, and is still there to be restored.

Neither survives a refactor on its own, so both are held here.
"""

from __future__ import annotations

import importlib

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
def campaign(client):
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    return client.post("/api/campaigns",
                       json={"name": "A Long Run", "world": wid}).json()["id"], wid


def _todo(client, cid: str) -> dict:
    r = client.get("/api/todo", params={"campaign": cid})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_clean_campaign_has_no_chores(client, campaign):
    cid, _ = campaign
    body = _todo(client, cid)
    assert body["chores"] == []
    assert body["count"] == 0


def test_a_chore_at_zero_leaves_the_list(client, campaign):
    """The property the whole page rests on.

    A list that can go stale teaches the reader to distrust it, and then the one
    entry that mattered is the one they scroll past. Nothing is stored: the
    chore is derived, so it disappears the moment its cause does.
    """
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    ids = [c["id"] for c in _todo(client, cid)["chores"]]
    assert "open-scenes" in ids

    # Absorb one, and the chore has nothing left to say.
    scenes = store.scenes.read.list_scenes(cid)
    store.scenes.mark_absorbed(cid, scenes[0]["id"], "It ended.", "It ended.")
    assert "open-scenes" not in [c["id"] for c in _todo(client, cid)["chores"]]


def test_a_chore_carries_why_it_matters_not_just_a_count(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    chore = next(c for c in _todo(client, cid)["chores"] if c["id"] == "open-scenes")
    assert chore["why"]
    assert chore["fix"]


def test_ignoring_moves_a_chore_and_stops_counting_it(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    assert _todo(client, cid)["count"] == 1

    r = client.put("/api/todo/open-scenes/ignored", json={"ignored": True})
    assert r.status_code == 200

    body = _todo(client, cid)
    assert body["count"] == 0
    assert [c["id"] for c in body["chores"]] == []
    # ...and it is not gone. A dismissal that cannot be taken back is one
    # nobody dares make.
    assert [c["id"] for c in body["ignored"]] == ["open-scenes"]


def test_restoring_puts_it_back(client, campaign):
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True})
    client.put("/api/todo/open-scenes/ignored", json={"ignored": False})
    assert _todo(client, cid)["count"] == 1


def test_the_shell_badge_does_not_count_an_ignored_chore(client, campaign):
    """The rail's number is what the reader still cares about.

    An ignore that silenced the page but left the badge lit would be worse than
    no ignore at all: the reader would have told the app to stop asking and it
    would still be asking, in the one place they cannot close.
    """
    cid, _ = campaign
    for i in range(2):
        client.post(f"/api/campaigns/{cid}/scenes", json={"title": f"Scene {i}"})
    assert client.get("/api/shell", params={"campaign": cid}).json()["todo"] == 1
    client.put("/api/todo/open-scenes/ignored", json={"ignored": True})
    assert client.get("/api/shell", params={"campaign": cid}).json()["todo"] == 0


def test_an_unknown_chore_id_is_refused(client):
    """An ignore set that accumulates ids nothing emits grows forever and
    silences things nobody can name."""
    r = client.put("/api/todo/not-a-chore/ignored", json={"ignored": True})
    assert r.status_code == 400


def test_a_malformed_ignore_file_is_an_empty_set_not_an_error(client, campaign, tmp_path):
    """This decides what a list SHOWS. A broken judgement file must not stop the
    app telling the user what is waiting."""
    cid, _ = campaign
    (tmp_path / "chores.json").write_text("{ not json", encoding="utf-8")
    assert store.chores.ignored() == set()
    assert _todo(client, cid)["count"] == 0


def test_no_campaign_asked_for_answers_an_empty_list(client):
    r = client.get("/api/todo")
    assert r.status_code == 200
    assert r.json() == {"chores": [], "ignored": [], "count": 0}
