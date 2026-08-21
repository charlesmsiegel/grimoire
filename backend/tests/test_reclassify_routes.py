"""The reclassify surface (#119): `POST /{scope}/{id}/{kind}/{eid}/reclassify`."""


def _world(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore",
                json={"name": "Tidewatch", "body": "A stretch of grey coast."})
    return wid


def _campaign(client, wid):
    return client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]


def test_world_reclassify_moves_the_record(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify", json={"to": "locations"})
    assert r.status_code == 200
    assert r.json() == {"id": "tidewatch", "campaigns": []}
    assert client.get(f"/api/worlds/{wid}/lore/tidewatch").status_code == 404
    got = client.get(f"/api/worlds/{wid}/locations/tidewatch").json()
    assert got["body"].strip() == "A stretch of grey coast."
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/lore").json()] == []
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == ["tidewatch"]


def test_world_reclassify_names_the_campaigns_it_swept(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    assert client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify",
                       json={"to": "locations"}).json() == {"id": "tidewatch",
                                                            "campaigns": [cid]}
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []


def test_campaign_reclassify_moves_only_this_campaigns_copy(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    r = client.post(f"/api/campaigns/{cid}/lore/tidewatch/reclassify", json={"to": "items"})
    assert r.status_code == 200
    assert r.json() == {"id": "tidewatch"}
    assert client.get(f"/api/campaigns/{cid}/lore/tidewatch").status_code == 404
    assert client.get(f"/api/campaigns/{cid}/items/tidewatch").status_code == 200
    assert client.get(f"/api/worlds/{wid}/lore/tidewatch").status_code == 200


def test_reclassify_refuses_an_actor_kind_and_says_why(client):
    wid = _world(client)
    for kind in ("characters", "pcs"):
        r = client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify", json={"to": kind})
        assert r.status_code == 400
        # not "no such kind": an actor IS a kind, it is just a different shape
        assert "conversion rather than a move" in r.json()["detail"]
    assert client.get(f"/api/worlds/{wid}/lore/tidewatch").status_code == 200


def test_reclassify_refuses_a_blank_and_a_nonsense_destination(client):
    wid = _world(client)
    assert client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify",
                       json={"to": "  "}).status_code == 400
    assert client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify",
                       json={"to": "weapons"}).status_code == 400


def test_reclassify_refuses_the_kind_it_already_has(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify", json={"to": "lore"})
    assert r.status_code == 400
    assert r.json()["detail"] == "already a lore record"


def test_reclassify_404s_for_an_unknown_kind_world_or_record(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    assert client.post(f"/api/worlds/{wid}/weapons/tidewatch/reclassify",
                       json={"to": "lore"}).status_code == 404
    assert client.post(f"/api/worlds/{wid}/lore/nobody/reclassify",
                       json={"to": "locations"}).status_code == 404
    assert client.post("/api/worlds/nowhere/lore/tidewatch/reclassify",
                       json={"to": "locations"}).status_code == 404
    assert client.post(f"/api/campaigns/{cid}/lore/nobody/reclassify",
                       json={"to": "locations"}).status_code == 404
    assert client.post("/api/campaigns/nowhere/lore/tidewatch/reclassify",
                       json={"to": "locations"}).status_code == 404


def test_reclassify_honours_the_rev_precondition(client):
    wid = _world(client)
    rev = client.get(f"/api/worlds/{wid}/lore/tidewatch").json()["rev"]
    client.put(f"/api/worlds/{wid}/lore/tidewatch", json={"body": "somebody else wrote this"})
    stale = client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify",
                        json={"to": "locations", "rev": rev})
    assert stale.status_code == 409
    assert client.get(f"/api/worlds/{wid}/lore/tidewatch").status_code == 200
    fresh = client.get(f"/api/worlds/{wid}/lore/tidewatch").json()["rev"]
    assert client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify",
                       json={"to": "locations", "rev": fresh}).status_code == 200


def test_campaign_reclassify_honours_the_rev_of_the_layer_that_answered(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    # the campaign inherits, so its rev is the WORLD file's; a world edit under
    # the editor's feet is exactly the write the precondition refuses
    rev = client.get(f"/api/campaigns/{cid}/lore/tidewatch").json()["rev"]
    client.put(f"/api/worlds/{wid}/lore/tidewatch", json={"body": "moved on"})
    assert client.post(f"/api/campaigns/{cid}/lore/tidewatch/reclassify",
                       json={"to": "locations", "rev": rev}).status_code == 409


def test_reclassify_takes_the_images_with_it(client):
    wid = _world(client)
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    up = client.put(f"/api/worlds/{wid}/lore/tidewatch/images/avatar",
                    files={"file": ("a.png", png, "image/png")})
    assert up.status_code == 200
    client.post(f"/api/worlds/{wid}/lore/tidewatch/reclassify", json={"to": "locations"})
    served = client.get(f"/api/worlds/{wid}/locations/tidewatch/images/avatar")
    assert served.status_code == 200
    assert served.content == png
    assert client.get(f"/api/worlds/{wid}/lore/tidewatch/images/avatar").status_code == 404
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/locations").json()
            if e["has_image"]] == ["tidewatch"]
