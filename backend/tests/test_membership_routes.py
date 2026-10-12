"""Membership has a typed read surface before generic entity catch-alls."""

import json

import pytest
from pydantic import BaseModel

from grimoire.main import app
from grimoire.routes import models
from grimoire.store import characters, entities, membership, overlay, pcs, worlds
from tests import test_membership_cache as cache_tests

GROUP = "groups:salt-circle"


def _setup(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    entities.create_entity(worlds.world_root(wid), "groups", "Salt Circle", fields={
        "members": "characters:mara, pcs:winifred", "leader": "characters:mara", "headquarters": "locations:saltmarch"})
    return wid, cid


@pytest.mark.parametrize("scope", ["worlds", "campaigns"])
def test_membership_response_models_and_role_lists(client, scope):
    wid, cid = _setup(client)
    sid = wid if scope == "worlds" else cid
    if scope == "campaigns":
        overlay.add_detached(cid, "characters/mara")
    result = client.get(f"/api/{scope}/{sid}/membership")
    assert result.status_code == 200
    body = result.json()
    assert set(body) == {"digest", "groups", "by_actor"}
    assert len(body["digest"]) == 64
    assert body["groups"] == [{"ref": GROUP, "name": "Salt Circle", "secrecy": "public", "group_type": "",
        "leader": "characters:mara" if scope == "worlds" else None,
        "members": ["characters:mara", "pcs:winifred"] if scope == "worlds" else ["pcs:winifred"],
        "headquarters": "locations:saltmarch"}]
    expected = {"pcs:winifred": [{"group": GROUP, "roles": ["member"]}]}
    if scope == "worlds":
        expected["characters:mara"] = [{"group": GROUP, "roles": ["leader", "member"]}]
    assert body["by_actor"] == expected
    filtered = client.get(f"/api/{scope}/{sid}/membership", params={"actor": "pcs:winifred"})
    assert filtered.status_code == 200
    assert filtered.json() == {"actor": "pcs:winifred", "groups": [
        {"ref": GROUP, "name": "Salt Circle", "secrecy": "public", "roles": ["member"]}]}


@pytest.mark.parametrize("actor", ["groups:salt-circle", "characters/mara", "characters:", "pcs:../winifred",
    "characters:mara, pcs:winifred", "characters:mara\nother", "Mara"])
def test_membership_route_rejects_malformed_actor(client, actor):
    wid, _ = _setup(client)
    response = client.get(f"/api/worlds/{wid}/membership", params={"actor": actor})
    assert response.status_code == 400


def test_membership_route_keeps_dangling_ref_without_actor_reads(client, monkeypatch):
    wid, cid = _setup(client)

    def forbidden(*args, **kwargs):
        pytest.fail("membership HTTP read loaded an actor or roster")

    monkeypatch.setattr(characters, "read_character", forbidden)
    monkeypatch.setattr(pcs, "read_pc", forbidden)
    monkeypatch.setattr(overlay, "character_roster", forbidden)
    monkeypatch.setattr(overlay, "pc_roster", forbidden)
    for scope, sid in (("worlds", wid), ("campaigns", cid)):
        result = client.get(f"/api/{scope}/{sid}/membership", params={"actor": "characters:mara"})
        assert result.status_code == 200
        assert result.json() == {"actor": "characters:mara", "groups": [
            {"ref": GROUP, "name": "Salt Circle", "secrecy": "public", "roles": ["leader", "member"]}]}
        result = client.get(f"/api/{scope}/{sid}/membership", params={"actor": "characters:seraphine"})
        assert result.status_code == 200 and result.json() == {"actor": "characters:seraphine", "groups": []}


@pytest.mark.parametrize("scope", ["worlds", "campaigns"])
def test_membership_unknown_scope_is_404(client, scope):
    assert client.get(f"/api/{scope}/missing/membership").status_code == 404


@pytest.mark.parametrize("filename", ["deleted.json", "detached.json"])
def test_membership_warm_ledger_refusal_is409(client, monkeypatch, filename):
    wid, cid = _setup(client)
    croot, wroot = overlay.croot_of(cid), worlds.world_root(wid)
    path = croot / filename
    path.write_text("[]", encoding="utf-8")
    cache_tests._age_tree(croot)
    cache_tests._age_tree(wroot)
    cache_tests._freeze_stats(monkeypatch, croot, wroot)
    cache_tests._age_clock(monkeypatch)
    assert client.get(f"/api/campaigns/{cid}/membership").status_code == 200
    assert membership._memo_pool
    path.write_text(json.dumps([None]), encoding="utf-8")
    result = client.get(f"/api/campaigns/{cid}/membership")
    assert result.status_code == 409
    assert result.json()["kind"] == "group_identity_conflict"
    assert result.json()["message"] == result.json()["detail"]
    assert client.get(f"/api/worlds/{wid}/membership").status_code == 200


def test_membership_plain_models_and_declared_response_schemas(client):
    wid, _ = _setup(client)
    for name in ("MembershipResponse", "ActorGroup", "ActorMembershipResponse"):
        cls = getattr(models, name, None)
        assert cls is not None and issubclass(cls, BaseModel)
    schema = app.openapi()
    for scope, parameter in (("worlds", "wid"), ("campaigns", "cid")):
        response = schema["paths"][f"/api/{scope}/{{{parameter}}}/membership"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert {entry["$ref"].rsplit("/", 1)[1] for entry in response["anyOf"]} == {"MembershipResponse", "ActorMembershipResponse"}
    assert set(schema["components"]["schemas"]["ActorGroup"]["properties"]) == {"ref", "name", "secrecy", "roles"}
    assert client.get(f"/api/worlds/{wid}/membership").status_code == 200
