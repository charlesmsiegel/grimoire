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
def test_config_system_prompt_and_quote_color_roundtrip(client):
    client.put("/api/config", json={"system_prompt": "Never speak for the PC.", "quote_color": "on"})
    body = client.get("/api/config").json()
    assert body["system_prompt"] == "Never speak for the PC."
    assert body["quote_color"] == "on"
    assert "openrouter_key" not in body


def test_data_dir_reports_env_override(client, tmp_path):
    body = client.get("/api/config/data-dir").json()
    assert body["data_dir"] == str(tmp_path)
    assert body["source"] == "env"


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


def test_character_image_routes(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images"
    # absent
    assert client.get(base).json() == []
    assert client.get(f"{base}/avatar").status_code == 404
    # upload
    files = {"file": ("a.png", io.BytesIO(b"\x89PNGdata"), "image/png")}
    r = client.put(f"{base}/avatar", files=files)
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(base).json() == [{"name": "avatar", "ext": "png"}]
    got = client.get(f"{base}/avatar")
    assert got.status_code == 200 and got.content == b"\x89PNGdata"
    assert got.headers["content-type"].startswith("image/png")
    # bad type -> 400
    bad = client.put(f"{base}/avatar", files={"file": ("a.svg", io.BytesIO(b"<svg/>"), "image/svg+xml")})
    assert bad.status_code == 400
    # delete
    assert client.delete(f"{base}/avatar").status_code == 200
    assert client.get(f"{base}/avatar").status_code == 404


def test_campaign_image_route_serves_copied_avatar(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{chid}/versions/default/images/avatar",
               files={"file": ("a.png", io.BytesIO(b"PNGBYTES"), "image/png")})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    got = client.get(f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/avatar")
    assert got.status_code == 200 and got.content == b"PNGBYTES"


def test_character_import_garbage_400(client):
    wid = _world(client)
    files = {"file": ("c.json", io.BytesIO(b"nonsense"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 400


def test_chub_import_route(client, monkeypatch):
    from grimoire.store import cards, chub

    wid = _world(client)
    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Imp", "extensions": {}}}, "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "https://chub.ai/characters/creator/imp"})
    assert r.status_code == 200
    body = r.json()
    assert body["character"] and body["version"]
    assert body["updated"] is False
    assert body["gallery"] == {"attempted": 0, "stored": 0}
    assert body["lore"] == {"lorebooks_found": 0, "created": []}


def test_chub_import_route_updates_in_place_when_already_linked(client, monkeypatch):
    from grimoire.store import cards, chub

    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source", json={"url": "creator/abelha"})

    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Abelha Updated", "extensions": {}}}, "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/abelha/chara_card_v2.png",
    })
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "creator/abelha", "into": cid, "into_version": "default"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "character": cid, "version": "default", "updated": True,
        "gallery": {"attempted": 0, "stored": 0},
        "lore": {"lorebooks_found": 0, "created": []},
    }
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert [v["id"] for v in detail["versions"]] == ["default"]  # no new version
    assert detail["versions"][0]["card"]["data"]["name"] == "Abelha Updated"


def test_chub_import_route_direct_url_not_chub(client, monkeypatch):
    from grimoire.store import cards

    wid = _world(client)
    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Direct", "extensions": {}}}, "png")
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "https://example.com/card.png"})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] is False
    assert body["gallery"] == {"attempted": 0, "stored": 0}
    assert body["lore"] == {"lorebooks_found": 0, "created": []}

    detail = client.get(f"/api/worlds/{wid}/characters/{body['character']}").json()
    assert detail["versions"][0]["card"]["data"]["name"] == "Direct"
    assert detail["versions"][0]["chub_source"] == "https://example.com/card.png"
    assert detail["versions"][0]["is_chub"] is False


def test_chub_import_route_bad_url(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters/import/chub", json={"url": "not a url"})
    assert r.status_code == 400


def test_chub_import_route_unreachable(client, monkeypatch):
    from grimoire.store import chub

    wid = _world(client)
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: None)
    r = client.post(f"/api/worlds/{wid}/characters/import/chub", json={"url": "creator/missing"})
    assert r.status_code == 404


def _version_chub_source(detail, vid):
    return next(v for v in detail["versions"] if v["id"] == vid)["chub_source"]


