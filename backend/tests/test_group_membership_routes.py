"""HTTP membership consent is explicit and shares the group revision hold."""

import json
import threading

import pytest

from grimoire.routes import entities as entity_routes
from grimoire.store import (
    campaigns,
    characters,
    entities,
    journal,
    locks,
    overlay,
    revision,
    undo,
    worlds,
)


def _setup(client, *, fields=None, kind="groups"):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    eid = entities.create_entity(worlds.world_root(wid), kind, "Salt Circle", fields=fields)
    return wid, cid, eid


def test_group_get_serializes_same_bytes_context(client):
    wid, cid, eid = _setup(client, fields={"members": "characters:mara, pcs:winifred", "leader": "characters:mara"})
    overlay.add_detached(cid, "characters/mara")
    text = (worlds.world_root(wid) / "groups" / f"{eid}.md").read_text(encoding="utf-8")
    result = client.get(f"/api/campaigns/{cid}/groups/{eid}")
    assert result.status_code == 200
    body = result.json()
    assert body["rev"] == entities.content_hash(text)
    assert "membership_context" not in body["meta"]
    assert body["membership_context"] == {
        "layer": "world", "members": ["pcs:winifred"], "leader": None,
        "filtered_members": ["characters:mara"], "filtered_leader": "characters:mara",
        "unresolved_members": ["characters:mara", "pcs:winifred"], "unresolved_leader": "characters:mara",
        "unresolved_leaders": ["characters:mara"]}


def test_owned_context_is_authored_even_with_unreadable_identity_ledger(client):
    _, cid, eid = _setup(client)
    root = campaigns.campaign_root(cid)
    entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    (root / "detached.json").write_bytes(b"{")
    result = client.get(f"/api/campaigns/{cid}/groups/{eid}")
    assert result.status_code == 200
    assert result.json()["membership_context"] == {
        "layer": "campaign", "members": ["characters:mara"], "leader": None,
        "filtered_members": [], "filtered_leader": None,
        "unresolved_members": [], "unresolved_leader": None, "unresolved_leaders": []}


@pytest.mark.parametrize("kind,fields,binding,status", [
    ("items", None, {"field": "members", "actor": "characters:mara"}, 400),
    ("groups", None, {"field": "members", "actor": "characters:mara"}, 400),
    ("groups", {"members": ""}, {"field": "leader", "actor": "characters:mara"}, 400),
    ("groups", {"members": ""}, {"field": "members", "actor": "locations:the-counting-house"}, 400),
    ("groups", {"members": ""}, {"field": "members", "actor": "characters:../mara"}, 400),
    ("groups", {"members": ""}, {"field": "headquarters", "actor": "characters:mara"}, 422)])
def test_bindings_require_actor_group_and_submitted_field(client, kind, fields, binding, status):
    wid, cid, eid = _setup(client, kind=kind)
    root = campaigns.campaign_root(cid)
    token = revision.current(cid)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    original = (worlds.world_root(wid) / kind / f"{eid}.md").read_bytes()
    result = client.put(f"/api/campaigns/{cid}/{kind}/{eid}", json={
        "body": "Winifred keeps the roster.", "fields": fields, "group_ref_bindings": [binding]})
    assert result.status_code == status
    assert revision.current(cid) == token
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert (worlds.world_root(wid) / kind / f"{eid}.md").read_bytes() == original


def test_missing_bindings_returns_structured_identity_conflict_before_copy(client):
    _, cid, eid = _setup(client, fields={"members": "characters:mara"})
    result = client.put(f"/api/campaigns/{cid}/groups/{eid}", json={"body": "Winifred keeps the roster."})
    assert result.status_code == 409
    payload = result.json()
    assert payload["kind"] == "group_identity_conflict"
    assert payload["message"] == payload["detail"]
    assert "Review" in payload["message"]
    assert payload["slots"] == [{"field": "members", "actor": "characters:mara"}]
    assert overlay.record_text(cid, "groups", eid) is None


def test_duplicate_exact_bindings_allow_only_that_slot(client):
    _, cid, eid = _setup(client, fields={"members": "characters:mara"})
    binding = {"field": "members", "actor": "characters:mara"}
    result = client.put(f"/api/campaigns/{cid}/groups/{eid}", json={
        "fields": {"members": "characters:mara"}, "group_ref_bindings": [binding, binding]})
    assert result.status_code == 200
    assert overlay.record_text(cid, "groups", eid) is not None
    got = client.get(f"/api/campaigns/{cid}/groups/{eid}").json()["membership_context"]
    assert got["layer"] == "campaign" and got["unresolved_members"] == []


