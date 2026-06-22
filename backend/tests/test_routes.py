import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app


class FakeOpenRouter:
    def __init__(self, deltas):
        self.deltas = deltas

    async def stream(self, messages, model, key):
        for d in self.deltas:
            yield d


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def _world(client, name="W"):
    return client.post("/api/worlds", json={"name": name}).json()["id"]


def _campaign(client, name="Run"):
    wid = _world(client)
    return wid, client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]


# ---- config (unchanged behavior) ----
def test_config_never_leaks_key(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    body = client.get("/api/config").json()
    assert body["key_set"] is True
    assert "sk-or-secret" not in json.dumps(body)


# ---- worlds ----
def test_world_crud(client):
    wid = _world(client, "Drowned Realm")
    assert wid == "drowned-realm"
    assert [w["id"] for w in client.get("/api/worlds").json()] == [wid]
    client.put(f"/api/worlds/{wid}", json={"name": "Renamed"})
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["name"] == "Renamed"
    assert client.delete(f"/api/worlds/{wid}").status_code == 200
    assert client.get("/api/worlds").json() == []


def test_world_entity_crud(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Drowned Library", "body": "Keeper"}).json()["id"]
    assert eid == "drowned-library"
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == [eid]
    client.put(f"/api/worlds/{wid}/locations/{eid}", json={"body": "Updated"})
    assert client.get(f"/api/worlds/{wid}/locations/{eid}").json()["body"].strip() == "Updated"
    assert client.delete(f"/api/worlds/{wid}/locations/{eid}").status_code == 200


def test_world_character_container_crud(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    cid = r.json()["character"]
    assert cid == "seraphine"
    # default version exists
    got = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert [v["id"] for v in got["versions"]] == ["default"]
    # add a version
    vid = client.post(f"/api/worlds/{wid}/characters/{cid}/versions",
                      json={"name": "Corrupted", "card": got["versions"][0]["card"]}).json()["version"]
    assert vid == "corrupted"
    # world counts include characters
    assert client.get(f"/api/worlds/{wid}").json()["counts"]["characters"] == 1
    # an unmatched deep path 404s
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/extra/nope").status_code == 404
    # delete
    assert client.delete(f"/api/worlds/{wid}/characters/{cid}").status_code == 200


def test_character_import_export_json(client):
    wid = _world(client)
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Imported", "description": "x", "extensions": {}}}
    files = {"file": ("c.json", io.BytesIO(json.dumps(card).encode()), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 200
    cid, vid = r.json()["character"], r.json()["version"]
    exp = client.get(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export", params={"format": "json"})
    assert exp.status_code == 200
    assert json.loads(exp.content)["data"]["name"] == "Imported"


def test_character_import_garbage_400(client):
    wid = _world(client)
    files = {"file": ("c.json", io.BytesIO(b"nonsense"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 400


def test_unknown_kind_404(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/weapons").status_code == 404


# ---- campaigns ----
def test_campaign_create_copies_world(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Drowned Library", "body": "Keeper"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/locations/drowned-library").json()["body"].strip() == "Keeper"


def test_campaign_missing_world_400(client):
    assert client.post("/api/campaigns", json={"name": "X", "world": "nope"}).status_code == 400


# ---- sync ----
def test_incoming_and_accept_flow(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Salt Pact", "body": "pact"})
    pend = client.get(f"/api/campaigns/{cid}/incoming").json()
    assert [p["status"] for p in pend] == ["new"]
    client.post(f"/api/campaigns/{cid}/incoming/accept", json={"refs": [{"kind": "lore", "id": "salt-pact"}]})
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    assert client.get(f"/api/campaigns/{cid}/lore/salt-pact").json()["body"].strip() == "pact"


def test_reject_flow(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Pact", "body": "p"})
    client.post(f"/api/campaigns/{cid}/incoming/reject", json={"refs": [{"kind": "lore", "id": "pact"}]})
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    assert client.get(f"/api/campaigns/{cid}/lore/pact").status_code == 404


def test_world_push_view(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/locations", json={"name": "A", "body": "a"})
    rows = client.get(f"/api/worlds/{wid}/campaigns").json()
    assert rows == [{"id": cid, "name": "Run", "pending": {"new": 1, "update": 0, "conflict": 0}}]


# ---- cast & suggestions ----
def test_cast_and_suggestions_flow(client):
    wid = _world(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Drowned King"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Docks"}).json()["id"]
    # manual appearance
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"character": "seraphine", "version": "default"}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json() == ["seraphine"]
    # suggestion surfaces drowned-king, then dismiss hides it
    sugg = client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json()
    assert [s["character"] for s in sugg] == ["drowned-king"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/suggestions/dismiss", json={"character": "drowned-king"})
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json() == []


# ---- scenes (re-homed chat) ----
def test_chat_missing_key_returns_409(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


def test_chat_streams_and_persists(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 200
    assert 'data: {"delta": "Hel"}' in resp.text
    assert 'data: {"done": true}' in resp.text
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1] == {"role": "assistant", "content": "Hello"}


def test_retry_regenerates_without_adding_a_user_turn(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_scene_rename_and_delete(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Old"}).json()["id"]
    new_id = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "New Name"}).json()["id"]
    assert new_id.endswith("new-name")
    assert client.put(f"/api/campaigns/{cid}/scenes/{new_id}", json={"title": "  "}).status_code == 400
    assert client.delete(f"/api/campaigns/{cid}/scenes/{new_id}").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_scene_missing_404(client):
    _wid, cid = _campaign(client)
    assert client.delete(f"/api/campaigns/{cid}/scenes/nope").status_code == 404