def test_chub_source_routes(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == ""

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                     json={"url": "creator/slug"})
    assert r.status_code == 200 and r.json() == {"chub_source": "https://chub.ai/characters/creator/slug"}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == "https://chub.ai/characters/creator/slug"

    r = client.delete(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source")
    assert r.status_code == 200 and r.json() == {"chub_source": ""}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == ""


def test_chub_source_is_per_version_route(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions",
                json={"name": "futa", "card": {"spec": "chara_card_v3", "spec_version": "3.0",
                                               "data": {"name": "Abelha", "extensions": {}}}})
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                json={"url": "creator/abelha-main"})

    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert _version_chub_source(detail, "default") == "https://chub.ai/characters/creator/abelha-main"
    assert _version_chub_source(detail, "futa") == ""  # sibling version untouched


def test_chub_source_route_accepts_an_arbitrary_url(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Direct"}).json()["character"]

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                     json={"url": "https://example.com/cards/direct.png"})
    assert r.status_code == 200
    assert r.json() == {"chub_source": "https://example.com/cards/direct.png"}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == "default")
    assert version["chub_source"] == "https://example.com/cards/direct.png"
    assert version["is_chub"] is False


def test_chub_source_route_bad_url(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                     json={"url": "not a url"})
    assert r.status_code == 400


def test_chub_source_route_unknown_character_or_version(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    r = client.post(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-source",
                     json={"url": "creator/slug"})
    assert r.status_code == 404
    r = client.delete(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-source")
    assert r.status_code == 404

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-source",
                     json={"url": "creator/slug"})
    assert r.status_code == 404
    r = client.delete(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-source")
    assert r.status_code == 404


def test_chub_gallery_and_lorebooks_routes(client, monkeypatch):
    from grimoire.store import chub

    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-source",
                json={"url": "creator/abelha"})

    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": True, "related_lorebooks": [7],
    })
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg"])
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (b"\xff\xd8\xffJPEGDATA", "image/jpeg"))
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "x"}]}},
    })

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-gallery")
    assert r.status_code == 200
    events = [json.loads(line[len("data:"):].strip())
              for line in r.text.splitlines() if line.startswith("data:")]
    assert events == [
        {"total": 1}, {"done": 1, "total": 1}, {"summary": {"attempted": 1, "stored": 1}},
    ]

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-lorebooks")
    assert r.status_code == 200
    body = r.json()
    assert body["lorebooks_found"] == 1 and len(body["created"]) == 1