def test_group_put_keeps_revision_check_through_write_in_one_hold(client, monkeypatch):
    _, cid, eid = _setup(client)
    url = f"/api/campaigns/{cid}/groups/{eid}"
    rev = client.get(url).json()["rev"]
    paused, release, started, second_checked = (threading.Event() for _ in range(4))
    original = entity_routes._fresh_or_409
    holds, results, errors = [], [], []

    def put(text, second=False):
        if second:
            started.set()
        try:
            results.append(client.put(url, json={"body": text, "rev": rev}))
        except BaseException as exc:  # noqa: BLE001 - propagate request-thread failures to the test thread
            errors.append(exc)

    # FastAPI dispatches each route in its own worker, so identify the first
    # checked request by arrival rather than its TestClient caller thread name.
    calls = []

    def by_arrival(expected, current):
        calls.append(current)
        holds.append(locks.holds_campaign(cid))
        if len(calls) == 1:
            paused.set()
            assert release.wait(5)
        else:
            second_checked.set()
        return original(expected, current)

    monkeypatch.setattr(entity_routes, "_fresh_or_409", by_arrival)
    first = threading.Thread(target=put, args=("Mara keeps the roster.",), name="first-put")
    second = threading.Thread(target=put, args=("Winifred keeps the roster.", True), name="second-put")
    first.start()
    try:
        assert paused.wait(5)
        second.start()
        assert started.wait(5)
        assert not second_checked.wait(0.1), "second PUT checked while first held its revision"
    finally:
        release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert holds == [True, True]
    assert sorted(r.status_code for r in results) == [200, 409]
    rejected = next(r for r in results if r.status_code == 409)
    assert rejected.json()["kind"] == "stale_record"
    assert overlay.read_entity(cid, "groups", eid)["body"].strip() == "Mara keeps the roster."



def test_world_requests_cannot_supply_campaign_binding_consent(client):
    wid, _, eid = _setup(client)
    result = client.put(f"/api/worlds/{wid}/groups/{eid}", json={
        "fields": {"members": ""},
        "group_ref_bindings": [{"field": "members", "actor": "characters:mara"}]})
    assert result.status_code == 400



def _members_row(cid):
    rows = [row for row in journal.read(cid) if row.get("field") == "members"]
    assert rows, "Members edit did not record its specialized journal row"
    return rows[-1]


def test_members_edit_journals_json_native_effective_state(client):
    _, cid, eid = _setup(client, fields={"members": "pcs:winifred"})
    url = f"/api/campaigns/{cid}/groups/{eid}"
    assert client.put(url, json={"fields": {"members": "pcs:winifred"},
        "group_ref_bindings": [{"field": "members", "actor": "pcs:winifred"}]}).status_code == 200
    row = _members_row(cid)
    target = row["undo"]["target"]
    assert target["w"] == "group_members" and target["kind"] == "groups" and target["id"] == eid
    assert target["bindings"] == ["pcs:winifred"]
    assert target["inherited"] == ["pcs:winifred"]
    assert target["actor_world_root"] is not None
    assert target["scope"] == {"store_root": str(campaigns.campaign_root(cid).parents[1].resolve()),
        "campaign_root": str(campaigns.campaign_root(cid).resolve()), "layer": "campaign"}
    assert row["undo"]["restore"] == {"raw": "pcs:winifred", "effective": ["pcs:winifred"], "unresolved": ["pcs:winifred"]}
    assert row["undo"]["expect"] == {"raw": "pcs:winifred", "effective": ["pcs:winifred"], "unresolved": []}
    persisted = json.loads((campaigns.campaign_root(cid) / "journal.json").read_text(encoding="utf-8"))
    loaded = next(e for e in persisted["entries"] if e["id"] == row["id"])
    assert undo.read_value(cid, loaded["undo"]["target"]) == loaded["undo"]["expect"]
    assert "Recorded members" in row["before"] and "Effective members" in row["before"]
    assert "Unresolved World references" in row["before"]
    assert "pcs:winifred" in row["after"]


def test_undo_revokes_identity_only_future_binding_and_redo_keeps_consent(client):
    _, cid, eid = _setup(client, fields={"members": "pcs:winifred"})
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={
        "fields": {"members": "pcs:winifred"},
        "group_ref_bindings": [{"field": "members", "actor": "pcs:winifred"}]}).status_code == 200
    row = _members_row(cid)
    reversed_row = undo.undo(cid, row["id"])
    value = undo.read_value(cid, reversed_row["undo"]["target"])
    assert value["effective"] == []
    assert "pcs:winifred" not in (value["raw"] or "")
    assert overlay.record_text(cid, "groups", eid) is not None
    overlay.create_pc(cid, "Winifred", [])
    assert undo.read_value(cid, reversed_row["undo"]["target"])["effective"] == []
    redone = undo.undo(cid, reversed_row["id"])
    assert undo.read_value(cid, redone["undo"]["target"])["effective"] == ["pcs:winifred"]
    for key in ("bindings", "inherited", "actor_world_root", "scope"):
        assert redone["undo"]["target"][key] == row["undo"]["target"][key]


