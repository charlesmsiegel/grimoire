"""Full actor names are unique where a transcript can show both actors."""

import io
import json

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
