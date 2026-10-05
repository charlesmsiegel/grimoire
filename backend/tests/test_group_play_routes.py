"""Per-scene group-play settings: GET/PUT /scenes/{sid}/group."""

import json

import pytest

from grimoire import store

DEFAULTS = {"order": "directed", "order_list": [], "talkativeness": {},
            "sitting_out": [], "auto_rounds": 0}


def seed(client):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def test_get_group_defaults(client):
    cid, sid = seed(client)
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/group")
    assert r.status_code == 200, r.text
    assert r.json() == DEFAULTS


def test_put_group_round_trips_and_survives_rename(client):
    cid, sid = seed(client)
    body = {"order": "natural", "order_list": ["characters:mara"],
            "talkativeness": {"characters:mara": 80}, "sitting_out": [],
            "auto_rounds": 2}
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/group", json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "settings": body}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/group").json() == body
    raw = store.scenes.read_scene(cid, sid)["meta"]["group_play"]
    assert raw == json.dumps(body, sort_keys=True, separators=(",", ":"))

    renamed = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Winifred"})
    assert renamed.status_code == 200, renamed.text
    new_sid = renamed.json()["id"]
    assert new_sid != sid
    assert client.get(f"/api/campaigns/{cid}/scenes/{new_sid}/group").json() == body


def test_put_group_rejects_invalid(client):
    cid, sid = seed(client)
    good = {"order": "list", "auto_rounds": 1}
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/group", json=good).status_code == 200
    before = client.get(f"/api/campaigns/{cid}/scenes/{sid}/group").json()
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/group", json={"auto_rounds": 9})
    assert r.status_code == 400
    assert r.json()["kind"] == "invalid_group"
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/group").json() == before


def test_put_group_unknown_scene_is_404(client):
    cid, _ = seed(client)
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/group").status_code == 404
    assert client.put(f"/api/campaigns/{cid}/scenes/nope/group", json={}).status_code == 404


@pytest.fixture
def held_scene(client):
    """A scene with a live `turn` run on it (as in test_scene_freeze)."""
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Mara steps onto the dock.")
    identity = store.scenes.scene_identity(cid, sid)
    run, _ = client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity,
        {"campaign": "Saltmarch", "scene": "Mara"})
    return cid, sid, run


def test_put_group_accepted_while_a_run_holds_the_scene(held_scene, client):
    cid, sid, _ = held_scene
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/group", json={"order": "manual"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/group").json()["order"] == "manual"


def test_get_group_survives_corrupt_frontmatter(client):
    cid, sid = seed(client)
    store.scenes.set_group(cid, sid, "{oops")
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/group")
    assert r.status_code == 200, r.text
    assert r.json() == DEFAULTS
