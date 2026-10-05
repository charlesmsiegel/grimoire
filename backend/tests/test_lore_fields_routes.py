"""Activation fields on the generic entity routes: every world-info kind takes them."""


def _world(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _campaign(client, wid):
    return client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]


def test_world_lore_saves_activation_fields(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lore", json={
        "name": "Tidewatch", "body": "A stretch of grey coast.",
        "fields": {"priority": "250", "known_by": "characters:mara"}})
    assert r.status_code == 200, r.text
    meta = client.get(f"/api/worlds/{wid}/lore/{r.json()['id']}").json()["meta"]
    assert meta["priority"] == "250"
    assert meta["known_by"] == "characters:mara"


def test_campaign_item_saves_activation_fields(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    r = client.post(f"/api/campaigns/{cid}/items", json={
        "name": "Lantern", "body": "Burns blue.",
        "fields": {"priority": "250", "known_by": "characters:mara"}})
    assert r.status_code == 200, r.text
    meta = client.get(f"/api/campaigns/{cid}/items/{r.json()['id']}").json()["meta"]
    assert meta["priority"] == "250"
    assert meta["known_by"] == "characters:mara"


def test_update_saves_activation_fields_alongside_schema_fields(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch"}).json()["id"]
    r = client.put(f"/api/worlds/{wid}/locations/{eid}", json={
        "fields": {"weather_zone": "coast", "sticky": "3", "key_logic": "and_all"}})
    assert r.status_code == 200, r.text
    meta = client.get(f"/api/worlds/{wid}/locations/{eid}").json()["meta"]
    assert (meta["weather_zone"], meta["sticky"], meta["key_logic"]) == ("coast", "3", "and_all")


def test_bad_activation_value_is_400(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/lore", json={"name": "Tidewatch"}).json()["id"]
    r = client.put(f"/api/worlds/{wid}/lore/{eid}", json={"fields": {"sticky": "51"}})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid values for lore: sticky"
    r = client.post(f"/api/worlds/{wid}/lore", json={
        "name": "Winifred", "fields": {"priority": "high", "key_logic": "maybe"}})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid values for lore: key_logic, priority"


def test_bad_activation_value_sits_beside_a_bad_schema_value(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch"}).json()["id"]
    r = client.put(f"/api/worlds/{wid}/locations/{eid}", json={
        "fields": {"climate": "not-a-climate", "sticky": "51"}})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid values for locations: climate, sticky"


def test_unknown_field_is_still_unknown(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lore", json={
        "name": "Tidewatch", "fields": {"priority": "5", "flavour": "salty"}})
    assert r.status_code == 400
    assert r.json()["detail"] == "unknown fields for lore: flavour"


def test_blank_clears_a_field(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/lore", json={
        "name": "Tidewatch", "fields": {"priority": "250"}}).json()["id"]
    assert client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]["priority"] == "250"
    r = client.put(f"/api/worlds/{wid}/lore/{eid}", json={"fields": {"priority": ""}})
    assert r.status_code == 200, r.text
    assert "priority" not in client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]
