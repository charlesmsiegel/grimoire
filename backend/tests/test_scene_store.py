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


def test_set_location_first_is_silent_then_move_announces(monkeypatch, tmp_path):
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    a = entities.create_entity(croot, "locations", "Salt Cathedral", "A drowned basilica.")
    b = entities.create_entity(croot, "locations", "Drowned Market", "Stalls in the shallows.")
    sid = scenes.create_scene(cid, "S")
    # first set: silent
    assert scenes.set_location(cid, sid, a) == {"moved": False, "name": "Salt Cathedral"}
    assert scenes.get_location_history(cid, sid) == [a]
    assert scenes.read_scene(cid, sid)["messages"] == []
    # change: announces and records
    assert scenes.set_location(cid, sid, b) == {"moved": True, "name": "Drowned Market"}
    assert scenes.get_location_history(cid, sid) == [a, b]
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "assistant", "content": "*The scene moves to Drowned Market.*"}]
    # re-select current: no-op
    assert scenes.set_location(cid, sid, b) == {"moved": False, "name": "Drowned Market"}
    assert scenes.get_location_history(cid, sid) == [a, b]
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_set_location_unknown_id_raises(monkeypatch, tmp_path):
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    with pytest.raises(entities.EntityNotFound):
        scenes.set_location(cid, sid, "nowhere")


def test_get_location_history_missing_scene_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_location_history(cid, "nope") == []


def test_edit_message_roundtrip_and_bounds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "frist")
    scenes.append_message(cid, sid, "assistant", "She nods.")
    scenes.edit_message(cid, sid, 0, "first")
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "She nods."}]
    with pytest.raises(IndexError):
        scenes.edit_message(cid, sid, 5, "x")


def test_remove_last_message(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    scenes.append_message(cid, sid, "assistant", "She nods.")
    scenes.remove_last_message(cid, sid)
    assert scenes.read_scene(cid, sid)["messages"] == [{"role": "user", "content": "hi"}]
    scenes.remove_last_message(cid, sid)
    assert scenes.read_scene(cid, sid)["messages"] == []
    with pytest.raises(IndexError):
        scenes.remove_last_message(cid, sid)


def test_remove_last_message_missing_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.remove_last_message(cid, "nope")


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


def test_set_datetime_first_silent_then_advance(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    # first set: silent, no transcript line — and the start date enters the filename
    res = scenes.set_datetime(cid, sid, "2026-06-29")
    assert res == {"advanced": False, "friendly": "29 June 2026", "id": "001--2026-06-29--s"}
    sid = res["id"]
    assert scenes.get_time_history(cid, sid) == ["2026-06-29"]
    assert scenes.read_scene(cid, sid)["messages"] == []
    # change: appends an italic transition line; filename keeps the start date
    res = scenes.set_datetime(cid, sid, "2026-07-04T09:00")
    assert res == {"advanced": True, "friendly": "4 July 2026", "id": sid}
    assert scenes.get_time_history(cid, sid) == ["2026-06-29", "2026-07-04T09:00"]
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "assistant", "content": "*Time passes. It is now 4 July 2026.*"}]
    # re-set the same current: no-op
    assert scenes.set_datetime(cid, sid, "2026-07-04T09:00") == {
        "advanced": False, "friendly": "4 July 2026", "id": sid}
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_first_datetime_rename_carries_references(monkeypatch, tmp_path):
    import json
    from grimoire.store import appearances
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "appearances.json").write_text(json.dumps(
        {"characters/a": {"version": "default", "base": "", "scenes": [sid], "role": "npc"}}),
        encoding="utf-8")
    new_sid = scenes.set_datetime(cid, sid, "2026-06-29")["id"]
    assert new_sid != sid
    assert appearances.record(cid)["characters/a"]["scenes"] == [new_sid]
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, sid)


def test_first_datetime_with_time_of_day_keeps_filename_windows_safe(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    new_sid = scenes.set_datetime(cid, sid, "2026-06-29T14:30")["id"]
    assert new_sid == "001--2026-06-29--s"  # time part (with its colon) never reaches the filename


def test_set_datetime_bad_input_raises(monkeypatch, tmp_path):
    from grimoire.store import calendars
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    with pytest.raises(calendars.CalendarError):
        scenes.set_datetime(cid, sid, "2026-13-40")


def test_get_time_history_missing_scene_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_time_history(cid, "nope") == []


def test_delete_removes_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Doomed")
    scenes.delete_scene(cid, sid)
    assert scenes.list_scenes(cid) == []
    with pytest.raises(scenes.SceneNotFound):
        scenes.delete_scene(cid, sid)


def test_mark_absorbed_writes_frontmatter(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Ending")
    scenes.mark_absorbed(cid, sid, "They parted.", "A and B parted at dawn.")
    meta = scenes.read_scene(cid, sid)["meta"]
    assert meta["one_line"] == "They parted."
    assert meta["summary"] == "A and B parted at dawn."
    assert meta["done"] == "true"


def test_mark_absorbed_missing_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.mark_absorbed(cid, "nope", "x", "y")


def test_create_assigns_padded_sequence_numbers(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.create_scene(cid, "Alpha") == "001--alpha"
    assert scenes.create_scene(cid, "Beta") == "002--beta"


def test_numbering_skips_gaps_left_by_deletes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    s1 = scenes.create_scene(cid, "Alpha")
    scenes.create_scene(cid, "Beta")
    scenes.delete_scene(cid, s1)
    assert scenes.create_scene(cid, "Gamma") == "003--gamma"  # 001 is never reused


def test_repad_widens_every_scene_and_repoints(monkeypatch, tmp_path):
    from grimoire.store import chronicle
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "One")           # 001--one
    d = campaigns.campaign_root(cid) / "scenes"
    (d / f"{sid}.md").rename(d / "999--one.md")     # simulate a campaign at the width limit
    chronicle.absorb(cid, {"id": "999--one", "one_line": "x", "summary": "", "keywords": []})
    new = scenes.create_scene(cid, "Two")
    assert new == "1000--two"
    assert sorted(p.stem for p in d.glob("*.md")) == ["0999--one", "1000--two"]
    assert "0999--one" in chronicle.read_chronicle(cid)
