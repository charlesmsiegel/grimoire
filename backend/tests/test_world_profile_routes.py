"""The world profile over HTTP (#38): create with it, edit it partially, read
it back -- and the name-only bodies every older client sends still work."""


def test_a_name_only_create_and_rename_still_work(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    w = client.get(f"/api/worlds/{wid}").json()
    assert (w["meta"]["genre"], w["meta"]["tone"], w["meta"]["themes"], w["body"]) == ("", "", [], "")
    r = client.put(f"/api/worlds/{wid}", json={"name": "Saltmarch"})
    assert r.json() == {"id": wid, "name": "Saltmarch"}


def test_create_with_a_profile_and_edit_it(client):
    wid = client.post("/api/worlds", json={
        "name": "Realm", "genre": "Coastal gothic", "tone": "Wry dread",
        "themes": ["salt", "debt"], "description": "A drowned coast."}).json()["id"]
    w = client.get(f"/api/worlds/{wid}").json()
    assert w["meta"]["genre"] == "Coastal gothic" and w["meta"]["themes"] == ["salt", "debt"]
    assert w["body"].strip() == "A drowned coast."
    assert client.get("/api/worlds").json()[0]["genre"] == "Coastal gothic"

    # partial: only the tone moves, and the name is read back, not echoed
    r = client.put(f"/api/worlds/{wid}", json={"tone": "Bright"})
    assert r.json() == {"id": wid, "name": "Realm"}
    w = client.get(f"/api/worlds/{wid}").json()
    assert (w["meta"]["genre"], w["meta"]["tone"]) == ("Coastal gothic", "Bright")
    assert w["body"].strip() == "A drowned coast."


def test_a_blank_name_is_refused_and_a_missing_world_is_404(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    assert client.put(f"/api/worlds/{wid}", json={"name": "  "}).status_code == 400
    assert client.put("/api/worlds/nowhere", json={"genre": "x"}).status_code == 404