def test_chub_gallery_and_lorebooks_routes_require_a_link(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-gallery")
    assert r.status_code == 404
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/chub-lorebooks")
    assert r.status_code == 404


def test_chub_gallery_and_lorebooks_routes_unknown_character_or_version(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    assert client.post(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-gallery").status_code == 404
    assert client.post(f"/api/worlds/{wid}/characters/nobody/versions/default/chub-lorebooks").status_code == 404
    assert client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-gallery").status_code == 404
    assert client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghost/chub-lorebooks").status_code == 404


def test_chub_unlinked_route(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/characters/chub-unlinked").json() == {"versions": []}

    linked = client.post(f"/api/worlds/{wid}/characters", json={"name": "Abelha"}).json()["character"]
    client.post(f"/api/worlds/{wid}/characters/{linked}/versions/default/chub-source",
                json={"url": "creator/abelha"})
    unlinked = client.post(f"/api/worlds/{wid}/characters", json={"name": "Loose End"}).json()["character"]

    r = client.get(f"/api/worlds/{wid}/characters/chub-unlinked")
    assert r.status_code == 200
    assert r.json() == {"versions": [
        {"character": unlinked, "character_name": "Loose End", "version": "default", "version_name": "Loose End"},
    ]}


def test_character_book_import_route(client):
    wid = _world(client)
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {
        "name": "Sera",
        "character_book": {"entries": [{"keys": ["pact"], "content": "the salt pact", "name": "Pact"}]},
        "extensions": {},
    }}
    cid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Sera", "card": card}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/lorebook/import")
    assert r.status_code == 200
    created = r.json()["created"]
    assert len(created) == 1 and created[0]["kind"] == "lore"
    assert any(e["id"] == created[0]["id"] for e in client.get(f"/api/worlds/{wid}/lore").json())


def test_character_book_import_empty(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/lorebook/import")
    assert r.status_code == 200 and r.json() == {"created": []}


def test_world_tag_vocabulary_crud(client):
    wid = _world(client)
    tid = client.post(f"/api/worlds/{wid}/tags", json={"name": "Student"}).json()["id"]
    assert tid == "student"
    assert client.get(f"/api/worlds/{wid}/tags").json() == {"student": "Student"}
    client.put(f"/api/worlds/{wid}/tags/{tid}", json={"name": "Pupil"})
    assert client.get(f"/api/worlds/{wid}/tags").json() == {"student": "Pupil"}
    assert client.delete(f"/api/worlds/{wid}/tags/{tid}").status_code == 200
    assert client.get(f"/api/worlds/{wid}/tags").json() == {}
    assert client.put(f"/api/worlds/{wid}/tags/ghost", json={"name": "X"}).status_code == 404


def test_campaign_local_pc_create_seat_and_sync(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # create a campaign-local PC (overlay; not written to the world)
    r = client.post(f"/api/campaigns/{cid}/pcs", json={
        "name": "Mara", "tags": ["rebel"],
        "persona": {"name": "Mara", "pronouns": "she/her", "summary": "outlaw", "description": "On the run."}})
    assert r.status_code == 200
    assert r.json()["pc"] == "mara"
    # lists at campaign scope, absent at world scope
    assert [p["id"] for p in client.get(f"/api/campaigns/{cid}/pcs").json()] == ["mara"]
    assert client.get(f"/api/worlds/{wid}/pcs").json() == []
    # seat as player with explicit version
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": "mara", "version": "default"}).status_code == 200
    assert {"kind": "pcs", "id": "mara", "role": "player"} in \
        client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    # re-seat in a second scene with version omitted -> resolved from the campaign
    sid2 = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S2"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid2}/cast",
                       json={"kind": "pcs", "id": "mara"}).status_code == 200
    # no spurious incoming sync change for the local PC
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []


def test_scene_location_set_get_and_move(client):
    wid, cid = _campaign(client)
    a = client.post(f"/api/campaigns/{cid}/locations",
                    json={"name": "Salt Cathedral", "body": "A drowned basilica."}).json()["id"]
    b = client.post(f"/api/campaigns/{cid}/locations",
                    json={"name": "Drowned Market", "body": "Shallow stalls."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # first set: silent
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": a}).json() == \
        {"ok": True, "moved": False, "name": "Salt Cathedral"}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/location").json() == \
        {"current": {"id": a, "name": "Salt Cathedral"}, "visited": []}
    # move: announces
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": b}).json() == \
        {"ok": True, "moved": True, "name": "Drowned Market"}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == [
        {"role": "assistant", "content": "*The scene moves to Drowned Market.*"}]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/location").json() == \
        {"current": {"id": b, "name": "Drowned Market"}, "visited": [{"id": a, "name": "Salt Cathedral"}]}


def test_scene_location_unknown_404(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location",
                      json={"location": "nope"}).status_code == 404


def test_scene_context_breakdown(client):
    wid, cid = _campaign(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert body["model"]
    labels = [s["label"] for s in body["sections"]]
    assert "Character descriptions" in labels
    assert all(s["tokens"] > 0 for s in body["sections"])
    assert body["total_tokens"] == sum(s["tokens"] for s in body["sections"])


def test_edit_message_route(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "sk-test"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "helo"})
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0", json={"content": "hello"}).json() == {"ok": True}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"][0]["content"] == "hello"
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/9", json={"content": "x"}).status_code == 400


def test_cast_detail_for_character_and_pc(client):
    wid, cid = _campaign(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.",
                     "personality": "cold", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.post(f"/api/campaigns/{cid}/pcs", json={
        "name": "Mara", "persona": {"name": "Mara", "pronouns": "she/her", "summary": "outlaw", "description": "On the run."}})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "pcs", "id": "mara", "version": "default"})

    c = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/seraphine").json()
    assert c["name"] == "Seraphine" and "Drowned King" in c["body"] and "cold" in c["body"]
    p = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/pcs/mara").json()
    assert p["name"] == "Mara" and "On the run." in p["body"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/ghost").status_code == 404


def test_world_pc_crud_and_tag_validation(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/tags", json={"name": "Student"})
    r = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara", "tags": ["student"]})
    pid = r.json()["pc"]
    assert pid == "elara"
    assert client.get(f"/api/worlds/{wid}/pcs/{pid}").json()["meta"]["tags"] == ["student"]
    assert client.get(f"/api/worlds/{wid}").json()["counts"]["pcs"] == 1
    assert client.post(f"/api/worlds/{wid}/pcs", json={"name": "Rook", "tags": ["ghost"]}).status_code == 400
    assert client.delete(f"/api/worlds/{wid}/pcs/{pid}").status_code == 200


def test_entity_keys_via_routes(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/lore", json={"name": "Salt Pact", "body": "p", "keys": "pact"}).json()["id"]
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["keys"] == "pact"
    client.put(f"/api/worlds/{wid}/lore/{eid}", json={"keys": "pact, salt"})
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["keys"] == "pact, salt"


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
                       json={"kind": "characters", "id": "seraphine", "version": "default"}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json() == [
        {"kind": "characters", "id": "seraphine", "role": "npc"}]
    # suggestion surfaces drowned-king, then dismiss hides it
    sugg = client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json()
    assert [s["character"] for s in sugg] == ["drowned-king"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/suggestions/dismiss", json={"character": "drowned-king"})
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json() == []


def test_cast_pc_and_character_as_player(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "desmond"})
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # a PC casts as player automatically
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": "elara"}).status_code == 200
    # a character cast explicitly as player
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond", "role": "player"}).status_code == 200
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    assert {"kind": "pcs", "id": "elara", "role": "player"} in cast
    assert {"kind": "characters", "id": "desmond", "role": "player"} in cast
    roster = client.get(f"/api/campaigns/{cid}/appearances").json()
    assert {r["kind"] for r in roster} == {"pcs", "characters"}


def test_recasting_with_different_role_or_version_409(client):
    wid = _world(client)
    got = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    cid = got["character"]
    # add a second version so a version-mismatch is possible
    base = client.get(f"/api/worlds/{wid}/characters/{cid}").json()["versions"][0]["card"]
    client.post(f"/api/worlds/{wid}/characters/{cid}/versions", json={"name": "Alt", "card": base})
    camp = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{camp}/scenes", json={"title": "S"}).json()["id"]
    # first appearance locks version=default, role=npc
    client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    # re-cast as player -> role lock conflict
    assert client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "seraphine", "role": "player"}).status_code == 409
    # re-cast with a different version -> version lock conflict
    assert client.post(f"/api/campaigns/{camp}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "seraphine", "version": "alt"}).status_code == 409


class CapturingOpenRouter:
    def __init__(self):
        self.messages = None

    async def stream(self, messages, model, key):
        self.messages = messages
        for d in ["ok"]:
            yield d


def test_chat_injects_system_message(client):
    wid = _world(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "the drowned keeper", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})

    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_openrouter] = lambda: cap

    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hello"}) as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[0]["role"] == "system"
    assert "the drowned keeper" in cap.messages[0]["content"]
    assert cap.messages[-1] == {"role": "user", "content": "hello"}


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


def test_regenerate_replaces_the_last_assistant_post(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "old reply")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "Hello"}]


