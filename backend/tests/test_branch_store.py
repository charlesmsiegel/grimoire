"""Scene branching (play controls III) — branch groups, the derived closed
state, and `store/branch.py`.

A scene is *closed* when another member of its branch group is absorbed. That
is derived while listing rather than stored, so deleting or un-absorbing the
absorbed member reopens the rest with no bookkeeping — which is what most of
the store tests below pin.
"""

import pytest

from grimoire.store import campaigns, scenes, worlds


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _played(cid, title, posts=2):
    """A scene with a transcript, so a cut has something to take."""
    sid = scenes.create_scene(cid, title)
    for i in range(posts):
        scenes.append_message(cid, sid, "user", f"{title} post {i}")
    return sid


def _group(cid, *sids):
    g = scenes.ensure_identity(cid, sids[0])
    for s in sids:
        scenes.write.set_branch_keys(cid, s, g, of=None if s == sids[0] else g)
    return g


# --- branch groups and the derived closed state ------------------------------


def test_a_member_is_closed_while_another_member_is_absorbed(cid):
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    _group(cid, a, b)
    scenes.mark_absorbed(cid, a, "x", "y")
    rows = {r["id"]: r for r in scenes.list_scenes(cid)}
    assert rows[b]["closed_by"] == {"sid": a, "title": "Mara"}
    assert "closed_by" not in rows[a]
    assert scenes.read.closed_by(cid, b) == {"sid": a, "title": "Mara"}
    assert scenes.read.closed_by(cid, a) is None


def test_unabsorbing_or_deleting_the_absorbed_member_reopens_the_rest(cid):
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    _group(cid, a, b)
    scenes.mark_absorbed(cid, a, "x", "y")
    scenes.write.unmark_absorbed(cid, a)
    assert scenes.read.closed_by(cid, b) is None
    scenes.mark_absorbed(cid, a, "x", "y")
    assert scenes.read.closed_by(cid, b) is not None
    scenes.delete_scene(cid, a)
    assert scenes.read.closed_by(cid, b) is None


def test_the_source_needs_no_key_to_be_a_member(cid):
    """A group is `branch_group or identity`: the scene a branch was taken
    from carries no new key, and is still closed by its absorbed sibling."""
    a, b = _played(cid, "Mara"), _played(cid, "Winifred")
    g = scenes.ensure_identity(cid, a)
    scenes.write.set_branch_keys(cid, b, g, of=g)
    assert "branch_group" not in scenes.read_scene_meta(cid, a)
    scenes.mark_absorbed(cid, b, "x", "y")
    assert scenes.read.closed_by(cid, a) == {"sid": b, "title": "Winifred"}
    rows = {r["id"]: r for r in scenes.list_scenes(cid)}
    assert rows[a]["branch_group"] == rows[b]["branch_group"] == g


def test_a_scene_in_no_group_carries_no_branch_keys(cid):
    sid = _played(cid, "Mara")
    row = next(r for r in scenes.list_scenes(cid) if r["id"] == sid)
    assert not {"branch_group", "branch_of", "closed_by", "_identity"} & set(row)
    assert scenes.read.closed_by(cid, sid) is None


def test_closed_by_is_none_for_a_missing_scene_or_campaign(cid):
    assert scenes.read.closed_by(cid, "999--nobody") is None
    assert scenes.read.closed_by("no-such-campaign", "001--mara") is None


def test_setting_branch_keys_does_not_touch_updated(cid):
    sid = _played(cid, "Mara")
    before = scenes.read_scene_meta(cid, sid)["updated"]
    scenes.write.set_branch_keys(cid, sid, "a" * 32, of="b" * 32)
    meta = scenes.read_scene_meta(cid, sid)
    assert meta["updated"] == before
    assert (meta["branch_group"], meta["branch_of"]) == ("a" * 32, "b" * 32)


def test_setting_branch_keys_on_a_missing_scene_raises(cid):
    with pytest.raises(scenes.SceneNotFound):
        scenes.write.set_branch_keys(cid, "999--nobody", "a" * 32)
