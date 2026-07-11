import pytest

from grimoire.store import appearances, campaigns, pcs, scenes, worlds


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


def test_set_location_resolves_inherited_world_location(monkeypatch, tmp_path):
    """A thin campaign never copies world locations up front; setting one of
    them as a scene's setting must resolve through the overlay, not 404."""
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    eid = entities.create_entity(wroot, "locations", "Seraphine's Hall", "Never copied to the campaign.")
    sid = scenes.create_scene(cid, "S")
    assert scenes.set_location(cid, sid, eid) == {"moved": False, "name": "Seraphine's Hall"}
    assert scenes.get_location_history(cid, sid) == [eid]


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


def test_remove_trailing_run_missing_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.remove_trailing_assistant_run(cid, "nope")


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


def test_list_scenes_reports_first_time_history_entry_as_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Undated")
    assert scenes.list_scenes(cid)[0]["date"] == ""
    new_sid = scenes.set_datetime(cid, sid, "2026-06-29")["id"]
    scenes.set_datetime(cid, new_sid, "2026-07-04T09:00")  # later advance must not change "date"
    assert scenes.list_scenes(cid)[0]["date"] == "2026-06-29"


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


def test_rename_preserves_number_and_date_sections(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Old")                      # 001--old
    assert scenes.rename_scene(cid, sid, "New Name") == "001--new-name"
    sid = scenes.set_datetime(cid, "001--new-name", "2026-06-29")["id"]  # 001--2026-06-29--new-name
    assert scenes.rename_scene(cid, sid, "Final") == "001--2026-06-29--final"


def test_rename_repoints_chronicle_changes_and_plot(monkeypatch, tmp_path):
    from grimoire.store import changes, chronicle, plot
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    chronicle.absorb(cid, {"id": sid, "one_line": "x", "summary": "", "keywords": []})
    changes.record(cid, sid, {"characters/a": [{"op": "equal", "text": "hi"}]})
    plot.set_movement(cid, "heist", "The Heist", "open", "beat", sid)
    new_sid = scenes.rename_scene(cid, sid, "Renamed")
    assert new_sid in chronicle.read_chronicle(cid)
    assert changes.read(cid)["characters/a"]["scene"] == new_sid
    assert plot.read(cid)["heist"]["last_scene"] == new_sid


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


def test_message_speaker_round_trip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Speakers")
    scenes.append_message(cid, sid, "user", "I open the door.")
    scenes.append_message(cid, sid, "assistant", "“At last,” she says.", speaker="Seraphine Vale")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert "speaker" not in msgs[0]
    assert msgs[1]["speaker"] == "Seraphine Vale"
    assert msgs[1]["content"] == "“At last,” she says."
    # edit_message must preserve the speaker
    scenes.edit_message(cid, sid, 1, "Edited.")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert msgs[1]["speaker"] == "Seraphine Vale"
    assert msgs[1]["content"] == "Edited."


def _campaign_with_pc(monkeypatch, tmp_path, pc_name="Elara Vane"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    pid, pvid = pcs.create_pc(worlds.world_root(wid), pc_name, [])
    appearances.appear(cid, sid, "pcs", pid, pvid, "player")
    return cid, sid


def test_script_labels_store_and_derive_roles(monkeypatch, tmp_path):
    cid, sid = _campaign_with_pc(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "I draw my blade.", speaker="Elara Vane")
    scenes.append_message(cid, sid, "assistant", '"You dare?"', speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "The hall falls silent.")
    raw = (campaigns.campaign_root(cid) / "scenes" / f"{sid}.md").read_text(encoding="utf-8")
    assert "**Elara Vane:** I draw my blade." in raw
    assert "**Seraphine Vale:**" in raw and "(Seraphine Vale)" not in raw
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert [(m["role"], m.get("speaker")) for m in msgs] == [
        ("user", "Elara Vane"),
        ("assistant", "Seraphine Vale"),
        ("assistant", None),
    ]


def test_legacy_labels_and_parens_still_parse(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    p = campaigns.campaign_root(cid) / "scenes" / f"{sid}.md"
    meta_text = p.read_text(encoding="utf-8").split("---")[1]
    p.write_text("---" + meta_text + "---\n\n"
                 "**You:** hello\n\n"
                 "**Grimoire (Seraphine Vale):** she nods\n\n"
                 "**Grimoire:** rain falls\n", encoding="utf-8")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert [(m["role"], m.get("speaker")) for m in msgs] == [
        ("user", None),
        ("assistant", "Seraphine Vale"),
        ("assistant", None),
    ]


def test_marker_requires_blank_line(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "line one\n**Aside:** same message")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "line one\n**Aside:** same message"


def test_unsafe_speaker_falls_back_to_reserved_label(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi", speaker="x" * 65)
    raw = (campaigns.campaign_root(cid) / "scenes" / f"{sid}.md").read_text(encoding="utf-8")
    assert "**You:** hi" in raw


def test_stamp_user_speaker_backfills_only_bare_user_lines(monkeypatch, tmp_path):
    cid, sid = _campaign_with_pc(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "first, before the PC joined")
    scenes.append_message(cid, sid, "assistant", "noted")
    scenes.stamp_user_speaker(cid, sid, "Elara Vane")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert [(m["role"], m.get("speaker")) for m in msgs] == [
        ("user", "Elara Vane"), ("assistant", None)]
    raw = (campaigns.campaign_root(cid) / "scenes" / f"{sid}.md").read_text(encoding="utf-8")
    assert "**Elara Vane:** first, before the PC joined" in raw


def test_split_reply_segments_and_guards():
    players = frozenset({"Elara Vane"})
    text = ("The rain hammers the roof.\n\n"
            '**Seraphine Vale:** "You dare?"\n\n'
            "**Grimoire:** Thunder rolls.\n\n"
            "**Elara Vane:** I would never—")
    assert scenes.split_reply(text, players) == [
        {"speaker": None, "content": "The rain hammers the roof."},
        {"speaker": "Seraphine Vale", "content": '"You dare?"'},
        {"speaker": None, "content": "Thunder rolls."},
        # a player-named block is never stored as the player: reassigned to the narrator
        {"speaker": None, "content": "I would never—"},
    ]


def test_split_reply_without_markers_is_one_narrator_post():
    assert scenes.split_reply("Just prose.", frozenset()) == [
        {"speaker": None, "content": "Just prose."}]
    assert scenes.split_reply("   ", frozenset()) == []


def test_remove_trailing_assistant_run(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    scenes.append_message(cid, sid, "assistant", "one", speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "two")
    scenes.remove_trailing_assistant_run(cid, sid)
    assert scenes.read_scene(cid, sid)["messages"] == [{"role": "user", "content": "hi"}]
    with pytest.raises(IndexError):
        scenes.remove_trailing_assistant_run(cid, sid)


def test_roll_speaker_does_not_collide_with_a_character_actually_named_roll(monkeypatch, tmp_path):
    # A real speaker literally named "Roll" must round-trip as plain "Roll",
    # not be swallowed by the (invisible-prefixed) manual-roll sentinel.
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "assistant", "hello", speaker="Roll")
    scenes.append_message(cid, sid, "assistant", "\U0001F3B2 2d6 = 7", speaker=scenes.ROLL_SPEAKER)
    messages = scenes.read_scene(cid, sid)["messages"]
    assert messages[0]["speaker"] == "Roll"
    assert messages[1]["speaker"] == scenes.ROLL_SPEAKER
    assert messages[0]["speaker"] != messages[1]["speaker"]
    # rerolling stops at the roll line but the plain "Roll"-spoken reply
    # would have been fair game had it trailed instead
    with pytest.raises(IndexError):
        scenes.remove_trailing_assistant_run(cid, sid)


def test_remove_trailing_assistant_run_refuses_when_trailing_message_is_a_roll(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    scenes.append_message(cid, sid, "assistant", "one")
    scenes.append_message(cid, sid, "assistant", "\U0001F3B2 2d6 = 7", speaker=scenes.ROLL_SPEAKER)
    with pytest.raises(IndexError):
        scenes.remove_trailing_assistant_run(cid, sid)
    # the reply and the roll line both survive — reroll must not silently
    # delete a transcript line whose entry still lives in rolls.json
    messages = scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 3


# ---- fuzzy speaker matching (a first name refers to the cast member) ----
def test_edit_message_refuses_a_manual_roll_line(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "assistant", "\U0001F3B2 2d6 = 7", speaker=scenes.ROLL_SPEAKER)
    with pytest.raises(scenes.RollMessageImmutable):
        scenes.edit_message(cid, sid, 0, "9001")
    assert scenes.read_scene(cid, sid)["messages"][0]["content"] == "\U0001F3B2 2d6 = 7"


def test_match_name_prefix_and_ambiguity_rules():
    names = ["winifred Vance", "Seraphine Vale"]
    assert scenes.match_name("winifred", names) == "winifred Vance"
    assert scenes.match_name("winifred vance", names) == "winifred Vance"  # case-insensitive
    assert scenes.match_name("Flo", names) is None          # mid-word: not a match
    assert scenes.match_name("Vale", names) is None          # not a prefix
    assert scenes.match_name("", names) is None
    both = ["winifred Vance", "winifred Nightingale"]
    assert scenes.match_name("winifred", both) is None       # ambiguous: match nothing
    assert scenes.match_name("winifred Vance", both) == "winifred Vance"


def test_split_reply_guards_player_first_name():
    players = frozenset({"Elara Vane"})
    text = '**Elara:** forged line\n\n**Seraphine:** "Fine."'
    assert scenes.split_reply(text, players) == [
        # "Elara" refers to the player: reassigned to the narrator like the full name
        {"speaker": None, "content": "forged line"},
        {"speaker": "Seraphine", "content": '"Fine."'},
    ]


def test_create_scene_stores_a_valid_suggested_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S", suggested_date="2026-07-10")
    assert scenes.get_suggested_date(cid, sid) == "2026-07-10"


def test_create_scene_ignores_an_invalid_suggested_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S", suggested_date="soonish")
    assert scenes.get_suggested_date(cid, sid) == ""


def test_get_suggested_date_missing_scene_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_suggested_date(cid, "nope") == ""


def test_set_datetime_clears_the_suggested_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S", suggested_date="2026-07-10")
    sid = scenes.set_datetime(cid, sid, "2026-07-12")["id"]
    assert scenes.get_suggested_date(cid, sid) == ""