def test_regenerate_excludes_the_dropped_post_from_the_prompt(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "old reply")
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_openrouter] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/regenerate") as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[-1] == {"role": "user", "content": "hi"}


def test_regenerate_after_a_failed_turn_behaves_like_retry(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_regenerate_empty_scene_returns_400(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate").status_code == 400


def test_regenerate_sole_opening_post_returns_400(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "assistant", "the greeting")
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate").status_code == 400
    # the opening post is untouched
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "assistant", "content": "the greeting"}]


def test_regenerate_missing_key_returns_409(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate")
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


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


# ---- greetings & plot maps (2b) ----
def test_greeting_present_cast_roundtrips_over_api(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Rowan"})
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Arrival: Mara & Rowan", "character": "mara",
                            "version": "default", "body": "Mara and Rowan.",
                            "present": ["mara", "rowan"]}).json()["id"]
    read = client.get(f"/api/worlds/{wid}/greetings/{gid}").json()
    assert read["meta"]["present"] == ["mara", "rowan"]


def test_greeting_crud_import_edges_and_start(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    got = client.get(f"/api/worlds/{wid}/characters/seraphine").json()
    card = got["versions"][0]["card"]
    card["data"]["first_mes"] = "You meet Seraphine."
    client.put(f"/api/worlds/{wid}/characters/seraphine/versions/default", json={"card": card})
    imported = client.post(f"/api/worlds/{wid}/greetings/import",
                           json={"character": "seraphine", "version": "default"}).json()["greetings"]
    assert len(imported) == 1
    g2 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Reckoning", "character": "seraphine", "version": "default",
                           "body": "It ends here."}).json()["id"]
    client.put(f"/api/worlds/{wid}/greetings/{imported[0]}/edges", json={"leads_to": [g2]})
    read = client.get(f"/api/worlds/{wid}/greetings/{imported[0]}").json()
    assert read["meta"]["character"] == "seraphine"
    assert read["edges"]["leads_to"] == [g2]   # edges surfaced on read for the editor
    # the target greeting surfaces its predecessors (what unlocks it) for the view sidebar
    assert client.get(f"/api/worlds/{wid}/greetings/{g2}").json()["predecessors"] == [imported[0]]
    assert [x["id"] for x in client.get(f"/api/worlds/{wid}/greetings").json()] == sorted([imported[0], g2])

    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    avail = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert {x["id"]: x["available"] for x in avail}[imported[0]] is True
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": imported[0]})
    assert r.status_code == 200
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()
    assert scene["messages"][0]["content"] == "You meet Seraphine."
    # starting again on a non-empty scene -> 409
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g2}).status_code == 409


