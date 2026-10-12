"""Spec 07 S1/S3 audit: publication strictness is scoped to membership, the
incoming display stays fail-soft, every guarded Leader slot is reported, and
the 17.1 acceptance cases the first pass left out."""

import pytest

from grimoire.store import (
    campaigns,
    characters,
    entities,
    group_refs,
    journal,
    membership,
    overlay,
    reclassify,
    sync,
    undo,
    worlds,
)
from grimoire.store.campaigns import paths as campaigns_paths


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return {"wid": wid, "cid": cid, "world": worlds.world_root(wid),
            "campaign": campaigns.campaign_root(cid)}


def _files(*roots):
    return [{p.relative_to(r): p.read_bytes() for p in r.rglob("*") if p.is_file()} for r in roots]


def _raw_line(path, key):
    return next(line for line in path.read_bytes().splitlines() if line.startswith(key.encode() + b":"))


# ---- G1/B1: a record that publishes no membership takes no new ledger read ----

@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
@pytest.mark.parametrize("force", [False, True])
def test_push_of_a_location_without_actor_refs_survives_a_corrupt_ledger(case, filename, force):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "locations", "Saltmarch", body="Grey coast.")
    overlay.update_entity(cid, "locations", eid, body="Grey coast, now flooded.")
    (croot / filename).write_bytes(b"{")
    sync.push(cid, "locations", eid, force=force)
    assert entities.read_entity(wroot, "locations", eid)["body"].strip() == "Grey coast, now flooded."
    assert campaigns_paths.read_manifest(cid)[f"locations/{eid}"] == entities.entity_hash(wroot, "locations", eid)
    assert (croot / filename).read_bytes() == b"{"


def test_push_of_lore_with_only_legacy_member_text_survives_a_corrupt_ledger(case):
    # Invalid legacy tokens are ignored by the publication guard (5.5), so
    # they publish no identity and impose no strict ledger read either.
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(wroot, "lore", "Salt Circle", body="A roster.")
    overlay.update_entity(cid, "lore", eid, body="A revised roster.")
    path = croot / "lore" / f"{eid}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "name: Salt Circle", "name: Salt Circle\nmembers: Mara, Seraphine"), encoding="utf-8")
    (croot / "detached.json").write_bytes(b"[7]")
    sync.push(cid, "lore", eid, force=True)
    assert entities.read_entity(wroot, "lore", eid)["meta"]["members"] == "Mara, Seraphine"


@pytest.mark.parametrize("field", ["members", "leader"])
@pytest.mark.parametrize("force", [False, True])
def test_push_of_dormant_actor_refs_still_reads_the_ledger_strictly(case, field, force):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    characters.create_character(wroot, "Mara")
    eid = entities.create_entity(croot, "lore", "Salt Circle", fields={field: "characters:mara"})
    entities.create_entity(wroot, "lore", "Salt Circle", body="Mara keeps the roster.")
    campaigns_paths.write_manifest(cid, {f"lore/{eid}": "older common version"})
    (croot / "detached.json").write_bytes(b"{")
    before = _files(croot, wroot)
    with pytest.raises(group_refs.GroupIdentityConflict):
        sync.push(cid, "lore", eid, force=force)
    assert _files(croot, wroot) == before


def test_a_detached_location_is_still_refused_through_the_fail_soft_read(case):
    # #225's own check keeps its meaning on the fail-soft path.
    cid, wroot = case["cid"], case["world"]
    eid = entities.create_entity(wroot, "locations", "Saltmarch", body="Grey coast.")
    overlay.update_entity(cid, "locations", eid, body="Grey coast, now flooded.")
    overlay.add_detached(cid, f"locations/{eid}")
    with pytest.raises(sync.NotInLibraryError):
        sync.push(cid, "locations", eid, force=True)


# ---- G2: the refusal names its remedy ----

