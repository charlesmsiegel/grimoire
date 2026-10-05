"""Adopting imported SillyTavern settings: per entry and in bulk, world and campaign."""

import json

from grimoire.store import campaigns, entities, journal, lorebook, overlay, undo, worlds

STASH = {"sticky": 2, "cooldown": 3, "order": 900, "probability": 40}


def _world(client):
    return client.post("/api/worlds", json={"name": "Realm"}).json()["id"]


def _campaign(client, wid):
    return client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]


def _stashed(root, kind="lore", name="Saltmarch Charter", stash=STASH, **fields):
    raw = stash if isinstance(stash, str) else json.dumps(stash)
    return entities.create_entity(root, kind, name, body="A grey coast.",
                                  fields={"st_extensions": raw, **fields})


def test_preview_lists_pending_fields(client):
    wid = _world(client)
    eid = _stashed(worlds.world_root(wid))
    r = client.get(f"/api/worlds/{wid}/lore/{eid}/adopt-st")
    assert r.status_code == 200, r.text
    assert r.json() == {"fields": {"sticky": "2", "cooldown": "3", "priority": "900"},
                        "unmapped": ["probability"]}
    # a preview writes nothing
    assert "sticky" not in client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]


def test_campaign_preview_reads_an_inherited_record(client):
    wid = _world(client)
    eid = _stashed(worlds.world_root(wid))
    cid = _campaign(client, wid)
    r = client.get(f"/api/campaigns/{cid}/lore/{eid}/adopt-st")
    assert r.status_code == 200, r.text
    assert r.json()["fields"]["sticky"] == "2"
    # the campaign only reads the world's file, and says so
    assert r.json()["inherited"] is True


