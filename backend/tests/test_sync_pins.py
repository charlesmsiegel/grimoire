"""Per-ref pins and the composition overview (#71).

Two properties carry the whole design and both are pinned here: a pin is a
PURE FILTER over the sync engine (nothing touches sync.md or a base hash, so
unpinning restores exactly the offer that was waiting), and the composition
view reports the two rows `/incoming` and `/diverged` cannot -- a materialized
ref with nothing pending, and an actor edited here but never version-locked.
"""

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, entities, overlay, sync, worlds


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "locations", "Seraphine", "v1")
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def _actor_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young")
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, char_id


def _rows(cid):
    return {(r["ref"]["kind"], r["ref"]["id"]): r for r in sync.composition(cid)}


# ---- pins are a pure filter ------------------------------------------------

def test_a_pinned_ref_is_not_offered_and_unpinning_restores_the_offer(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    assert [i["status"] for i in sync.incoming(cid)] == ["update"]

    sync.set_pin(cid, "locations", "seraphine", True)
    assert sync.incoming(cid) == []

    sync.set_pin(cid, "locations", "seraphine", False)
    pend = sync.incoming(cid)
    assert [i["status"] for i in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "v2"  # the same offer, untouched


def test_accept_and_reject_never_advance_a_pinned_ref(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    base = campaigns.read_manifest(cid)["locations/seraphine"]
    sync.set_pin(cid, "locations", "seraphine", True)

    # Refs arrive from the request body, not from `incoming`, so a stale
    # submission is exactly what these two must shrug off.
    sync.accept(cid, [{"kind": "locations", "id": "seraphine"}])
    assert campaigns.read_manifest(cid)["locations/seraphine"] == base
    assert overlay.read_entity(cid, "locations", "seraphine")["body"].strip() == "v1"
    sync.reject(cid, [{"kind": "locations", "id": "seraphine"}])
    assert campaigns.read_manifest(cid)["locations/seraphine"] == base

    sync.set_pin(cid, "locations", "seraphine", False)
    sync.accept(cid, [{"kind": "locations", "id": "seraphine"}])
    assert overlay.read_entity(cid, "locations", "seraphine")["body"].strip() == "v2"


def test_a_damaged_pins_file_reads_as_no_pins(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    sync.set_pin(cid, "locations", "seraphine", True)
    (campaigns.campaign_root(cid) / "sync_pins.json").write_text("{not json", encoding="utf-8")
    # Fail-open: updates are offered again, rather than refs silently frozen.
    assert sync.pinned_refs(cid) == set()
    assert [i["status"] for i in sync.incoming(cid)] == ["update"]


# ---- the composition overview ----------------------------------------------

def test_composition_reports_the_quiescent_ref_incoming_cannot(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")
    assert sync.incoming(cid) == [] and sync.diverged(cid) == []
    rows = _rows(cid)
    assert rows[("locations", "seraphine")]["state"] == "insync"
    assert rows[("locations", "seraphine")]["name"] == "Seraphine"
    assert rows[("locations", "seraphine")]["pinned"] is False
    assert rows[("locations", "seraphine")]["lock"] is None


def test_composition_states_follow_the_three_hash_comparison(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    overlay.materialize_entity(cid, "locations", "seraphine")

    entities.update_entity(wroot, "locations", "seraphine", body="world-edit")
    assert _rows(cid)[("locations", "seraphine")]["state"] == "update"

    overlay.update_entity(cid, "locations", "seraphine", body="my-edit")
    assert _rows(cid)[("locations", "seraphine")]["state"] == "conflict"

    sync.reject(cid, [{"kind": "locations", "id": "seraphine"}])  # keep mine
    assert _rows(cid)[("locations", "seraphine")]["state"] == "diverged"


def test_composition_carries_the_pin_beside_the_state_it_hides(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    overlay.materialize_entity(cid, "locations", "seraphine")
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    sync.set_pin(cid, "locations", "seraphine", True)
    row = _rows(cid)[("locations", "seraphine")]
    # `incoming` hides the update; the overview still says what the pin holds off.
    assert (row["state"], row["pinned"]) == ("update", True)


def test_composition_reports_the_unlocked_actor_diverged_covers_not(monkeypatch, tmp_path):
    _wid, _wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    overlay.materialize_actor(cid, "characters", char_id)
    characters.update_version(croot, char_id, "young", characters.blank_card("C-Mara"))
    assert sync.diverged(cid) == []  # flat records only, by design
    row = _rows(cid)[("characters", char_id)]
    assert row["state"] == "diverged"
    assert row["lock"] is None


def test_composition_reports_a_version_locked_actor_with_its_lock(monkeypatch, tmp_path):
    _wid, _wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    row = _rows(cid)[("characters", char_id)]
    assert row["state"] == "insync"
    assert row["lock"]["version"] == "young"
    assert row["name"] == "Mara"


# ---- the routes ------------------------------------------------------------

def _campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    client.post(f"/api/worlds/{wid}/locations", json={"name": "Quay", "body": "v1"})
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    # Materialize by editing at campaign scope, then put the copy back in step
    # with the world so the ref starts insync.
    client.put(f"/api/campaigns/{cid}/locations/quay", json={"name": "Quay", "body": "v1"})
    return wid, cid


def test_composition_route_serves_rows_and_pin_toggles_the_offer(client):
    wid, cid = _campaign(client)
    client.put(f"/api/worlds/{wid}/locations/quay", json={"name": "Quay", "body": "v2"})
    assert len(client.get(f"/api/campaigns/{cid}/incoming").json()) == 1

    rows = client.get(f"/api/campaigns/{cid}/composition").json()["rows"]
    assert [(r["ref"]["id"], r["state"], r["pinned"]) for r in rows] \
        == [("quay", "update", False)]

    pin = client.put(f"/api/campaigns/{cid}/composition/pins",
                     json={"ref": {"kind": "locations", "id": "quay"}, "pinned": True})
    assert pin.json() == {"pinned": ["locations/quay"]}
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    rows = client.get(f"/api/campaigns/{cid}/composition").json()["rows"]
    assert [(r["state"], r["pinned"]) for r in rows] == [("update", True)]

    client.put(f"/api/campaigns/{cid}/composition/pins",
               json={"ref": {"kind": "locations", "id": "quay"}, "pinned": False})
    assert len(client.get(f"/api/campaigns/{cid}/incoming").json()) == 1


def test_pinning_a_ref_outside_the_composition_is_refused(client):
    _wid, cid = _campaign(client)
    r = client.put(f"/api/campaigns/{cid}/composition/pins",
                   json={"ref": {"kind": "locations", "id": "nowhere"}, "pinned": True})
    assert r.status_code == 404
    # Unpinning is exempt: it only removes, and the ref may have left the
    # composition since it was pinned.
    r = client.put(f"/api/campaigns/{cid}/composition/pins",
                   json={"ref": {"kind": "locations", "id": "nowhere"}, "pinned": False})
    assert r.status_code == 200


def test_composition_routes_404_an_unknown_campaign(client):
    assert client.get("/api/campaigns/nope/composition").status_code == 404
    r = client.put("/api/campaigns/nope/composition/pins",
                   json={"ref": {"kind": "locations", "id": "x"}, "pinned": True})
    assert r.status_code == 404
