"""Author's notes, the HTTP half (play controls V) -- `routes/authors_notes.py`."""

from urllib.parse import quote

import grimoire.store as store

NOTE = {"text": "Keep the storm audible in every scene.", "depth": 4, "every": 1}


def seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
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


def test_put_validates_ranges(client):
    cid, sid = seed(client)
    url = f"/api/campaigns/{cid}/authors-notes/campaign"
    for body, detail in (({**NOTE, "depth": 51}, "depth_out_of_range"),
                         ({**NOTE, "depth": -1}, "depth_out_of_range"),
                         ({**NOTE, "every": 0}, "every_out_of_range"),
                         ({**NOTE, "every": 51}, "every_out_of_range"),
                         ({**NOTE, "text": "x" * 2001}, "text_too_long")):
        r = client.put(url, json=body)
        assert r.status_code == 400, (body, r.text)
        assert r.json()["detail"] == detail
    assert client.put(url, json={**NOTE, "text": "x" * 2000}).status_code == 200
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/authors-note", json={**NOTE, "every": 0})
    assert r.status_code == 400


def test_put_campaign_and_clear(client):
    cid, _ = seed(client)
    r = client.put(f"/api/campaigns/{cid}/authors-notes/campaign", json=NOTE)
    assert r.status_code == 200, r.text
    assert r.json()["campaign"] == NOTE
    assert client.get(f"/api/campaigns/{cid}/authors-notes").json()["campaign"] == NOTE
    r = client.put(f"/api/campaigns/{cid}/authors-notes/campaign", json={"text": ""})
    assert r.json()["campaign"] is None
    assert client.get(f"/api/campaigns/{cid}/authors-notes").json() == {
        "campaign": None, "scenes": {}, "characters": {}}


def test_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nowhere/authors-notes").status_code == 404
    assert client.put("/api/campaigns/nowhere/authors-notes/campaign",
                      json=NOTE).status_code == 404


def test_character_ref_validated(client):
    cid, _ = seed(client)
    base = f"/api/campaigns/{cid}/authors-notes/characters/"
    r = client.put(base + quote("characters:mara", safe=""), json=NOTE)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/campaigns/{cid}/authors-notes").json()["characters"] == {
        "characters:mara": NOTE}
    r = client.put(base + quote("characters:nobody", safe=""), json=NOTE)
    assert r.status_code == 404 and r.json()["detail"] == "character_not_found"
    assert client.put(base + "mara", json=NOTE).status_code == 404


def test_scene_note_keyed_by_sid_in_get_and_survives_rename(client):
    cid, sid = seed(client)
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/authors-note", json=NOTE)
    assert r.status_code == 200, r.text
    assert r.json()["scenes"] == {sid: NOTE}
    new = store.scenes.rename_scene(cid, sid, "Winifred")
    assert new != sid
    assert client.get(f"/api/campaigns/{cid}/authors-notes").json()["scenes"] == {new: NOTE}
    # A note whose scene is gone is not reported.
    store.authors_notes.set_scene(cid, "f" * 32, NOTE)
    assert client.get(f"/api/campaigns/{cid}/authors-notes").json()["scenes"] == {new: NOTE}


def test_scene_note_unknown_scene_404(client):
    cid, _ = seed(client)
    r = client.put(f"/api/campaigns/{cid}/scenes/nope/authors-note", json=NOTE)
    assert r.status_code == 404
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/authors-notes/next").status_code == 404


def test_scene_note_on_a_closed_branch_is_refused(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Hello")
    branch = store.branch.branch_scene(cid, sid, 0)
    store.scenes.mark_absorbed(cid, sid, "x", "y")
    r = client.put(f"/api/campaigns/{cid}/scenes/{branch}/authors-note", json=NOTE)
    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "branch_closed"
    assert store.scenes.scene_identity(cid, branch) not in store.authors_notes.read(cid)["scenes"]


def test_next_reports_applies_and_character_condition(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "user", "Hello")     # turn 2 is next
    client.put(f"/api/campaigns/{cid}/authors-notes/campaign", json={**NOTE, "every": 2})
    client.put(f"/api/campaigns/{cid}/scenes/{sid}/authors-note", json={**NOTE, "every": 3})
    client.put(f"/api/campaigns/{cid}/authors-notes/characters/{quote('characters:mara', safe='')}",
               json=NOTE)
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/authors-notes/next")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn"] == 2
    by_level = {n["level"]: n for n in body["notes"]}
    assert by_level["campaign"]["applies"] is True
    assert by_level["scene"]["applies"] is False
    assert by_level["character"] == {"level": "character", "depth": 4, "every": 1,
                                     "applies": True, "ref": "characters:mara", "name": "Mara"}
    assert body["count"] == sum(1 for n in body["notes"] if n["applies"]) == 2


def test_next_with_no_notes(client):
    cid, sid = seed(client)
    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/authors-notes/next").json()
    assert body == {"turn": 1, "count": 0, "notes": []}
