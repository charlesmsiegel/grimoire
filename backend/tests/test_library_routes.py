"""The HTTP surface of the library moves (#52, #53, #60).

`test_library_move.py` holds the store-level semantics; this file is about the
door: status codes, the machine-readable refusal codes the UI branches on, and
the route-ordering that lets a literal fifth segment through at all.
"""

def _world_and_campaign(client, *, name="Run"):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]
    return wid, cid


# ---- emergent characters (#60) --------------------------------------------

def test_a_campaign_can_create_a_character_of_its_own(client):
    _wid, cid = _world_and_campaign(client)

    r = client.post(f"/api/campaigns/{cid}/characters", json={"name": "Winifred"})

    assert r.status_code == 200
    assert r.json()["character"] == "winifred"


def test_an_emergent_character_is_in_the_campaign_roster(client):
    _wid, cid = _world_and_campaign(client)
    client.post(f"/api/campaigns/{cid}/characters", json={"name": "Winifred"})

    roster = client.get(f"/api/campaigns/{cid}/characters").json()

    assert [c["id"] for c in roster] == ["winifred"]


def test_an_emergent_character_is_not_in_the_world(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/campaigns/{cid}/characters", json={"name": "Winifred"})

    assert client.get(f"/api/worlds/{wid}/characters").json() == []


def test_an_emergent_character_never_surfaces_as_an_incoming_change(client):
    _wid, cid = _world_and_campaign(client)
    client.post(f"/api/campaigns/{cid}/characters", json={"name": "Winifred"})

    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []


# ---- promote (#52, #60) ---------------------------------------------------

def test_promote_publishes_a_campaign_local_entity(client):
    wid, cid = _world_and_campaign(client)
    eid = client.post(f"/api/campaigns/{cid}/locations",
                      json={"name": "Saltmarch", "body": "fog"}).json()["id"]

    r = client.post(f"/api/campaigns/{cid}/locations/{eid}/promote")

    assert r.status_code == 200
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == [eid]


def test_promote_publishes_an_emergent_character(client):
    wid, cid = _world_and_campaign(client)
    aid = client.post(f"/api/campaigns/{cid}/characters",
                      json={"name": "Winifred"}).json()["character"]

    r = client.post(f"/api/campaigns/{cid}/characters/{aid}/promote")

    assert r.status_code == 200
    assert [c["id"] for c in client.get(f"/api/worlds/{wid}/characters").json()] == [aid]


def test_promoting_a_record_the_library_already_has_is_a_409(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    r = client.post(f"/api/campaigns/{cid}/locations/saltmarch/promote")

    assert r.status_code == 409
    assert r.json()["kind"] == "library_move_refused"


def test_promoting_something_that_does_not_exist_is_a_404(client):
    _wid, cid = _world_and_campaign(client)
    r = client.post(f"/api/campaigns/{cid}/locations/nowhere/promote")
    assert r.status_code == 404


def test_promote_on_an_unknown_kind_is_a_404(client):
    _wid, cid = _world_and_campaign(client)
    r = client.post(f"/api/campaigns/{cid}/sandwiches/blt/promote")
    assert r.status_code == 404


def test_promote_on_an_unknown_campaign_is_a_404(client):
    r = client.post("/api/campaigns/nope/locations/saltmarch/promote")
    assert r.status_code == 404


# ---- push (#53) -----------------------------------------------------------

def test_push_saves_a_campaign_override_back_to_the_library(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "my better wording"})

    r = client.post(f"/api/campaigns/{cid}/locations/saltmarch/push")

    assert r.status_code == 200
    assert client.get(f"/api/worlds/{wid}/locations/saltmarch"
                      ).json()["body"].strip() == "my better wording"


def test_a_pushed_override_stops_being_listed_as_diverged(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "mine"})
    assert len(client.get(f"/api/campaigns/{cid}/diverged").json()) == 1

    client.post(f"/api/campaigns/{cid}/locations/saltmarch/push")

    assert client.get(f"/api/campaigns/{cid}/diverged").json() == []


def test_diverged_names_the_record(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "mine"})

    assert client.get(f"/api/campaigns/{cid}/diverged").json() == [
        {"ref": {"kind": "locations", "id": "saltmarch"}, "name": "Saltmarch"}]


def test_a_push_conflict_is_a_409_the_ui_can_recognize(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "mine"})
    client.put(f"/api/worlds/{wid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "theirs"})

    r = client.post(f"/api/campaigns/{cid}/locations/saltmarch/push")

    assert r.status_code == 409
    assert r.json()["kind"] == "push_conflict"


def test_a_forced_push_goes_through(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "mine"})
    client.put(f"/api/worlds/{wid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "theirs"})

    r = client.post(f"/api/campaigns/{cid}/locations/saltmarch/push", json={"force": True})

    assert r.status_code == 200
    assert client.get(f"/api/worlds/{wid}/locations/saltmarch").json()["body"].strip() == "mine"


def test_pushing_a_campaign_local_record_is_refused(client):
    _wid, cid = _world_and_campaign(client)
    eid = client.post(f"/api/campaigns/{cid}/locations",
                      json={"name": "Saltmarch", "body": "mine"}).json()["id"]

    r = client.post(f"/api/campaigns/{cid}/locations/{eid}/push")

    assert r.status_code == 409
    assert r.json()["kind"] == "library_move_refused"


def test_push_with_no_body_at_all_is_an_unforced_push(client):
    # the frontend sends no body for the ordinary case; that must not 422
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "mine"})

    assert client.post(f"/api/campaigns/{cid}/locations/saltmarch/push").status_code == 200