@pytest.mark.parametrize("operation", ["promote", "push"])
def test_publication_refusal_tells_the_user_how_to_resolve_it(case, operation):
    cid, croot, wroot = case["cid"], case["campaign"], case["world"]
    eid = entities.create_entity(croot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    if operation == "push":
        entities.create_entity(wroot, "groups", "Salt Circle", body="Mara keeps the roster.")
        campaigns_paths.write_manifest(cid, {f"groups/{eid}": "older common version"})
    with pytest.raises(sync.DanglingReferenceError) as refused:
        if operation == "promote":
            sync.promote(cid, "groups", eid)
        else:
            sync.push(cid, "groups", eid, force=True)
    message = str(refused.value)
    assert "characters:mara" in message
    assert "promote that actor" in message and "remove them from the group" in message


# ---- G4/B2: the incoming display is an ordinary, fail-soft read ----

def _pending_group(case):
    cid, wroot = case["cid"], case["world"]
    characters.create_character(wroot, "Mara")
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    overlay.materialize_entity(cid, "groups", eid)
    entities.update_entity(wroot, "groups", eid, body="The roster changed.")
    return eid


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
def test_incoming_with_actor_fields_survives_a_corrupt_ledger(case, filename):
    eid = _pending_group(case)
    (case["campaign"] / filename).write_bytes(b"{")
    items = sync.incoming(case["cid"])
    assert [item["ref"] for item in items] == [{"kind": "groups", "id": eid}]
    row = next(r for r in items[0]["world"]["fields"] if r["key"] == "members")
    assert row["value"] == "characters:mara" and row["shown"] == "Mara"
    assert (case["campaign"] / filename).read_bytes() == b"{"


def test_incoming_route_stays_200_with_a_corrupt_ledger(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara")
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    overlay.materialize_entity(cid, "groups", eid)
    entities.update_entity(wroot, "groups", eid, body="The roster changed.")
    (campaigns.campaign_root(cid) / "detached.json").write_bytes(b"{")
    result = client.get(f"/api/campaigns/{cid}/incoming")
    assert result.status_code == 200
    assert [item["ref"] for item in result.json()] == [{"kind": "groups", "id": eid}]


def test_incoming_still_marks_a_detached_stranger_when_the_ledger_reads(case):
    eid = _pending_group(case)
    overlay.materialize_actor(case["cid"], "characters", "mara")
    overlay.add_detached(case["cid"], "characters/mara")
    item = next(i for i in sync.incoming(case["cid"]) if i["ref"]["id"] == eid)
    row = next(r for r in item["world"]["fields"] if r["key"] == "members")
    assert row["shown"] == "Mara (this campaign's own; not the library's)"


# ---- G5/B3: every guarded Leader slot is reportable ----

def _legacy_leader_group(wroot, leader):
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"leader": "characters:seraphine"})
    path = wroot / "groups" / f"{eid}.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "leader: 'characters:seraphine'", f"leader: '{leader}'"), encoding="utf-8")
    return eid


def test_context_reports_a_dormant_unresolved_leader_token(case):
    cid, wroot = case["cid"], case["world"]
    characters.create_character(wroot, "Seraphine")
    eid = _legacy_leader_group(wroot, "characters:seraphine, characters:mara")
    _record, context = overlay.read_group_membership(cid, eid)
    assert context.leader == "characters:seraphine"
    assert context.unresolved_leader is None   # display's one Leader resolves
    assert context.unresolved_leaders == ("characters:mara",)
    # ...and that is exactly the slot the copy guard refuses.
    with pytest.raises(group_refs.GroupIdentityConflict) as refused:
        overlay.update_entity(cid, "groups", eid, body="A revised roster.")
    assert refused.value.slots == (("leader", "characters:mara"),)


