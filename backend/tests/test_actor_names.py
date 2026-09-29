"""Full actor names are unique where a transcript can show both actors."""

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from grimoire import store


def test_world_rejects_cross_kind_name_collision(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    created = client.post(f"/api/worlds/{wid}/characters",
                          json={"name": "Mara", "version_name": "main"})
    assert created.status_code == 200
    duplicate = client.post(f"/api/worlds/{wid}/pcs",
                            json={"name": " mara ", "tags": []})
    assert duplicate.status_code == 409
    assert client.get(f"/api/worlds/{wid}/pcs").json() == []


def test_campaign_rejects_name_in_inherited_world_and_world_checks_dependent_campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    made = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    assert made.status_code == 200, made.text
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    assert [row["name"] for row in client.get(f"/api/campaigns/{cid}/characters").json()] == ["Mara"]
    assert client.post(f"/api/campaigns/{cid}/pcs",
                       json={"name": "MARA", "tags": []}).status_code == 409
    client.post(f"/api/campaigns/{cid}/pcs", json={"name": "Winifred", "tags": []})
    assert client.post(f"/api/worlds/{wid}/characters",
                       json={"name": "winifred"}).status_code == 409


def test_import_rejects_colliding_card_before_creating_character(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Mara", "tags": []})
    card = store.characters.blank_card("Mara")
    response = client.post(f"/api/worlds/{wid}/characters/import",
                           files={"file": ("mara.json", io.BytesIO(json.dumps(card).encode()),
                                           "application/json")},
                           data={"format": "json"})
    assert response.status_code == 409
    assert client.get(f"/api/worlds/{wid}/characters").json() == []


def test_scenario_import_rejects_pc_name_before_writing_any_character(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Mara", "tags": []})
    response = client.post(f"/api/worlds/{wid}/scenario/import", json={
        "characters": [{"name": "Mara", "description": "A witness"}],
        "entries": [], "greetings": [], "art": False,
    })
    assert response.status_code == 409
    assert client.get(f"/api/worlds/{wid}/characters").json() == []


def test_promotion_refuses_a_name_owned_by_a_sibling_campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    first = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    second = client.post("/api/campaigns", json={"name": "Realm", "world": wid}).json()["id"]
    actor = client.post(f"/api/campaigns/{first}/characters", json={"name": "Mara"}).json()
    assert client.post(f"/api/campaigns/{second}/pcs",
                       json={"name": "Mara", "tags": []}).status_code == 200
    result = client.post(f"/api/campaigns/{first}/characters/{actor['character']}/promote")
    assert result.status_code == 409
    assert client.get(f"/api/worlds/{wid}/characters").json() == []


def test_simultaneous_campaign_creates_cannot_claim_the_same_name(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    entered = threading.Event()
    release = threading.Event()
    real_create = store.overlay.create_pc

    def delayed_create(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(store.overlay, "create_pc", delayed_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, f"/api/campaigns/{cid}/pcs",
                            json={"name": "Mara", "tags": []})
        assert entered.wait(5)
        second = pool.submit(client.post, f"/api/campaigns/{cid}/pcs",
                             json={"name": "mara", "tags": []})
        time.sleep(0.05)
        release.set()
        statuses = sorted((first.result().status_code, second.result().status_code))
    assert statuses == [200, 409]


def test_simultaneous_world_character_and_pc_cannot_claim_the_same_name(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    entered = threading.Event()
    release = threading.Event()
    real_create = store.pcs.create_pc

    def delayed_create(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(store.pcs, "create_pc", delayed_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, f"/api/worlds/{wid}/pcs",
                            json={"name": "Mara", "tags": []})
        assert entered.wait(5)
        second = pool.submit(client.post, f"/api/worlds/{wid}/characters",
                             json={"name": "mara"})
        time.sleep(0.05)
        release.set()
        statuses = sorted((first.result().status_code, second.result().status_code))
    assert statuses == [200, 409]


def test_simultaneous_emergent_casts_cannot_claim_the_same_name(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    entered = threading.Event()
    release = threading.Event()
    real_create = store.overlay.create_character

    def delayed_create(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(store.overlay, "create_character", delayed_create)
    path = f"/api/campaigns/{cid}/scenes/{sid}/cast/emergent"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, path, json={"name": "Mara"})
        assert entered.wait(5)
        second = pool.submit(client.post, path, json={"name": "mara"})
        time.sleep(0.05)
        release.set()
        statuses = sorted((first.result().status_code, second.result().status_code))
    assert statuses == [200, 409]


def test_legacy_colliding_version_cannot_be_selected(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    actor = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"}).json()
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Winifred", "tags": []})
    vid = store.characters.create_version(store.worlds.world_root(wid), actor["character"],
                                          "legacy", store.characters.blank_card("Winifred"))
    world_pick = client.put(f"/api/worlds/{wid}/characters/{actor['character']}",
                            json={"default_version": vid})
    assert world_pick.status_code == 409
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    campaign_pick = client.post(
        f"/api/campaigns/{cid}/characters/{actor['character']}/pick-version",
        json={"version": vid})
    assert campaign_pick.status_code == 409
