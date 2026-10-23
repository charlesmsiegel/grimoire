"""Canonical edits and branches cannot hand observations to unrelated posts."""

import pytest

from grimoire import store
from grimoire.store import branch, fork
from grimoire.store.scene_history import projection, records


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = store.campaigns.create_campaign("Saltmarch", store.worlds.create_world("Realm"))
    sid = store.scenes.create_scene(cid, "Opening")
    root = store.campaigns.campaign_root(cid)
    a = store.entities.create_entity(root, "locations", "Harbour", "")
    b = store.entities.create_entity(root, "locations", "Market", "")
    store.scenes.set_location(cid, sid, a)
    store.scenes.append_message(cid, sid, "user", "First.")
    store.scenes.set_location(cid, sid, b)
    store.scenes.append_message(cid, sid, "user", "Later.")
    return cid, sid, a, b


def test_branch_retains_only_prefix_history(scene):
    cid, sid, a, _ = scene
    sibling = branch.branch_scene(cid, sid, 0)
    posts = store.scenes.read_scene(cid, sibling)["messages"]
    assert len(posts) == 1
    assert projection.at_post(cid, sibling, posts[0]["post_id"]).location_id == a
    assert projection.at_post(cid, sid, posts[0]["post_id"]).location_id == a


def test_cut_cannot_reassign_boundary_to_same_index(scene):
    cid, sid, a, _ = scene
    old = store.scenes.read_scene(cid, sid)["messages"][-1]["post_id"]
    store.scenes.delete_from(cid, sid, 1)
    store.scenes.append_message(cid, sid, "user", "Replacement.")
    new = store.scenes.read_scene(cid, sid)["messages"][-1]["post_id"]
    assert old != new
    with pytest.raises(ValueError):
        projection.at_post(cid, sid, old)
    assert projection.at_post(cid, sid, new).location_id == a


def test_deleted_scene_slug_reuse_has_no_history(scene):
    cid, sid, _, _ = scene
    old = store.scenes.read_scene(cid, sid)["messages"][0]["post_id"]
    store.scenes.delete_scene(cid, sid)
    replacement = store.scenes.create_scene(cid, "Opening")
    assert replacement == sid
    with pytest.raises(ValueError):
        projection.at_post(cid, replacement, old)


def test_fork_does_not_retain_discarded_future_state(scene):
    cid, sid, a, _ = scene
    result = fork.fork_campaign(cid, "Branch", from_scene=sid, from_index=0)
    target = result["id"]
    posts = store.scenes.read_scene(target, sid)["messages"]
    assert len(posts) == 1
    assert projection.at_post(target, sid, posts[0]["post_id"]).location_id == a


def test_checkpoint_failure_does_not_block_branch(scene, monkeypatch):
    cid, sid, _, _ = scene

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(records, "save_checkpoint", fail)
    sibling = branch.branch_scene(cid, sid, 0)
    post, = store.scenes.read_scene(cid, sibling)["messages"]
    assert post["content"] == "First."
    assert projection.at_post(cid, sibling, post["post_id"]).location_id is None
