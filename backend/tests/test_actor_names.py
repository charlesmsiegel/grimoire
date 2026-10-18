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


def test_a_refused_name_is_said_in_words_not_refs(client):
    # The 409 is shown verbatim, and the campaign wizard printed "characters:mara
    # in campaign <slug>" at a player who had only typed a name.
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    refused = client.post(f"/api/campaigns/{cid}/pcs", json={"name": "mara", "tags": []})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert 'There is already a character called "Mara" in this campaign' in detail
    assert "characters:" not in detail and cid not in detail


def test_a_world_write_names_the_campaign_that_holds_the_name(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    client.post(f"/api/campaigns/{cid}/pcs", json={"name": "Winifred", "tags": []})
    refused = client.post(f"/api/worlds/{wid}/characters", json={"name": "winifred"})
    assert refused.status_code == 409
    assert "a player character called \"Winifred\" in Saltmarch, a campaign of this world" \
        in refused.json()["detail"]


def test_a_new_campaign_pc_name_is_checked_before_any_campaign_exists(client):
    # The wizard's question, asked of the world because the campaign it would be
    # asked of does not exist yet. Answering from the PC route meant creating
    # the campaign first, and every refused name left one behind.
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Seraphine", "tags": []})

    def ask(name: str):
        return client.get(f"/api/worlds/{wid}/campaign-pc-name",
                          params={"name": name}).json()["refusal"]

    assert 'a character called "Mara"' in ask(" MARA ")
    assert 'a player character called "Seraphine"' in ask("Seraphine")
    # The persona's folded spelling is the one the PC route checks too (#65).
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara Vance"})
    assert ask("Mara\nVance") is not None
    assert ask("Winifred") is None
    assert ask("   ") is None
    assert client.get("/api/campaigns").json() == []


def test_a_new_campaign_does_not_inherit_a_sibling_campaigns_pc(client):
    # A campaign PC is visible in its own campaign alone, so a sibling's does
    # not make the name unavailable to the next campaign -- which is why the
    # check is the world's own rows and not `require_unique(scope="world")`.
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    client.post(f"/api/campaigns/{cid}/pcs", json={"name": "Winifred", "tags": []})
    answer = client.get(f"/api/worlds/{wid}/campaign-pc-name", params={"name": "Winifred"})
    assert answer.json() == {"refusal": None}
    second = client.post("/api/campaigns", json={"name": "Realm", "world": wid}).json()["id"]
    assert client.post(f"/api/campaigns/{second}/pcs",
                       json={"name": "Winifred", "tags": []}).status_code == 200


def test_the_new_campaign_check_refuses_an_unknown_world(client):
    assert client.get("/api/worlds/nowhere/campaign-pc-name",
                      params={"name": "Mara"}).status_code == 404
