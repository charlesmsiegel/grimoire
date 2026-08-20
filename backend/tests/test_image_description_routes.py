"""The description write routes, on all seven surfaces, and the reads that ride
along with the image listings the editors already fetch."""

import importlib

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
def world(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _char(client, wid):
    made = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    cid, vid = made["character"], made["version"]
    client.put(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/gallery_1",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    return cid, vid


# ---- characters, world side ------------------------------------------------

def test_world_character_description_roundtrip(client, world):
    cid, vid = _char(client, world)
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images"
    r = client.put(f"{base}/gallery_1/description", json={"description": "A grey quay."})
    assert r.status_code == 200
    detail = client.get(f"/api/worlds/{world}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "A grey quay."}


def test_describing_an_image_that_is_not_there_is_a_404(client, world):
    cid, vid = _char(client, world)
    r = client.put(
        f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/nope/description",
        json={"description": "x"})
    assert r.status_code == 404


def test_empty_description_persists_as_reviewed(client, world):
    """`""` is not the same as never having written one — it is what takes an
    image out of the queue without offering it to the model."""
    cid, vid = _char(client, world)
    base = f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images"
    assert client.put(f"{base}/gallery_1/description", json={"description": ""}).status_code == 200
    detail = client.get(f"/api/worlds/{world}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": ""}


# ---- characters, campaign side --------------------------------------------

def test_campaign_character_description_lands_campaign_side(client, world):
    cid, vid = _char(client, world)
    client.put(f"/api/worlds/{world}/characters/{cid}/versions/{vid}/images/gallery_1/description",
               json={"description": "The world's take."})
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    detail = client.get(f"/api/campaigns/{camp}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "The world's take."}

    r = client.put(
        f"/api/campaigns/{camp}/characters/{cid}/versions/{vid}/images/gallery_1/description",
        json={"description": "This campaign's take."})
    assert r.status_code == 200
    detail = client.get(f"/api/campaigns/{camp}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "This campaign's take."}
    # ...and the world is untouched
    detail = client.get(f"/api/worlds/{world}/characters/{cid}").json()
    version = next(v for v in detail["versions"] if v["id"] == vid)
    assert version["image_descriptions"] == {"gallery_1": "The world's take."}


# ---- pcs -------------------------------------------------------------------

def test_pc_description_roundtrip_both_scopes(client, world):
    made = client.post(f"/api/worlds/{world}/pcs", json={"name": "Mara"}).json()
    pid, vid = made["pc"], made["version"]
    client.put(f"/api/worlds/{world}/pcs/{pid}/versions/{vid}/images/avatar",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    r = client.put(f"/api/worlds/{world}/pcs/{pid}/versions/{vid}/images/avatar/description",
                   json={"description": "Mara, in travelling clothes."})
    assert r.status_code == 200
    version = client.get(f"/api/worlds/{world}/pcs/{pid}").json()["versions"][0]
    assert version["image_descriptions"] == {"avatar": "Mara, in travelling clothes."}

    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.put(f"/api/campaigns/{camp}/pcs/{pid}/versions/{vid}/images/avatar/description",
                   json={"description": "Campaign-side."})
    assert r.status_code == 200
    version = client.get(f"/api/campaigns/{camp}/pcs/{pid}").json()["versions"][0]
    assert version["image_descriptions"] == {"avatar": "Campaign-side."}


# ---- entities --------------------------------------------------------------

def test_entity_description_roundtrip_both_scopes(client, world):
    eid = client.post(f"/api/worlds/{world}/locations",
                      json={"name": "Saltmarch Harbour", "body": "A grey quay."}).json()["id"]
    client.put(f"/api/worlds/{world}/locations/{eid}/images/gallery_1",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    r = client.put(f"/api/worlds/{world}/locations/{eid}/images/gallery_1/description",
                   json={"description": "Fishing boats under fog."})
    assert r.status_code == 200
    assert store.image_descriptions.read(
        store.worlds.world_root(world), eid, "default", "gallery_1",
        base="locations") == "Fishing boats under fog."

    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.put(f"/api/campaigns/{camp}/locations/{eid}/images/gallery_1/description",
                   json={"description": "Campaign-side."})
    assert r.status_code == 200
    assert store.overlay.read_description(camp, eid, "default", "gallery_1",
                                          base="locations") == "Campaign-side."


# ---- the campaign's own library -------------------------------------------

def test_library_description_roundtrip(client, world):
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    client.put(f"/api/campaigns/{camp}/images/coastline",
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    r = client.put(f"/api/campaigns/{camp}/images/coastline/description",
                   json={"description": "A hand-drawn map."})
    assert r.status_code == 200
    listing = client.get(f"/api/campaigns/{camp}/images").json()
    assert [i["name"] for i in listing] == ["coastline"]
    assert store.image_descriptions.read_in(
        store.campaign_images.images_dir(camp)) == {"coastline": "A hand-drawn map."}


def test_library_description_for_a_missing_image_is_a_404(client, world):
    camp = client.post("/api/campaigns",
                       json={"name": "Saltmarch", "world": world}).json()["id"]
    r = client.put(f"/api/campaigns/{camp}/images/nope/description",
                   json={"description": "x"})
    assert r.status_code == 404
