import pytest

from grimoire.store import campaigns, scenes, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def test_create_list_and_read_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "My First Scene")
    assert sid.endswith("my-first-scene")
    metas = scenes.list_scenes(cid)
    assert len(metas) == 1 and metas[0]["id"] == sid and metas[0]["title"] == "My First Scene"
    assert scenes.read_scene(cid, sid)["messages"] == []


def test_append_and_parse_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Roundtrip")
    scenes.append_message(cid, sid, "user", "Describe the keeper.\n\n**Not a real marker** still mine.")
    scenes.append_message(cid, sid, "assistant", "She is older than the salt.")
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "Describe the keeper.\n\n**Not a real marker** still mine."},
        {"role": "assistant", "content": "She is older than the salt."},
    ]


def test_unknown_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, "nope")


def test_create_in_missing_campaign_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(campaigns.CampaignNotFound):
        scenes.create_scene("no-campaign", "X")


def test_rename_changes_id_keeps_order(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Old Title")
    scenes.append_message(cid, sid, "user", "keep me")
    before = scenes.list_scenes(cid)[0]["updated"]
    new_sid = scenes.rename_scene(cid, sid, "Shiny New Name")
    assert new_sid != sid and new_sid.endswith("shiny-new-name")
    metas = scenes.list_scenes(cid)
    assert len(metas) == 1 and metas[0]["id"] == new_sid and metas[0]["updated"] == before
    assert scenes.read_scene(cid, new_sid)["messages"] == [{"role": "user", "content": "keep me"}]
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, sid)


def test_rename_to_same_title_is_noop(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Same")
    scenes.append_message(cid, sid, "user", "hi")
    new_sid = scenes.rename_scene(cid, sid, "Same")
    assert new_sid == sid
    assert scenes.read_scene(cid, new_sid)["messages"] == [{"role": "user", "content": "hi"}]


def test_traversal_sid_is_rejected(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, "../../secret")


def test_delete_removes_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Doomed")
    scenes.delete_scene(cid, sid)
    assert scenes.list_scenes(cid) == []
    with pytest.raises(scenes.SceneNotFound):
        scenes.delete_scene(cid, sid)