# ---- dependents + demote (#52) --------------------------------------------

def test_dependents_reports_each_campaign_and_whether_it_holds_a_copy(client):
    wid, cid = _world_and_campaign(client)
    sibling = client.post("/api/campaigns",
                          json={"name": "Other", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})
    client.put(f"/api/campaigns/{cid}/locations/saltmarch",
               json={"name": "Saltmarch", "body": "mine"})

    got = {d["id"]: d for d in
           client.get(f"/api/worlds/{wid}/locations/saltmarch/dependents").json()}

    assert set(got) == {cid, sibling}
    assert got[cid]["has_copy"] is True
    assert got[sibling]["has_copy"] is False
    assert got[sibling]["name"] == "Other"


def test_dependents_of_a_missing_record_is_a_404(client):
    wid, _cid = _world_and_campaign(client)
    assert client.get(f"/api/worlds/{wid}/locations/nowhere/dependents").status_code == 404


def test_demote_copies_down_and_removes_the_library_record(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    r = client.post(f"/api/worlds/{wid}/locations/saltmarch/demote",
                    json={"copy_down": True})

    assert r.status_code == 200
    assert r.json()["copied_down"] == [cid]
    assert client.get(f"/api/worlds/{wid}/locations/saltmarch").status_code == 404
    assert client.get(f"/api/campaigns/{cid}/locations/saltmarch"
                      ).json()["body"].strip() == "v1"


def test_demote_without_copy_down_takes_the_record_away(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    client.post(f"/api/worlds/{wid}/locations/saltmarch/demote", json={"copy_down": False})

    assert client.get(f"/api/campaigns/{cid}/locations/saltmarch").status_code == 404


def test_demote_defaults_to_copying_down(client):
    # the destructive option must be the one you have to ask for
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    client.post(f"/api/worlds/{wid}/locations/saltmarch/demote")

    assert client.get(f"/api/campaigns/{cid}/locations/saltmarch").status_code == 200


def test_demoting_to_a_target_that_is_not_a_dependent_is_refused(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    r = client.post(f"/api/worlds/{wid}/locations/saltmarch/demote",
                    json={"copy_down": True, "target": "no-such-campaign"})

    assert r.status_code == 409
    assert r.json()["kind"] == "library_move_refused"
    # and the record is still there, in both places
    assert client.get(f"/api/worlds/{wid}/locations/saltmarch").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/locations/saltmarch").status_code == 200


def test_the_library_status_route_drives_the_editors_button(client):
    wid, cid = _world_and_campaign(client)
    eid = client.post(f"/api/campaigns/{cid}/locations",
                      json={"name": "Saltmarch", "body": "mine"}).json()["id"]

    local = client.get(f"/api/campaigns/{cid}/locations/{eid}/library").json()
    assert local == {"in_library": False, "diverged": False,
                     "can_promote": True, "can_push": False}

    client.post(f"/api/campaigns/{cid}/locations/{eid}/promote")

    assert client.get(f"/api/campaigns/{cid}/locations/{eid}/library").json() == {
        "in_library": True, "diverged": False, "can_promote": False, "can_push": False}

    client.put(f"/api/campaigns/{cid}/locations/{eid}",
               json={"name": "Saltmarch", "body": "edited here"})

    assert client.get(f"/api/campaigns/{cid}/locations/{eid}/library").json() == {
        "in_library": True, "diverged": True, "can_promote": False, "can_push": True}
    assert wid  # the world is the library these flags are about


def test_library_status_offers_nothing_for_an_inherited_record(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    assert client.get(f"/api/campaigns/{cid}/locations/saltmarch/library").json() == {
        "in_library": True, "diverged": False, "can_promote": False, "can_push": False}


def test_library_status_offers_nothing_for_an_actor_it_cannot_push(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Winifred"})

    status = client.get(f"/api/campaigns/{cid}/characters/winifred/library").json()

    assert status["can_push"] is False and status["can_promote"] is False
    assert wid


def test_demoting_an_actor_is_refused(client):
    wid, _cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Winifred"})

    r = client.post(f"/api/worlds/{wid}/characters/winifred/demote")

    assert r.status_code == 409
    assert r.json()["kind"] == "library_move_refused"


def test_a_demoted_record_leaves_the_campaign_with_nothing_incoming(client):
    wid, cid = _world_and_campaign(client)
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Saltmarch", "body": "v1"})

    client.post(f"/api/worlds/{wid}/locations/saltmarch/demote")

    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []


# ---- the round trip the issues describe ------------------------------------

def test_promote_then_edit_in_the_library_then_accept(client):
    """The whole loop: a location invented at the table becomes library content,
    the library edits it, and the campaign takes that edit like any other."""
    wid, cid = _world_and_campaign(client)
    eid = client.post(f"/api/campaigns/{cid}/locations",
                      json={"name": "Saltmarch", "body": "fog"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/locations/{eid}/promote")

    client.put(f"/api/worlds/{wid}/locations/{eid}",
               json={"name": "Saltmarch", "body": "fog, and a bell somewhere"})

    pending = client.get(f"/api/campaigns/{cid}/incoming").json()
    assert [p["status"] for p in pending] == ["update"]

    client.post(f"/api/campaigns/{cid}/incoming/accept",
                json={"refs": [{"kind": "locations", "id": eid}]})

    assert client.get(f"/api/campaigns/{cid}/locations/{eid}"
                      ).json()["body"].strip() == "fog, and a bell somewhere"
