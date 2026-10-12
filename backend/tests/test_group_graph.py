"""Group edges close over actors created by existing graph families."""

import json
from contextlib import contextmanager

import pytest

from grimoire.store import campaigns, group_refs, membership, relationships
from grimoire.store.continuity import graph
from tests.test_continuity_graph import _cast, _scenes
from tests.test_continuity_pressure import _actor, _campaign
from tests.test_membership_cache import _write_group


def test_existing_actor_closure_independent_roles_and_once_outside_hold(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (sid,) = _scenes(cid, "Saltmarch")
    _cast(cid, sid, ["characters/mara"])
    relationships.set_feeling(cid, "characters:seraphine", "characters:mara", 1, 1, 1, "")
    _write_group(campaigns.campaign_root(cid), members="characters:mara, pcs:winifred", leader="characters:seraphine", secrecy="gm-only")
    held = False
    calls = []
    real_hold = graph.locks.best_effort_campaign_lock
    real_read = membership.campaign_index

    @contextmanager
    def hold(*args, **kwargs):
        nonlocal held
        with real_hold(*args, **kwargs):
            held = True
            try:
                yield
            finally:
                held = False

    def read(scope):
        assert not held
        calls.append(scope)
        return real_read(scope)

    monkeypatch.setattr(graph.locks, "best_effort_campaign_lock", hold)
    monkeypatch.setattr(membership, "campaign_index", read)
    got = graph.build(cid)
    assert calls == [cid]
    by_id = {n["id"]: n for n in got["nodes"]}
    assert "pcs:winifred" not in by_id
    assert by_id["groups:salt-circle"]["secrecy"] == "gm-only"
    assert {(e["kind"], e["from"], e["to"]) for e in got["edges"] if e["kind"] in ("member_of", "leads")} == {
        ("member_of", "characters:mara", "groups:salt-circle"),
        ("leads", "characters:seraphine", "groups:salt-circle"),
    }


def test_dual_roles_birthday_only_actor_and_effective_overlay(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    mara = _actor(cid, "Mara", "1990-05-13")
    root = campaigns.campaign_root(cid)
    _write_group(root, members=f"characters:{mara}", leader=f"characters:{mara}")
    got = graph.build(cid)
    edges = [e for e in got["edges"] if e["to"] == "groups:salt-circle"]
    assert {e["kind"] for e in edges} == {"leads", "member_of"}
    assert len({e["id"] for e in edges}) == 2
    _write_group(campaigns.world_root_of(cid), members=f"characters:{mara}", leader=f"characters:{mara}")
    (root / "groups" / "salt-circle.md").unlink()
    (root / "detached.json").write_text(json.dumps([f"characters/{mara}"]), encoding="utf-8")
    assert all(n["kind"] != "group" for n in graph.build(cid)["nodes"])
    _write_group(root, members=f"characters:{mara}", leader="")
    assert any(n["kind"] == "group" for n in graph.build(cid)["nodes"])


def test_ordinary_group_failure_omits_only_groups(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (sid,) = _scenes(cid, "Saltmarch")

    def unreadable(_cid):
        raise PermissionError("group collection unavailable")

    monkeypatch.setattr(membership, "campaign_index", unreadable)
    got = graph.build(cid)
    assert got["omitted"] == ["groups"]
    assert f"scene:{sid}" in {n["id"] for n in got["nodes"]}


@pytest.mark.parametrize("ledger", ["detached.json", "deleted.json"])
@pytest.mark.parametrize("warm", [False, True])
def test_bad_group_identity_refuses_whole_payload(monkeypatch, tmp_path, ledger, warm):
    cid = _campaign(monkeypatch, tmp_path)
    if warm:
        graph.build(cid)
    (campaigns.campaign_root(cid) / ledger).write_text("{invalid", encoding="utf-8")
    with pytest.raises(group_refs.GroupIdentityConflict):
        graph.build(cid)