def test_start_from_greeting_unknown_404(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": "nope"}).status_code == 404


def test_opener_streams_without_persisting(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "Begin in a tavern."}) as r:
        body = "".join(r.iter_text())
    assert "Hel" in body and "lo" in body and '"done": true' in body
    # ephemeral: the scene is untouched
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []


def test_opener_requires_key(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/opener",
                       json={"prompt": "x"}).status_code == 409


def test_first_post_adopts_opener_onto_empty_scene(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                       json={"text": "  Mist rolls in.  "}).status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs == [{"role": "assistant", "content": "Mist rolls in."}]
    # a second first-post is refused once the scene has messages
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                       json={"text": "again"}).status_code == 409
    # empty text is rejected on a fresh scene
    sid2 = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid2}/first-post",
                       json={"text": "   "}).status_code == 400


# ---- lorebook / world-info import (2c) ----
def test_lorebook_parse_then_import(client):
    wid = _world(client)
    book = {"entries": {
        "0": {"key": ["pact"], "comment": "Salt Pact", "content": "It binds."},
        "1": {"key": ["docks"], "comment": "The Docks", "content": "Wet planks."},
    }}
    files = {"file": ("wi.json", io.BytesIO(json.dumps(book).encode()), "application/json")}
    parsed = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files,
                         data={"format": "lorebook"})
    assert parsed.status_code == 200
    entries = parsed.json()["entries"]
    assert {e["name"] for e in entries} == {"Salt Pact", "The Docks"}
    # parse writes nothing
    assert client.get(f"/api/worlds/{wid}/lore").json() == []

    # route the docks entry to locations, keep the other as lore, then commit
    for e in entries:
        if e["name"] == "The Docks":
            e["category"] = "locations"
    created = client.post(f"/api/worlds/{wid}/lorebook/import", json={"entries": entries})
    assert created.status_code == 200
    kinds = {c["kind"] for c in created.json()["created"]}
    assert kinds == {"lore", "locations"}
    assert [e["name"] for e in client.get(f"/api/worlds/{wid}/lore").json()] == ["Salt Pact"]
    assert [e["name"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == ["The Docks"]


def test_lorebook_parse_bad_file_400(client):
    wid = _world(client)
    files = {"file": ("x.json", io.BytesIO(b"not json"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files, data={"format": "lorebook"})
    assert r.status_code == 400


def test_lorebook_import_unknown_category_400(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lorebook/import",
                    json={"entries": [{"name": "X", "keys": [], "body": "y", "category": "bogus"}]})
    assert r.status_code == 400


def test_lorebook_imported_key_activates_in_builder(client):
    # end-to-end sanity: an imported keyed entry feeds the context builder
    wid = _world(client)
    book = {"entries": {"0": {"key": ["leviathan"], "comment": "Leviathan", "content": "the beast"}}}
    files = {"file": ("wi.json", io.BytesIO(json.dumps(book).encode()), "application/json")}
    entries = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files,
                          data={"format": "lorebook"}).json()["entries"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    # commit into the CAMPAIGN root via the store (campaign-scoped lore the builder reads)
    import grimoire.store as store
    store.lorebook.commit(store.campaigns.campaign_root(cid), entries)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "the leviathan rises")
    msgs = store.context.build_messages(cid, sid)
    assert any("the beast" in m["content"] for m in msgs if m["role"] == "system")


def test_localize_endpoint_streams_and_rewrites(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    card = {
        "spec": "chara_card_v3", "spec_version": "3.0",
        "data": {"name": "Img", "description": "![a](https://h/a.png)",
                 "alternate_greetings": []},
    }
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    cid, vid = r.json()["character"], r.json()["version"]

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    monkeypatch.setattr(store.fetch, "download_url", lambda url: (png, "png"))

    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    assert resp.status_code == 200
    events = [json.loads(line[len("data:"):].strip())
              for line in resp.text.splitlines() if line.startswith("data:")]
    assert events[0] == {"total": 1}
    assert events[-1]["summary"]["localized"] == 1

    exported = client.get(
        f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export?format=json").json()
    assert "/api/worlds/" in exported["data"]["description"]


def test_localize_endpoint_no_refs_short_circuits(client):
    wid = client.post("/api/worlds", json={"name": "W2"}).json()["id"]
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Plain", "description": "no images", "alternate_greetings": []}}
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    cid, vid = r.json()["character"], r.json()["version"]
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[0] == {"total": 0}
    assert events[-1]["summary"]["total"] == 0


def test_localize_endpoint_404_for_missing_character(client):
    wid = client.post("/api/worlds", json={"name": "W3"}).json()["id"]
    resp = client.post(f"/api/worlds/{wid}/characters/ghost/versions/v/localize")
    assert resp.status_code == 404


def test_localize_endpoint_404_for_missing_version(client):
    wid = client.post("/api/worlds", json={"name": "W4"}).json()["id"]
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Real"}).json()["character"]
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/ghostver/localize")
    assert resp.status_code == 404


def _import_card(client, wid, description):
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "X", "description": description, "alternate_greetings": []}}
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    return r.json()["character"], r.json()["version"]


def test_localize_endpoint_emits_error_frame_on_generator_failure(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "WE"}).json()["id"]
    cid, vid = _import_card(client, wid, "![a](https://h/a.png)")

    def boom(*a, **k):
        yield {"total": 1}
        raise RuntimeError("kaboom")

    monkeypatch.setattr(store.localize, "localize_card", boom)
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[0] == {"total": 1}
    assert events[-1]["error"]["kind"] == "localize"


def test_localize_endpoint_does_not_persist_when_nothing_localized(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "WN"}).json()["id"]
    cid, vid = _import_card(client, wid, "see https://h/page now")  # a non-image link
    monkeypatch.setattr(store.fetch, "download_url", lambda url: None)  # never an image

    calls = []
    monkeypatch.setattr(store.characters, "update_version",
                        lambda *a, **k: calls.append(a))

    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[-1]["summary"]["localized"] == 0
    assert events[-1]["summary"]["skipped"] == 1
    assert calls == []  # changed-gating skipped the write


class FakeOpenRouterComplete:
    def __init__(self, text):
        self.text = text

    async def stream(self, messages, model, key):
        yield self.text

    async def complete(self, messages, model, key):
        return self.text


def _world_char(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Aese", "version_name": "main"}).json()["character"]
    return wid, cid


def test_get_tagline_absent_is_empty(client):
    wid, cid = _world_char(client)
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": ""}


def test_put_tagline_saves(client):
    wid, cid = _world_char(client)
    r = client.put(f"/api/worlds/{wid}/characters/{cid}/tagline",
                   json={"tagline": "A snowleopardgirl."})
    assert r.json() == {"ok": True}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": "A snowleopardgirl."}


def test_post_tagline_generate_from_model(client):
    wid, cid = _world_char(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_openrouter] = \
        lambda: FakeOpenRouterComplete("A silent snowleopardgirl.\nignored second line")
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/tagline/generate")
    assert r.status_code == 200
    assert r.json() == {"tagline": "A silent snowleopardgirl."}
    # preview only: generate does not persist until the caller saves via PUT
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/tagline").json() == {"tagline": ""}


def test_post_tagline_generate_requires_key(client):
    wid, cid = _world_char(client)
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/tagline/generate")
    assert r.status_code == 409


# ---- scene calendar ----
def test_campaign_create_writes_calendar_with_region(client):
    from grimoire.store import campaigns, calendars
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid, "region": "GB"}).json()["id"]
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    assert cfg["primary"]["region"] == "GB"