def test_members_undo_preserves_legacy_leader_and_later_body(client):
    wid, cid, eid = _setup(client, fields={"members": "legacy note, characters:mara", "leader": "pcs:winifred"})
    characters.create_character(worlds.world_root(wid), "Mara")
    # First clear the unresolved Leader safely while changing Members. Leader
    # never lends consent to a Members restore; subsequent owned changes stay.
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={
        "fields": {"members": "", "leader": ""}}).status_code == 200
    row = _members_row(cid)
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={
        "body": "Winifred keeps the roster.", "fields": {"leader": "characters:mara"}}).status_code == 200
    undo.undo(cid, row["id"])
    record = overlay.read_entity(cid, "groups", eid)
    assert record["meta"]["members"] == "legacy note, characters:mara"
    assert record["meta"]["leader"] == "characters:mara"
    assert record["body"].strip() == "Winifred keeps the roster."


@pytest.mark.parametrize("change", ["missing", "detached", "world_rebound"])
def test_restore_revalidates_original_world_actor_without_minting_consent(client, change):
    wid, cid, eid = _setup(client, fields={"members": "characters:mara"})
    characters.create_character(worlds.world_root(wid), "Mara")
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={"fields": {"members": ""}}).status_code == 200
    row = _members_row(cid)
    root = campaigns.campaign_root(cid)
    if change == "missing":
        characters.delete_character(worlds.world_root(wid), "mara")
    elif change == "detached":
        overlay.add_detached(cid, "characters/mara")
    else:
        other = worlds.create_world("Realm")
        characters.create_character(worlds.world_root(other), "Mara")
        path = root / "campaign.md"
        path.write_text(path.read_text(encoding="utf-8").replace(f"world: {wid}", f"world: {other}"), encoding="utf-8")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(undo.UndoConflict, match="group editor"):
        undo.undo(cid, row["id"])
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("filename", ["detached.json", "deleted.json"])
def test_member_restore_refuses_unreadable_identity_even_with_original_bindings(client, filename):
    _, cid, eid = _setup(client, fields={"members": "pcs:winifred"})
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={
        "fields": {"members": "pcs:winifred"},
        "group_ref_bindings": [{"field": "members", "actor": "pcs:winifred"}]}).status_code == 200
    row = _members_row(cid)
    root = campaigns.campaign_root(cid)
    (root / filename).write_bytes(b"{")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, row["id"])
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_later_member_edit_conflicts_but_unchanged_members_do_not_journal(client):
    _, cid, eid = _setup(client)
    url = f"/api/campaigns/{cid}/groups/{eid}"
    assert client.put(url, json={"fields": {"members": "characters:mara"}}).status_code == 200
    row = _members_row(cid)
    assert row["undo"]["target"]["inherited"] == []
    assert client.put(url, json={"fields": {"members": "characters:mara"}}).status_code == 200
    assert len([e for e in journal.read(cid) if e.get("field") == "members"]) == 1
    assert client.put(url, json={"fields": {"members": "pcs:winifred"}}).status_code == 200
    before = overlay.record_text(cid, "groups", eid)
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, row["id"])
    assert overlay.record_text(cid, "groups", eid) == before


def test_members_target_requires_owned_scope_and_never_falls_back_to_world(client):
    _, cid, eid = _setup(client)
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={"fields": {"members": "characters:mara"}}).status_code == 200
    row = _members_row(cid)
    (campaigns.campaign_root(cid) / "groups" / f"{eid}.md").unlink()
    with pytest.raises(undo.UndoConflict):
        undo.read_value(cid, row["undo"]["target"])



def test_members_reversal_display_describes_actual_conservative_restore(client):
    _, cid, eid = _setup(client, fields={"members": "pcs:winifred"})
    assert client.put(f"/api/campaigns/{cid}/groups/{eid}", json={
        "fields": {"members": "pcs:winifred"},
        "group_ref_bindings": [{"field": "members", "actor": "pcs:winifred"}]}).status_code == 200
    row = _members_row(cid)
    reversed_row = undo.undo(cid, row["id"])
    assert "pcs:winifred" not in reversed_row["after"]
    assert "Recorded members: (none)" in reversed_row["after"]
    assert "Effective members: (none)" in reversed_row["after"]
    assert "Unresolved World references: (none)" in reversed_row["before"]
    redone = undo.undo(cid, reversed_row["id"])
    assert "pcs:winifred" not in redone["before"]
    assert "Recorded members: pcs:winifred" in redone["after"]
    assert "Effective members: pcs:winifred" in redone["after"]