def test_campaign_preview_of_its_own_record_is_not_inherited(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    eid = _stashed(campaigns.campaign_root(cid))
    r = client.get(f"/api/campaigns/{cid}/lore/{eid}/adopt-st")
    assert r.status_code == 200, r.text
    assert r.json()["inherited"] is False
    assert r.json()["fields"]["sticky"] == "2"


def test_apply_writes_pending_and_keeps_existing(client):
    wid = _world(client)
    eid = _stashed(worlds.world_root(wid), priority="7")
    r = client.post(f"/api/worlds/{wid}/lore/{eid}/adopt-st")
    assert r.status_code == 200, r.text
    assert r.json() == {"applied": {"sticky": "2", "cooldown": "3"}}
    meta = client.get(f"/api/worlds/{wid}/lore/{eid}").json()["meta"]
    assert (meta["sticky"], meta["cooldown"], meta["priority"]) == ("2", "3", "7")
    # applied once, so a second apply has nothing left to write
    assert client.post(f"/api/worlds/{wid}/lore/{eid}/adopt-st").json() == {"applied": {}}


def test_apply_on_record_without_stash_is_noop_200(client):
    wid = _world(client)
    eid = entities.create_entity(worlds.world_root(wid), "lore", "Saltmarch Charter", body="x")
    r = client.post(f"/api/worlds/{wid}/lore/{eid}/adopt-st")
    assert r.status_code == 200
    assert r.json() == {"applied": {}}
    assert client.get(f"/api/worlds/{wid}/lore/{eid}/adopt-st").json() == {"fields": {}, "unmapped": []}


def test_unknown_kind_and_record_are_404(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    for base in (f"/api/worlds/{wid}", f"/api/campaigns/{cid}"):
        assert client.get(f"{base}/nonsense/x/adopt-st").status_code == 404
        assert client.post(f"{base}/lore/missing/adopt-st").status_code == 404
        assert client.get(f"{base}/lore/missing/adopt-st").status_code == 404


def test_campaign_apply_refuses_an_inherited_record(client):
    wid = _world(client)
    eid = _stashed(worlds.world_root(wid))
    cid = _campaign(client, wid)
    r = client.post(f"/api/campaigns/{cid}/lore/{eid}/adopt-st")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "inherited from the world; adopt it in the world"
    # nothing was materialized, so the record still follows its world, and
    # there is nothing in the journal to undo
    assert not overlay.has_own_copy(cid, "lore", eid)
    assert journal.read(cid) == []
    assert "sticky" not in entities.read_entity(worlds.world_root(wid), "lore", eid)["meta"]


def test_campaign_apply_journals_its_own_record(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    eid = _stashed(campaigns.campaign_root(cid))
    r = client.post(f"/api/campaigns/{cid}/lore/{eid}/adopt-st")
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["priority"] == "900"
    assert overlay.read_entity(cid, "lore", eid)["meta"]["sticky"] == "2"
    (entry,) = journal.read(cid)
    assert entry["label"] == "Adopted SillyTavern settings: Saltmarch Charter"
    assert entry["undo"]["target"] == {"w": "entity_fields", "kind": "lore", "id": eid,
                                       "fields": ["cooldown", "priority", "sticky"]}


def test_bulk_world_adopt_keeps_a_field_saved_after_the_listing(client, monkeypatch):
    """The bulk route lists every record, then adopts them one by one; a field an
    author saves in between is held by the record by the time it is written, and
    the pending set has to be worked out from that read, not the listing's."""
    wid = _world(client)
    root = worlds.world_root(wid)
    eid = _stashed(root)
    real = lorebook.unreadable_stash

    def edit_then_check(row):
        entities.update_entity(root, "lore", eid, fields={"priority": "7"})
        return real(row)

    monkeypatch.setattr(lorebook, "unreadable_stash", edit_then_check)
    out = client.post(f"/api/worlds/{wid}/adopt-st").json()
    assert out["applied"] == [{"kind": "lore", "id": eid,
                               "fields": {"sticky": "2", "cooldown": "3"}}]
    assert entities.read_entity(root, "lore", eid)["meta"]["priority"] == "7"


def test_bulk_world_apply_reports_applied_and_skipped(client):
    wid = _world(client)
    root = worlds.world_root(wid)
    good = _stashed(root, "lore", "Saltmarch Charter")
    lantern = _stashed(root, "items", "Mara's Lantern", {"sticky": 4})
    entities.create_entity(root, "lore", "Plain", body="no stash")
    bad = _stashed(root, "locations", "Saltmarch", "{not json")
    r = client.post(f"/api/worlds/{wid}/adopt-st")
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted((a["kind"], a["id"]) for a in out["applied"]) == [("items", lantern), ("lore", good)]
    assert {"kind": "lore", "id": good,
            "fields": {"sticky": "2", "cooldown": "3", "priority": "900"}} in out["applied"]
    assert out["skipped"] == [{"kind": "locations", "id": bad, "reason": "unreadable st_extensions"}]
    # the no-stash record is in neither list, and a rerun finds nothing pending
    again = client.post(f"/api/worlds/{wid}/adopt-st").json()
    assert again["applied"] == []
    assert len(again["skipped"]) == 1


def test_bulk_campaign_apply_ignores_inherited_world_records(client):
    wid = _world(client)
    inherited = _stashed(worlds.world_root(wid), "lore", "Saltmarch Charter")
    cid = _campaign(client, wid)
    own = _stashed(campaigns.campaign_root(cid), "items", "Mara's Lantern", {"sticky": 4})
    r = client.post(f"/api/campaigns/{cid}/adopt-st")
    assert r.status_code == 200, r.text
    assert r.json() == {"applied": [{"kind": "items", "id": own, "fields": {"sticky": "4"}}],
                        "skipped": []}
    # the inherited record was not materialized into the campaign
    assert not overlay.has_own_copy(cid, "lore", inherited)
    assert "sticky" not in overlay.read_entity(cid, "lore", inherited)["meta"]


def test_bulk_campaign_apply_journals_each_record_and_skips_garbled(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    croot = campaigns.campaign_root(cid)
    a = _stashed(croot, "lore", "Saltmarch Charter")
    b = _stashed(croot, "items", "Mara's Lantern", {"sticky": 4})
    bad = _stashed(croot, "lore", "Seraphine Ledger", "[1, 2]")
    out = client.post(f"/api/campaigns/{cid}/adopt-st").json()
    assert sorted(x["id"] for x in out["applied"]) == sorted([a, b])
    assert out["skipped"] == [{"kind": "lore", "id": bad, "reason": "unreadable st_extensions"}]
    entries = journal.read(cid)
    assert len(entries) == 2
    for entry in entries:
        undo.undo(cid, entry["id"])
    assert "sticky" not in overlay.read_entity(cid, "lore", a)["meta"]
    assert "sticky" not in overlay.read_entity(cid, "items", b)["meta"]


def test_adopt_is_undone_through_the_journal_route(client):
    wid = _world(client)
    cid = _campaign(client, wid)
    eid = _stashed(campaigns.campaign_root(cid))
    client.post(f"/api/campaigns/{cid}/lore/{eid}/adopt-st")
    (row,) = client.get(f"/api/campaigns/{cid}/journal").json()
    assert row["undoable"] and row["field"] == "activation"
    assert client.post(f"/api/campaigns/{cid}/journal/{row['id']}/undo").status_code == 200
    meta = client.get(f"/api/campaigns/{cid}/lore/{eid}").json()["meta"]
    assert not {"sticky", "cooldown", "priority"} & set(meta)