def test_campaign_create_defaults_region_us(client):
    from grimoire.store import campaigns, calendars
    _wid, cid = _campaign(client)
    assert calendars.read_calendar(campaigns.campaign_root(cid))["primary"]["region"] == "US"


def test_datetime_get_put_roundtrip(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"] is None
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-12-25"})
    assert r.json() == {"ok": True, "advanced": False, "friendly": "25 December 2026",
                        "id": "001--2026-12-25--s"}
    sid = r.json()["id"]  # first date set renames the scene
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"]["native"] == "2026-12-25"
    assert got["current"]["weekday"] == "Friday"
    assert "Christmas Day" in got["current"]["holidays_today"]
    assert got["history"] == ["2026-12-25"]


def test_datetime_put_bad_date_is_400(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-13-40"})
    assert r.status_code == 400


def test_character_birthdate_route_sets_meta(client):
    wid = _world(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    r = client.put(f"/api/worlds/{wid}/characters/{chid}/birthdate", json={"birthdate": "1985-03-14"})
    assert r.json() == {"ok": True}
    assert client.get(f"/api/worlds/{wid}/characters/{chid}").json()["meta"]["birthdate"] == "1985-03-14"


def test_datetime_get_includes_cast_age(client):
    wid = _world(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{chid}/birthdate", json={"birthdate": "1990-12-25"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/datetime", json={"datetime": "2026-12-25"})
    sid = r.json()["id"]  # first date set renames the scene
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["current"]["cast"]
    assert cast == [{"kind": "characters", "id": chid, "name": "Seraphine",
                     "age": 36, "birthday_today": True}]


def test_calendar_config_get_put(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["primary"]["region"] == "US"
    cfg = {"primary": {"provider": "gregorian", "region": "GB",
            "custom_holidays": [{"name": "Founding Day", "month": 4, "day": 12}], "anchor": None},
           "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).json() == {"ok": True}
    got = client.get(f"/api/campaigns/{cid}/calendar").json()
    assert got["primary"]["region"] == "GB"
    assert got["primary"]["custom_holidays"][0]["name"] == "Founding Day"


def test_calendar_config_confirmed_flag(client):
    _wid, cid = _campaign(client)
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["confirmed"] is False
    cfg = {"primary": {"provider": "gregorian", "region": "US", "custom_holidays": [], "anchor": None},
           "secondary": None, "confirmed": True}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=cfg).json() == {"ok": True}
    assert client.get(f"/api/campaigns/{cid}/calendar").json()["confirmed"] is True


def test_calendar_config_rejects_malformed_custom_holiday(client):
    _wid, cid = _campaign(client)
    bad = {"primary": {"provider": "gregorian", "region": "US",
            "custom_holidays": [{"name": "Oops", "month": 13}], "anchor": None}, "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=bad).status_code == 400
    nameless = {"primary": {"provider": "gregorian", "region": "US",
            "custom_holidays": [{"month": 4, "day": 12}], "anchor": None}, "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=nameless).status_code == 400
    bogus = {"primary": {"provider": "bogus", "region": "US", "custom_holidays": [], "anchor": None},
             "secondary": None}
    assert client.put(f"/api/campaigns/{cid}/calendar", json=bogus).status_code == 400


def test_absorb_returns_preview_without_persisting(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered the crypt.")
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouterComplete(
        '{"one_line": "They entered.", "summary": "The party entered the crypt.",'
        ' "keywords": ["crypt"], "timeline_events": []}')
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    body = r.json()
    assert body["one_line"] == "They entered." and body["keywords"] == ["crypt"]
    assert client.get(f"/api/campaigns/{cid}/chronicle").json() == []  # not persisted yet


def test_absorb_writes_dossier_for_present_character(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Aese", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    store.scenes.append_message(cid, sid, "user", "Aese served tea.")
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_openrouter] = \
        lambda: FakeOpenRouterComplete("Aese is a shy snowleopardgirl who now trusts the owner.")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200
    croot = store.campaigns.campaign_root(cid)
    assert "Aese is a shy snowleopardgirl" in store.dossiers.read(croot, "aese")


def test_absorb_survives_dossier_failure(client):
    # A dossier generation error must not fail the absorb (the loop swallows per character).
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Aese", "version_name": "main"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    store.scenes.append_message(cid, sid, "user", "hi")
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})

    class Fake:  # 1st complete() = extraction (ok); later complete() = dossier (boom)
        def __init__(self):
            self.calls = 0

        async def stream(self, m, mo, k):
            yield "{}"

        async def complete(self, m, mo, k):
            self.calls += 1
            if self.calls == 1:
                return '{"one_line": "ok", "summary": "s", "keywords": [], "timeline_events": []}'
            raise RuntimeError("dossier boom")

    client.app.dependency_overrides[routes.get_openrouter] = lambda: Fake()
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 200 and r.json()["one_line"] == "ok"
    assert store.dossiers.read(store.campaigns.campaign_root(cid), "aese") == ""  # failed write skipped


def test_absorb_empty_scene_is_400(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert r.status_code == 400


def test_save_chronicle_persists_and_lists(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": "They entered.", "summary": "In the crypt.",
                         "keywords": ["crypt"],
                         "timeline_events": [{"date": "2026-01-01", "text": "Entered."}]})
    assert r.status_code == 200 and r.json()["one_line"] == "They entered."
    listed = client.get(f"/api/campaigns/{cid}/chronicle").json()
    assert len(listed) == 1 and listed[0]["summary"] == "In the crypt."
    assert store.scenes.read_scene(cid, sid)["meta"]["done"] == "true"  # scene marked done


def test_absorb_missing_key_returns_409(client):
    _, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered.")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


def test_absorb_upstream_error_returns_502(client):
    from grimoire.openrouter import OpenRouterError

    class FakeRaises:
        async def complete(self, messages, model, key):
            raise OpenRouterError("bad_response", "boom")

    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We entered.")
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeRaises()
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert resp.status_code == 502 and resp.json()["kind"] == "bad_response"


def test_absorb_returns_edits_without_persisting(client):
    _, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    croot = store.campaigns.campaign_root(cid)
    ch = store.characters.create_character(croot, "Seraphine", "main", store.characters.blank_card("Seraphine"))[0]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.appearances.appear(cid, sid, "characters", ch, "main", "npc")
    store.scenes.append_message(cid, sid, "user", "We fought.")
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouterComplete(
        '{"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],'
        f' "character_state_edits": [{{"id": "{ch}", "current_state": "hurt"}}],'
        ' "lore_edits": [], "authored_edits": []}')
    body = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb").json()
    assert body["edits"][0]["kind"] == "character_state" and body["edits"][0]["after"] == "hurt"
    assert store.playstate.read_state(croot, ch) is None  # not persisted


def test_scene_suggestions_returns_resolved(client):
    wid, cid = _campaign(client)
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    ann = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Ann", "version_name": "main"}).json()["character"]
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouterComplete(
        '{"suggestions": [{"title": "T", "premise": "P",'
        f' "cast": ["characters:{ann}"], "location": ""}}]}}')
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    s = r.json()["suggestions"][0]
    assert s["title"] == "T" and s["premise"] == "P"
    assert s["cast"][0] == {"kind": "characters", "id": ann, "name": "Ann"}
    assert s["location"] is None


def test_scene_suggestions_missing_key_returns_409(client):
    _, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 409 and r.json()["kind"] == "missing_key"


def test_put_chronicle_applies_approved_edits(client):
    _, cid = _campaign(client)
    croot = store.campaigns.campaign_root(cid)
    ch = store.characters.create_character(croot, "Seraphine", "main", store.characters.blank_card("Seraphine"))[0]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.appearances.appear(cid, sid, "characters", ch, "main", "npc")
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
        "edits": [{"id": f"character_state:{ch}", "kind": "character_state",
                   "target": {"kind": "characters", "id": ch}, "field": "current_state", "after": "Loyal."}]})
    assert r.json()["applied"] == [f"character_state:{ch}"]
    assert store.playstate.read_state(croot, ch)["current_state"] == "Loyal."


def _apply_lore_change(client, cid):
    croot = store.campaigns.campaign_root(cid)
    store.entities.create_entity(croot, "lore", "Pact", body="old body")
    edit = {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body", "before": "old body",
            "after": "old body\nnew line", "authored": False}
    store.absorb.apply_edits(cid, [edit], "s1")


def test_get_changes_returns_name_scene_and_diff(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)  # records under scene "s1" (never created -> title falls back)
    out = client.get(f"/api/campaigns/{cid}/changes").json()
    assert len(out) == 1
    rec = out[0]
    assert rec["ref"] == {"kind": "lore", "id": "pact"} and rec["name"] == "Pact"
    assert rec["scene"]["id"] == "s1"  # deleted/unknown scene -> title falls back to id
    ops = [d["op"] for d in rec["fields"][0]["diff"]]
    assert "insert" in ops


def test_get_changes_empty_and_not_shadowed_by_kind_route(client):
    _, cid = _campaign(client)
    res = client.get(f"/api/campaigns/{cid}/changes")
    assert res.status_code == 200 and res.json() == []  # not routed to the generic /{kind}


def test_get_changes_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/changes").status_code == 404


def test_get_changes_drops_deleted_record(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)
    assert client.delete(f"/api/campaigns/{cid}/lore/pact").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/changes").json() == []  # entity gone -> row dropped


def test_get_changes_tolerates_garbled_chronicle(client):
    _, cid = _campaign(client)
    _apply_lore_change(client, cid)
    (store.campaigns.campaign_root(cid) / "chronicle.json").write_text("{bad json", encoding="utf-8")
    out = client.get(f"/api/campaigns/{cid}/changes")
    assert out.status_code == 200 and len(out.json()) == 1
    assert out.json()[0]["scene"]["date"] == ""  # date degrades, no 500


def test_list_campaigns_scene_counts(client):
    _, cid = _campaign(client)
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "First Light"})
    client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Salt Road"})
    listing = [c for c in client.get("/api/campaigns").json() if c["id"] == cid]
    assert listing[0]["scenes"] == 2
    assert listing[0]["last_scene"] in ("First Light", "The Salt Road")
