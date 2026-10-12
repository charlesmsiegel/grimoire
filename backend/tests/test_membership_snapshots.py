"""Activation sees one captured group version, including visibility and body."""

import pytest

from grimoire.store import campaigns, group_refs, membership, overlay, worlds
from tests import test_membership_cache as cache_tests


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, overlay.croot_of(cid), worlds.world_root(wid)


def test_group_snapshot_couples_members_secrecy_and_body(case, monkeypatch):
    cid, _, root = case
    cache_tests._write_group(root, members="characters:mara", leader="", secrecy="gm-only", body="Mara keeps the ledger.")
    derive = membership._derive
    changed = []

    def interleaved(capture):
        result = derive(capture)
        if not changed:
            changed.append(1)
            cache_tests._write_group(root, members="", leader="", secrecy="public", body="Winifred keeps the ledger.")
        return result

    monkeypatch.setattr(membership, "_derive", interleaved)
    snapshot = membership.campaign_snapshot(cid)
    record = snapshot.records[0]
    roster = snapshot.index.groups["groups:salt-circle"]
    assert (record.body, record.meta["secrecy"], roster.secrecy, roster.members) == (
        "Mara keeps the ledger.", "gm-only", "gm-only", ("characters:mara",))
    assert "layer" not in record.meta
    fresh = membership.campaign_snapshot(cid)
    assert (fresh.records[0].body, fresh.index.groups["groups:salt-circle"].members) == ("Winifred keeps the ledger.", ())


def test_group_snapshot_deep_copies_companions_on_miss_and_hit(case):
    cid, croot, root = case
    cache_tests._write_group(root, leader="", body="Mara keeps the ledger.")
    cache_tests._age_tree(croot)
    cache_tests._age_tree(root)
    for _ in range(2):
        snapshot = membership.campaign_snapshot(cid)
        snapshot.records[0].meta["secrecy"] = "gm-only"
        snapshot.records[0].body = "changed"
        snapshot.index.groups["groups:salt-circle"].members = ()
        fresh = membership.campaign_snapshot(cid)
        assert fresh.records[0].body == "Mara keeps the ledger."
        assert fresh.records[0].meta["secrecy"] == "public"
        assert fresh.index.groups["groups:salt-circle"].members == ("characters:mara", "pcs:winifred")


@pytest.mark.parametrize("filename", ["deleted.json", "detached.json"])
def test_group_snapshot_keeps_strict_warm_identity_preflight(case, filename):
    cid, croot, root = case
    cache_tests._write_group(root)
    membership.campaign_snapshot(cid)
    (croot / filename).write_bytes(b"{")
    with pytest.raises(group_refs.GroupIdentityConflict):
        membership.campaign_snapshot(cid)