def test_context_reports_every_unresolved_leader_token_in_raw_order(case):
    cid, wroot = case["cid"], case["world"]
    eid = _legacy_leader_group(wroot, "pcs:winifred, Mara, characters:mara, pcs:winifred")
    _record, context = overlay.read_group_membership(cid, eid)
    assert context.unresolved_leader == "pcs:winifred"
    assert context.unresolved_leaders == ("pcs:winifred", "characters:mara")
    guarded = {actor for field, actor in group_refs.group_slots(_record["meta"]) if field == "leader"}
    assert set(context.unresolved_leaders) == guarded


def test_owned_context_reports_no_unresolved_leaders(case):
    cid, croot = case["cid"], case["campaign"]
    eid = _legacy_leader_group(croot, "characters:seraphine, characters:mara")
    _record, context = overlay.read_group_membership(cid, eid)
    assert context.layer == "campaign" and context.unresolved_leaders == ()


def test_legacy_leader_route_reports_slot_and_choosing_one_resolves_it(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine")
    eid = _legacy_leader_group(wroot, "characters:seraphine, characters:mara")
    url = f"/api/campaigns/{cid}/groups/{eid}"
    got = client.get(url).json()["membership_context"]
    assert got["unresolved_leader"] is None and got["unresolved_leaders"] == ["characters:mara"]
    refused = client.put(url, json={"body": "A revised roster."})
    assert refused.status_code == 409
    assert refused.json()["slots"] == [{"field": "leader", "actor": "characters:mara"}]
    assert client.put(url, json={"body": "A revised roster.",
                                 "fields": {"leader": "characters:seraphine"}}).status_code == 200
    assert overlay.read_entity(cid, "groups", eid)["meta"]["leader"] == "characters:seraphine"


# ---- 17.1: route-level legacy Members, journal rows ----

def _route_group(client, *, members=None, owned=True):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    root = campaigns.campaign_root(cid) if owned else worlds.world_root(wid)
    eid = entities.create_entity(root, "groups", "Salt Circle", body="A roster.")
    if members is not None:
        path = root / "groups" / f"{eid}.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "name: Salt Circle", f"name: Salt Circle\nmembers: {members}"), encoding="utf-8")
    return wid, cid, eid


@pytest.mark.parametrize("owned", [True, False])
def test_legacy_members_survive_an_unrelated_body_save_byte_for_byte(client, owned):
    wid, cid, eid = _route_group(client, members="Mara, Seraphine", owned=owned)
    source = (campaigns.campaign_root(cid) if owned else worlds.world_root(wid)) / "groups" / f"{eid}.md"
    line = _raw_line(source, "members")
    result = client.put(f"/api/campaigns/{cid}/groups/{eid}", json={"body": "A revised roster."})
    assert result.status_code == 200
    saved = campaigns.campaign_root(cid) / "groups" / f"{eid}.md"
    assert _raw_line(saved, "members") == line == b"members: Mara, Seraphine"
    assert overlay.read_entity(cid, "groups", eid)["body"].strip() == "A revised roster."


@pytest.mark.parametrize("value", ["Mara, Seraphine", "Mara", "characters:mara, Seraphine"])
def test_setting_a_legacy_members_value_through_the_route_is_400(client, value):
    _, cid, eid = _route_group(client)
    path = campaigns.campaign_root(cid) / "groups" / f"{eid}.md"
    before = path.read_bytes()
    result = client.put(f"/api/campaigns/{cid}/groups/{eid}", json={"fields": {"members": value}})
    assert result.status_code == 400
    assert path.read_bytes() == before


def test_a_body_only_group_save_writes_no_members_journal_row(client):
    _, cid, eid = _route_group(client, members="characters:mara")
    result = client.put(f"/api/campaigns/{cid}/groups/{eid}", json={"body": "A revised roster."})
    assert result.status_code == 200
    assert [r for r in journal.read(cid) if r.get("field") == "members"] == []
    assert any(r.get("field") == "body" for r in journal.read(cid))


# ---- 17.1: reclassify round trip, the index, and Redo while lore ----

def test_reclassify_round_trip_leaves_members_and_returns_the_same_rosters(case):
    cid, croot = case["cid"], case["campaign"]
    eid = overlay.create_entity(cid, "groups", "Salt Circle")
    overlay.update_entity(cid, "groups", eid, fields={
        "members": "characters:mara, pcs:winifred", "leader": "characters:mara"})
    line = _raw_line(croot / "groups" / f"{eid}.md", "members")
    before = membership.campaign_index(cid)
    assert membership.members_of(before, f"groups:{eid}") == ("characters:mara", "pcs:winifred")
    assert membership.groups_for(before, "pcs:winifred")
    moved = reclassify.campaign_entity(cid, "groups", eid, "lore")
    assert _raw_line(croot / "lore" / f"{moved}.md", "members") == line
    while_lore = membership.campaign_index(cid)
    assert while_lore.groups == {} and while_lore.by_actor == {}
    assert membership.groups_for(while_lore, "characters:mara") == ()
    back = reclassify.campaign_entity(cid, "lore", moved, "groups")
    assert back == eid
    assert _raw_line(croot / "groups" / f"{back}.md", "members") == line
    after = membership.campaign_index(cid)
    assert after.groups == before.groups and after.by_actor == before.by_actor


def test_world_reclassify_round_trip_hides_and_returns_the_world_roster(case):
    wroot = case["world"]
    eid = entities.create_entity(wroot, "groups", "Salt Circle", fields={"members": "characters:mara"})
    before = membership.world_index(wroot)
    assert membership.members_of(before, f"groups:{eid}") == ("characters:mara",)
    moved = reclassify.world_entity(case["wid"], "groups", eid, "lore")["id"]
    assert membership.world_index(wroot).groups == {}
    assert entities.read_entity(wroot, "lore", moved)["meta"]["members"] == "characters:mara"
    reclassify.world_entity(case["wid"], "lore", moved, "groups")
    assert membership.world_index(wroot).by_actor == before.by_actor


def test_redo_refuses_while_the_original_is_lore_and_spares_the_replacement(case):
    cid, croot = case["cid"], case["campaign"]
    eid = entities.create_entity(croot, "groups", "Salt Circle")
    with undo.journalled_group_members(cid, eid, bindings=frozenset(), label="Salt Circle -- members"):
        overlay.update_entity(cid, "groups", eid, fields={"members": "characters:mara"})
    row = next(r for r in journal.read(cid) if r.get("field") == "members")
    reversed_row = undo.undo(cid, row["id"])
    assert "members" not in entities.read_entity(croot, "groups", eid)["meta"]
    moved = reclassify.campaign_entity(cid, "groups", eid, "lore")
    replacement = entities.create_entity(croot, "groups", "Salt Circle")
    assert replacement == eid
    before = _files(croot)
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, reversed_row["id"])
    assert _files(croot) == before
    # Back in groups (suffixed past the replacement), Redo follows the original only.
    back = reclassify.campaign_entity(cid, "lore", moved, "groups")
    assert back != replacement
    undo.undo(cid, reversed_row["id"])
    assert entities.read_entity(croot, "groups", back)["meta"]["members"] == "characters:mara"
    assert "members" not in entities.read_entity(croot, "groups", replacement)["meta"]


@pytest.mark.parametrize("kind,eid", [("groups", "nowhere"), ("spells", "salt-circle")])
def test_deleting_a_missing_world_record_is_404_before_any_dependent_preflight(client, kind, eid):
    """A dependent campaign's damaged identity ledger must not turn a missing
    record or an unknown kind into a membership refusal (Codex review, #507)."""
    from grimoire.store import campaigns as campaigns_store
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    (campaigns_store.campaign_root(cid) / "detached.json").write_text("{", encoding="utf-8")
    r = client.delete(f"/api/worlds/{wid}/{kind}/{eid}")
    assert r.status_code == 404, r.text
