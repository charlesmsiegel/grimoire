"""Unfinished membership targets follow original lifetimes, never replacement slugs."""

import json

import pytest

from grimoire.routes import entities as entity_routes
from grimoire.store import (
    campaigns,
    commits,
    entities,
    group_proposals,
    group_refs,
    locks,
    overlay,
    reclassify,
    sync,
    worlds,
)
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from tests.test_group_membership_store import case as membership_case

case = membership_case


def capture(case, *, owned=False):
    cid, root, world = case["cid"], case["campaign"], case["world"]
    identities = group_proposals.GroupIdentity(store=str(root.parent.parent.resolve()),
        campaign=str(root.resolve()), layer="campaign" if owned else "world",
        world=None if owned else str(world.resolve()))
    with locks.world_lifecycle_lock(root.parent.parent, world):
        lease = group_proposals.register(cid, root, {"groups:salt-circle": identities})["groups:salt-circle"]
    target = {"kind": "groups", "id": "salt-circle", "actor": "characters:mara",
        "token": lease.token, "source": group_proposals.identity_data(identities), "retired": False}
    commits.reserve(cid, "mara", progress={"membership_targets": {"0": target},
        "edits": {"0": {"state": "pending", "id": "membership:salt-circle:characters:mara"}}})
    return lease, identities


def target(case):
    return commits.lookup(case["cid"], "mara")["progress"]["membership_targets"]["0"]


def bind(case, wid):
    path = case["campaign"] / "campaign.md"
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta["world"] = wid
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def test_world_delete_retires_historical_source_after_binding_away_and_back(case):
    entities.create_entity(case["world"], "groups", "Salt Circle", body="The gate.")
    lease, source = capture(case)
    other = worlds.create_world("Saltmarch")
    bind(case, other)
    entity_routes.delete_world_entity(case["wid"], "groups", "salt-circle")
    assert target(case)["retired"] is True
    assert commits.lookup(case["cid"], "mara")["progress"]["edits"]["0"]["state"] == "pending"
    entities.create_entity(case["world"], "groups", "Salt Circle", body="Replacement.")
    bind(case, case["wid"])
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(case["cid"], case["campaign"], "groups:salt-circle", lease.token, source)


def test_unreadable_historical_bookkeeping_refuses_world_delete_before_effects(case):
    entities.create_entity(case["world"], "groups", "Salt Circle", body="The gate.")
    other = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run", other)
    path = campaigns.campaign_root(cid) / "membership_review_tokens.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(group_refs.GroupIdentityConflict):
        entity_routes.delete_world_entity(case["wid"], "groups", "salt-circle")
    assert entities.read_entity(case["world"], "groups", "salt-circle")["body"].strip() == "The gate."
    assert path.read_text(encoding="utf-8") == "{bad"


def test_owned_delete_retires_targets_before_same_slug_recreation(case):
    entities.create_entity(case["campaign"], "groups", "Salt Circle", body="The gate.")
    lease, source = capture(case, owned=True)
    overlay.delete_entity(case["cid"], "groups", "salt-circle")
    assert target(case)["retired"] is True
    entities.create_entity(case["campaign"], "groups", "Salt Circle", body="Replacement.")
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(case["cid"], case["campaign"], "groups:salt-circle", lease.token, source)


def test_owned_pending_target_moves_to_actual_suffix_without_changing_attempt(case):
    entities.create_entity(case["campaign"], "groups", "Salt Circle", body="The gate.")
    entities.create_entity(case["campaign"], "lore", "Salt Circle", body="The other entry.")
    capture(case, owned=True)
    before = commits.lookup(case["cid"], "mara")
    eid = reclassify.campaign_entity(case["cid"], "groups", "salt-circle", "lore")
    assert eid == "salt-circle-2"
    moved = target(case)
    assert (moved["kind"], moved["id"]) == ("lore", eid)
    assert "relocating" not in moved and moved["retired"] is False
    assert commits.lookup(case["cid"], "mara")["fingerprint"] == before["fingerprint"]
    assert commits.lookup(case["cid"], "mara")["progress"]["edits"] == before["progress"]["edits"]


def test_world_pending_target_follows_move_out_and_actual_suffixed_return(case):
    entities.create_entity(case["world"], "groups", "Salt Circle", body="The gate.")
    capture(case)
    first = reclassify.world_entity(case["wid"], "groups", "salt-circle", "lore")
    assert target(case)["kind"] == "lore"
    entities.create_entity(case["world"], "groups", "Salt Circle", body="Replacement.")
    second = reclassify.world_entity(case["wid"], "lore", first["id"], "groups")
    assert second["id"] == "salt-circle-2"
    assert (target(case)["kind"], target(case)["id"]) == ("groups", second["id"])
    assert target(case)["retired"] is False


def test_trusted_demote_copy_keeps_nonce_and_owned_pending_target(case):
    entities.create_entity(case["world"], "groups", "Salt Circle", body="The gate.")
    lease, source = capture(case)
    sync.demote(case["wid"], "groups", "salt-circle", copy_down=True)
    moved = target(case)
    assert moved["retired"] is False and moved["copy_receipt"]["token"] == lease.token
    owned = group_proposals.GroupIdentity(store=source.store, campaign=source.campaign, layer="campaign", world=None)
    assert group_proposals.validate(case["cid"], case["campaign"], "groups:salt-circle", lease.token, owned).copied_from == source
    assert overlay.has_own_copy(case["cid"], "groups", "salt-circle")


def test_corrupt_unfinished_source_metadata_refuses_owned_delete_before_effects(case):
    entities.create_entity(case["campaign"], "groups", "Salt Circle", body="The gate.")
    capture(case, owned=True)
    path = case["campaign"] / "commits.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["tokens"]["mara"]["progress"]["membership_targets"]["0"]["source"] = None
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.delete_entity(case["cid"], "groups", "salt-circle")
    assert path.read_bytes() == before
    assert overlay.has_own_copy(case["cid"], "groups", "salt-circle")
