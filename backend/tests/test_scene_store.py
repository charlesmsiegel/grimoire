import threading
import time

import pytest

from grimoire.store import appearances, campaigns, frontmatter, pcs, scenes, worlds
from grimoire.store.scenes import lifecycle as scenes_lifecycle
from grimoire.store.scenes import moment as scenes_moment
from grimoire.store.scenes import read as scenes_read
from grimoire.store.scenes import write as scenes_write


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


def test_list_scenes_reports_absorbed_as_done(monkeypatch, tmp_path):
    """The rail marks a finished scene and the composer hides itself for one, so
    `done` has to reach the list -- and it has to agree with the guard that
    actually refuses a second absorb (`routes.scenes._already_absorbed`), which
    reads the same key case-insensitively out of a hand-editable file."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Unfinished")
    assert scenes.list_scenes(cid)[0]["done"] is False

    scenes.mark_absorbed(cid, sid, "one line", "the summary")
    assert scenes.list_scenes(cid)[0]["done"] is True


def test_list_scenes_done_matches_the_absorb_guard_on_hand_edits(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Hand Edited")
    p = tmp_path / "campaigns" / cid / "scenes" / f"{sid}.md"
    p.write_text(p.read_text(encoding="utf-8").replace("title:", "done: TRUE\ntitle:", 1),
                 encoding="utf-8")
    assert scenes.list_scenes(cid)[0]["done"] is True


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


def test_read_scene_meta_is_frontmatter_only(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Frontmatter Only")
    scenes.append_message(cid, sid, "user", "This transcript content must not be needed.")
    meta = scenes.read_scene_meta(cid, sid)
    assert meta["id"] == sid
    assert meta["title"] == "Frontmatter Only"
    assert "messages" not in meta


def test_read_scene_meta_unknown_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene_meta(cid, "nope")


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
        {"role": "assistant", "content": "*The scene moves to Drowned Market.*",
         "speaker": scenes.TRANSITION_SPEAKER}]
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
        {"role": "assistant", "content": "*Time passes. It is now 4 July 2026.*",
         "speaker": scenes.TRANSITION_SPEAKER}]
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


def test_append_message_reports_where_it_landed(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    assert scenes.append_message(cid, sid, "user", "hi") == 0
    assert scenes.append_message(cid, sid, "assistant", "hello") == 1
    assert scenes.append_message(cid, sid, "user", "and then?") == 2


def test_remove_trailing_user_post_takes_back_the_orphan(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    scenes.append_message(cid, sid, "assistant", "hello")
    at = scenes.append_message(cid, sid, "user", "and then?")
    assert scenes.remove_trailing_user_post(cid, sid, at, "and then?") is True
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_remove_trailing_user_post_leaves_a_transcript_that_moved_on(monkeypatch, tmp_path):
    """Only the post the caller named, and only while it is still last. A reply
    or a manual roll landing behind it means the turn is no longer the tail, and
    an undo that deleted anyway would take back an answered post."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    at = scenes.append_message(cid, sid, "user", "and then?")
    assert scenes.remove_trailing_user_post(cid, sid, at, "something else") is False
    scenes.append_message(cid, sid, "assistant", "the door opens")
    assert scenes.remove_trailing_user_post(cid, sid, at, "and then?") is False
    assert len(scenes.read_scene(cid, sid)["messages"]) == 2


def test_remove_trailing_user_post_will_not_take_a_twins_post(monkeypatch, tmp_path):
    """Two overlapping turns can carry identical text — nothing holds a lock
    across the LLM call between append and undo. Matching on content alone, the
    first turn's undo would delete the second turn's post while its generation
    was still running. The index makes it a refusal."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    first = scenes.append_message(cid, sid, "user", "and then?")   # turn A
    second = scenes.append_message(cid, sid, "user", "and then?")  # turn B, still streaming
    assert scenes.remove_trailing_user_post(cid, sid, first, "and then?") is False
    assert len(scenes.read_scene(cid, sid)["messages"]) == 2
    # B finishes badly too; now IT is the tail, and its own undo is the one that
    # fires. A's post is then last again and A's undo would fire on a re-run.
    assert scenes.remove_trailing_user_post(cid, sid, second, "and then?") is True
    assert scenes.remove_trailing_user_post(cid, sid, first, "and then?") is True
    assert scenes.read_scene(cid, sid)["messages"] == []


def test_remove_trailing_user_post_keeps_turn_boundaries(monkeypatch, tmp_path):
    """turn_sizes counts model blocks, so taking a user post off must not
    disturb it — a shifted boundary would have reroll eat the wrong generation."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "one"},
                                   {"speaker": None, "content": "two"}])
    at = scenes.append_message(cid, sid, "user", "orphan")
    assert scenes.remove_trailing_user_post(cid, sid, at, "orphan") is True
    assert scenes.get_turn_sizes(cid, sid) == [2]


def test_remove_trailing_user_post_on_an_empty_or_missing_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    assert scenes.remove_trailing_user_post(cid, sid, 0, "anything") is False
    with pytest.raises(scenes.SceneNotFound):
        scenes.remove_trailing_user_post(cid, "nope", 0, "anything")


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
    names = ["Winifred Vance", "Seraphine Vale"]
    assert scenes.match_name("Winifred", names) == "Winifred Vance"
    assert scenes.match_name("winifred vance", names) == "Winifred Vance"  # case-insensitive
    assert scenes.match_name("Flo", names) is None          # mid-word: not a match
    assert scenes.match_name("Vale", names) is None          # not a prefix
    assert scenes.match_name("", names) is None
    both = ["Winifred Vance", "Winifred Nightingale"]
    assert scenes.match_name("Winifred", both) is None       # ambiguous: match nothing
    assert scenes.match_name("Winifred Vance", both) == "Winifred Vance"


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


def test_trim_continuation_preserves_roll_speaker(monkeypatch, tmp_path):
    """A crashed/superseded continuation attempt is rolled back to the
    narration-intent point, but a manual dice-roll line that landed in the
    same crash window (the only non-superseding writer) must survive."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "I attack.")                       # 0: intent point
    scenes.append_message(cid, sid, "assistant", "Steel rings out")            # 1: continuation-partial
    scenes.append_message(cid, sid, "assistant", "🎲 rolled 14 vs DC 12 — success",
                          speaker=scenes.ROLL_SPEAKER)                         # 2: manual roll line
    scenes.trim_continuation(cid, sid, 1)
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "I attack."},
        {"role": "assistant", "content": "🎲 rolled 14 vs DC 12 — success",
         "speaker": scenes.ROLL_SPEAKER},
    ]


def test_trim_continuation_missing_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.trim_continuation(cid, "nope", 0)


# ---- transition speaker / turn boundaries (2026-07-26 response-presets design) ----

def test_transition_messages_carry_the_reserved_speaker(monkeypatch, tmp_path):
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Moves")
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "Saltmarch Docks", "Wet rope and tar.")
    entities.create_entity(croot, "locations", "The Long Stair", "Down and down.")
    scenes.set_location(cid, sid, "saltmarch-docks")     # first is silent
    scenes.set_location(cid, sid, "the-long-stair")      # this one appends
    messages = scenes.read_scene(cid, sid)["messages"]
    assert messages[-1]["speaker"] == scenes.TRANSITION_SPEAKER
    assert "The Long Stair" in messages[-1]["content"]


def test_time_advance_carries_the_reserved_speaker(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Clock")
    # the first set is silent and stamps the date into the filename, so the
    # scene id changes underneath us — carry it forward
    sid = scenes.set_datetime(cid, sid, "2026-06-29")["id"]
    got = scenes.set_datetime(cid, sid, "2026-07-04T09:00")
    messages = scenes.read_scene(cid, got["id"])["messages"]
    assert messages[-1]["speaker"] == scenes.TRANSITION_SPEAKER
    assert "Time passes" in messages[-1]["content"]


def test_transition_speaker_cannot_collide_with_a_real_name():
    # U+2063-prefixed, exactly like ROLL_SPEAKER, so a character actually
    # called "Scene" round-trips as plain "Scene"
    assert scenes.TRANSITION_SPEAKER.startswith("\u2063")
    assert scenes.TRANSITION_SPEAKER != scenes.ROLL_SPEAKER


def test_append_reply_records_a_turn_boundary(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Turns")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "The door opens."},
                                   {"speaker": "Mara", "content": "You're late."}])
    scenes.append_reply(cid, sid, [{"speaker": "Winifred", "content": "I walked."}])
    assert scenes.get_turn_sizes(cid, sid) == [2, 1]
    assert len(scenes.read_scene(cid, sid)["messages"]) == 3


def test_consecutive_generations_stay_separate_without_user_messages(monkeypatch, tmp_path):
    """Offscreen/director play persists no user turn between generations."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Offscreen")
    for _ in range(3):
        scenes.append_reply(cid, sid, [{"speaker": None, "content": "Time grinds on."}])
    assert scenes.get_turn_sizes(cid, sid) == [1, 1, 1]


def test_turn_sizes_survive_a_message_edit(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Edits")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Short."}])
    scenes.edit_message(cid, sid, 0, "Much, much longer now.")
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_editing_a_reply_into_two_blocks_retracks_the_turn(monkeypatch, tmp_path):
    """Stored content is re-split at READ time, so an edit that introduces a
    `**Speaker:**` marker grows the reply by a block. Leave turn_sizes saying
    1 and reroll removes half the reply the user asked to regenerate."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Split")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "One thing."}])
    scenes.edit_message(cid, sid, 1, "One thing.\n\n**Winifred:** And another.")
    assert len(scenes.read_scene(cid, sid)["messages"]) == 3
    assert scenes.get_turn_sizes(cid, sid) == [2]
    scenes.remove_trailing_assistant_run(cid, sid)
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == ["Go on."]
    assert scenes.get_turn_sizes(cid, sid) == []


def test_editing_an_earlier_turn_keeps_later_turns_attributed(monkeypatch, tmp_path):
    """A block spliced in mid-transcript shifts the last-sum(sizes) window, so
    drift segmentation would read the wrong blocks as the most recent turn."""
    from grimoire.store import length_drift
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Window")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "First."}])
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Second."}])
    scenes.edit_message(cid, sid, 0, "First.\n\n**Winifred:** Aside.")
    assert scenes.get_turn_sizes(cid, sid) == [2, 1]
    turns = length_drift.segment(scenes.read_scene(cid, sid)["messages"],
                                 scenes.get_turn_sizes(cid, sid))
    assert [m["content"] for m in turns[-1]] == ["Second."]


def test_editing_an_untracked_legacy_block_leaves_turn_sizes_alone(monkeypatch, tmp_path):
    """The tracked suffix is anchored at the END: growing the pre-tracking
    prefix cannot move it, so nothing needs adjusting."""
    from grimoire.store import length_drift
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Upgraded")
    scenes.append_message(cid, sid, "assistant", "Written before turn tracking.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Tracked."}])
    scenes.edit_message(cid, sid, 0, "Written before it.\n\n**Winifred:** Also legacy.")
    assert scenes.get_turn_sizes(cid, sid) == [1]
    turns = length_drift.segment(scenes.read_scene(cid, sid)["messages"],
                                 scenes.get_turn_sizes(cid, sid))
    assert [m["content"] for m in turns[-1]] == ["Tracked."]


def test_an_edit_that_removes_a_model_block_shrinks_the_turn(monkeypatch, tmp_path):
    """The count can fall as well as rise: a legacy `**Grimoire (Name):**`
    label is rewritten as plain `**Name:**` by the next save, and when Name is
    the seated player that block parses back as a USER line — one model block
    fewer than turn_sizes was told about."""
    from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
    cid, sid = _campaign_with_pc(monkeypatch, tmp_path)
    p = campaigns.campaign_root(cid) / "scenes" / f"{sid}.md"
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["turn_sizes"] = "2"
    p.write_text(dump_frontmatter(
        meta, "**Grimoire:** The gate holds.\n\n**Grimoire (Elara Vane):** I brace it.\n"),
        encoding="utf-8")
    assert len(scenes.read_scene(cid, sid)["messages"]) == 2
    scenes.edit_message(cid, sid, 0, "The gate holds fast.")
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_remove_trailing_assistant_run_pops_the_boundary(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Reroll")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "First try."},
                                   {"speaker": None, "content": "She waits."}])
    scenes.remove_trailing_assistant_run(cid, sid)
    assert scenes.get_turn_sizes(cid, sid) == []
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_reroll_removes_only_the_last_director_generation(monkeypatch, tmp_path):
    """Director generations have no persisted user separator between them, so
    a role-based trailing-run removal would delete ALL of them at once while
    popping a single recorded size."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Offscreen reroll")
    for n in ("one", "two", "three"):
        scenes.append_reply(cid, sid, [{"speaker": None, "content": n}])
    scenes.remove_trailing_assistant_run(cid, sid)
    assert scenes.get_turn_sizes(cid, sid) == [1, 1]
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == ["one", "two"]


def test_reroll_does_not_eat_transition_messages(monkeypatch, tmp_path):
    """A transition is not model output; reroll must stop at it rather than
    silently deleting the scene's *Time passes...* line."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Transitions")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Before."}])
    scenes.append_message(cid, sid, "assistant", "*Time passes. It is now dusk.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "After."}])
    scenes.remove_trailing_assistant_run(cid, sid)
    contents = [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]
    assert contents == ["Before.", "*Time passes. It is now dusk.*"]
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_trim_continuation_clamps_turn_sizes(monkeypatch, tmp_path):
    """proposals.commit_narration crash recovery drops model blocks; the
    boundary list must not be left describing blocks that no longer exist."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Crash")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Kept."}])
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Crashed."},
                                   {"speaker": None, "content": "Half-written."}])
    assert scenes.get_turn_sizes(cid, sid) == [1, 2]
    scenes.trim_continuation(cid, sid, 1)
    assert scenes.get_turn_sizes(cid, sid) == [1]
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_no_turn_sizes_on_a_legacy_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Legacy")
    scenes.append_message(cid, sid, "assistant", "Written before turn tracking.")
    assert scenes.get_turn_sizes(cid, sid) == []


def test_trim_continuation_clamps_against_tracked_blocks_not_total(monkeypatch, tmp_path):
    """On an upgraded scene the untracked legacy prefix must not mask a trim.
    Counting every retained assistant block leaves a stale size behind, and
    segmentation then attributes legacy messages to a turn that never existed."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Upgraded")
    for n in range(10):                                   # legacy, untracked
        scenes.append_message(cid, sid, "assistant", f"old {n}")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Kept."}])
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Crashed."},
                                   {"speaker": None, "content": "Half-written."}])
    assert scenes.get_turn_sizes(cid, sid) == [1, 2]
    scenes.trim_continuation(cid, sid, 11)                # drop the 2-block turn
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_append_reply_persists_blocks_and_boundary_in_one_write(monkeypatch, tmp_path):
    """Segments and their turn_sizes entry must land together. Writing each
    segment separately leaves untracked blocks at the tail if persistence is
    interrupted, and reroll then counts sizes[-1] blocks back THROUGH them into
    the previous completed reply."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Atomic")
    writes = _count_scene_writes(monkeypatch)
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "One."},
                                   {"speaker": None, "content": "Two."},
                                   {"speaker": "Winifred Vance", "content": "Three."}])
    assert len(writes) == 1, f"expected a single scene write, got {writes}"
    assert scenes.get_turn_sizes(cid, sid) == [3]
    assert len(scenes.read_scene(cid, sid)["messages"]) == 3


def test_set_response_writes_and_clears_bundle_fields(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Bundle")
    scenes.set_response(cid, sid, {"response_preset": "terse", "length_speakers": "3"})
    meta = scenes.read_scene(cid, sid)["meta"]
    assert meta["response_preset"] == "terse" and meta["length_speakers"] == "3"
    scenes.set_response(cid, sid, {"length_speakers": ""})       # clear one field
    meta = scenes.read_scene(cid, sid)["meta"]
    assert meta["length_speakers"] == ""
    assert meta["response_preset"] == "terse"                    # untouched


def test_reroll_steps_over_trailing_transitions_and_keeps_them(monkeypatch, tmp_path):
    """A transition that lands AFTER the reply (a location change, a time
    advance, someone leaving) must not block reroll: the generation beneath it
    is regenerated and the transition survives, in order."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Transitions")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "First try."},
                                   {"speaker": None, "content": "She waits."}])
    scenes.append_message(cid, sid, "assistant", "*The scene moves to the Salt Cathedral.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    scenes.append_message(cid, sid, "assistant", "*Time passes. It is now dusk.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    scenes.remove_trailing_assistant_run(cid, sid)
    messages = scenes.read_scene(cid, sid)["messages"]
    assert [m["content"] for m in messages] == [
        "Go on.",
        "*The scene moves to the Salt Cathedral.*",
        "*Time passes. It is now dusk.*",
    ]
    assert [m.get("speaker") for m in messages[1:]] == [scenes.TRANSITION_SPEAKER] * 2
    assert scenes.get_turn_sizes(cid, sid) == []


def test_reroll_still_refuses_a_roll_hidden_under_a_transition(monkeypatch, tmp_path):
    """Stepping over transitions must not step over a dice roll behind one —
    the roll's transcript line stays in lockstep with its rolls.json entry."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Rolls")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "A reply."}])
    scenes.append_message(cid, sid, "assistant", "\U0001F3B2 2d6 = 7", speaker=scenes.ROLL_SPEAKER)
    scenes.append_message(cid, sid, "assistant", "*Time passes. It is now dusk.*",
                          speaker=scenes.TRANSITION_SPEAKER)
    with pytest.raises(IndexError):
        scenes.remove_trailing_assistant_run(cid, sid)
    assert len(scenes.read_scene(cid, sid)["messages"]) == 4
    assert scenes.get_turn_sizes(cid, sid) == [1]


def _count_scene_writes(monkeypatch):
    """Record every scene-file write, so a two-phase mutation is visible.

    Transcript and turn_sizes must land in ONE write: a crash between two
    writes leaves the boundary list describing a transcript that no longer
    exists, and the next reroll trusts sizes[-1] and deletes blocks belonging
    to an older generation — irreversible transcript loss.
    """
    from grimoire.store import atomic
    writes: list[str] = []
    real = atomic.write_text

    # Counts at store.atomic, the single seam every record write goes through
    # since #233 -- patching Path.write_text here would silently count zero.
    def counting(path, text):
        if path.suffix == ".md":
            writes.append(path.name)
        return real(path, text)

    monkeypatch.setattr(atomic, "write_text", counting)
    return writes


def _turn_sizes_fit(cid, sid):
    """sum(turn_sizes) never claims more model blocks than the scene holds."""
    messages = scenes.read_scene(cid, sid)["messages"]
    blocks = [m for m in messages
              if m["role"] == "assistant" and m.get("speaker") not in scenes.SYNTHETIC_SPEAKERS]
    return sum(scenes.get_turn_sizes(cid, sid)) <= len(blocks)


def test_reroll_persists_transcript_and_boundary_in_one_write(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Atomic reroll")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "One."},
                                   {"speaker": None, "content": "Two."}])
    writes = _count_scene_writes(monkeypatch)
    scenes.remove_trailing_assistant_run(cid, sid)
    assert len(writes) == 1, f"expected a single scene write, got {writes}"
    assert scenes.get_turn_sizes(cid, sid) == []
    assert _turn_sizes_fit(cid, sid)


def test_trim_continuation_persists_transcript_and_boundary_in_one_write(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Atomic trim")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Kept."}])
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Crashed."},
                                   {"speaker": None, "content": "Half-written."}])
    writes = _count_scene_writes(monkeypatch)
    scenes.trim_continuation(cid, sid, 1)
    assert len(writes) == 1, f"expected a single scene write, got {writes}"
    assert scenes.get_turn_sizes(cid, sid) == [1]
    assert _turn_sizes_fit(cid, sid)


def test_edit_message_persists_transcript_and_boundary_in_one_write(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Atomic edit")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "One thing."}])
    writes = _count_scene_writes(monkeypatch)
    scenes.edit_message(cid, sid, 1, "One thing.\n\n**Winifred:** And another.")
    assert len(writes) == 1, f"expected a single scene write, got {writes}"
    assert scenes.get_turn_sizes(cid, sid) == [2]
    assert _turn_sizes_fit(cid, sid)


def _hand_write_turn_sizes(cid, sid, raw):
    """Corrupt turn_sizes out of band, as a hand-edit or a half-finished write
    from an older build would."""
    from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
    p = campaigns.campaign_root(cid) / "scenes" / f"{sid}.md"
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["turn_sizes"] = raw
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def test_reroll_refuses_boundaries_that_do_not_fit_the_transcript(monkeypatch, tmp_path):
    """A turn_sizes list claiming more blocks than exist must not authorize a
    deletion — it would consume an earlier generation's blocks."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Desynced")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "one"}])
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "two"}])
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "three"}])
    _hand_write_turn_sizes(cid, sid, "1,1,4")
    with pytest.raises(scenes.TurnSizesDesynced):
        scenes.remove_trailing_assistant_run(cid, sid)
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == \
        ["one", "two", "three"]


def test_reroll_refuses_when_the_last_turn_is_not_at_the_tail(monkeypatch, tmp_path):
    """The recorded generation must sit contiguously at the end. With a user
    line spliced into what it claims, deleting sizes[-1] blocks reaches back
    past the player's message into the previous generation."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Spliced")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "one"}])
    scenes.append_message(cid, sid, "user", "I interrupt.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "two"}])
    _hand_write_turn_sizes(cid, sid, "1,2")
    with pytest.raises(scenes.TurnSizesDesynced):
        scenes.remove_trailing_assistant_run(cid, sid)
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == \
        ["one", "I interrupt.", "two"]


def test_garbled_turn_sizes_are_no_tracking_at_all(monkeypatch, tmp_path):
    """Dropping the bad token would invent a boundary list ([2, 1]) that
    measurement then treats as authoritative and reroll uses destructively."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Garbled")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "one"}])
    _hand_write_turn_sizes(cid, sid, "2,garbled,1")
    assert scenes.get_turn_sizes(cid, sid) == []


def test_a_zero_turn_invalidates_the_whole_field(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Zeroes")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "one"}])
    _hand_write_turn_sizes(cid, sid, "2,0,1")
    assert scenes.get_turn_sizes(cid, sid) == []
    _hand_write_turn_sizes(cid, sid, "1,-2")
    assert scenes.get_turn_sizes(cid, sid) == []
    _hand_write_turn_sizes(cid, sid, "2,1")           # a valid list still parses
    assert scenes.get_turn_sizes(cid, sid) == [2, 1]


def test_reroll_on_garbled_boundaries_takes_the_untracked_path(monkeypatch, tmp_path):
    """No tracking means the pre-boundary behaviour: the trailing assistant run
    comes off, and the unusable field is cleared rather than half-trusted."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Garbled reroll")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "one"},
                                   {"speaker": None, "content": "two"}])
    _hand_write_turn_sizes(cid, sid, "2,garbled")
    scenes.remove_trailing_assistant_run(cid, sid)
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == ["Go on."]
    assert scenes.get_turn_sizes(cid, sid) == []


def test_a_failed_append_leaves_the_whole_transcript_readable(monkeypatch, tmp_path):
    """The #233 case: a scene transcript is the one piece of user data that
    cannot be regenerated. A write that dies mid-flight must leave every
    earlier message intact rather than a truncated file."""
    from grimoire.store import atomic
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Durable")
    scenes.append_message(cid, sid, "user", "First message.")
    scenes.append_message(cid, sid, "assistant", "Second message.")

    real = atomic.os.replace

    def boom(src, dst):
        raise OSError("crash mid-publish")

    monkeypatch.setattr(atomic.os, "replace", boom)
    with pytest.raises(OSError):
        scenes.append_message(cid, sid, "user", "Third message, lost.")
    monkeypatch.setattr(atomic.os, "replace", real)  # not undo(): that also
    # reverts the GRIMOIRE_HOME setenv the campaign fixture depends on

    messages = scenes.read_scene(cid, sid)["messages"]
    assert [m["content"] for m in messages] == ["First message.", "Second message."]
    scene_dir = campaigns.campaign_root(cid) / "scenes"
    assert list(scene_dir.glob("*.tmp")) == [], "a temp file was left behind"


# ---- lost updates (#254) ----
#
# #233 made the write atomic, which guarantees the file is never torn. It says
# nothing about a lost update: two unlocked read-modify-writes both publish
# complete, well-formed files, and the second one silently erases the first's
# message. These tests widen the read->write window so an unlocked
# implementation loses a message every run rather than once in a thousand.


def _widen_the_write_window(monkeypatch, delay=0.05):
    """Stretch every scene read so concurrent writers reliably overlap.

    Without this the window is microseconds wide and a lost-update test is a
    coin flip that passes on the broken code most of the time -- which is
    exactly how this bug survived to #254.

    EVERY submodule that imported the name, not just one: `parse_frontmatter`
    is imported by value into `read`, `write`, `moment` and `lifecycle`, so
    each holds its own binding and patching one leaves the others fast. The
    three tests below mutate only through `write`, and `write` alone would
    widen their window today; every importer is patched so that a lost-update
    test written against another entry point -- `moment` and `lifecycle` are
    read-modify-writes too -- is not silently narrow, back to microseconds and
    green on broken code.
    """
    real = frontmatter.parse_frontmatter

    def slow(text):
        parsed = real(text)
        time.sleep(delay)
        return parsed

    for mod in (scenes_read, scenes_write, scenes_moment, scenes_lifecycle):
        monkeypatch.setattr(mod, "parse_frontmatter", slow)


def _run_together(calls):
    """Run each thunk in its own thread, all entering at the same instant."""
    start = threading.Barrier(len(calls))
    errors = []

    def run(call):
        try:
            start.wait(timeout=5)
            call()
        except Exception as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads), "a scene write deadlocked"
    assert not errors, f"a concurrent write raised: {errors}"


def test_concurrent_appends_never_drop_a_message(monkeypatch, tmp_path):
    """Every append survives. Unlocked, all five readers see the same v0 and
    the last writer to publish wins -- four messages gone, no error, no trace."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Concurrent")
    _widen_the_write_window(monkeypatch)

    _run_together([
        (lambda n=n: scenes.append_message(cid, sid, "user", f"message {n}"))
        for n in range(5)
    ])

    contents = [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]
    assert sorted(contents) == [f"message {n}" for n in range(5)]


def test_a_user_message_racing_a_persisted_reply_keeps_both(monkeypatch, tmp_path):
    """The ordinary case from the ticket: the player types while the model's
    reply is being written. Either order is fine; losing one is not, and
    turn_sizes must still describe the blocks that are actually stored."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Racing")
    _widen_the_write_window(monkeypatch)

    _run_together([
        lambda: scenes.append_message(cid, sid, "user", "Wait, I interrupt."),
        lambda: scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "one"},
                                               {"speaker": None, "content": "two"}]),
    ])

    contents = [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]
    assert sorted(contents) == ["Wait, I interrupt.", "one", "two"]
    assert scenes.get_turn_sizes(cid, sid) == [2]


def test_an_edit_racing_an_append_keeps_the_appended_message(monkeypatch, tmp_path):
    """edit_message is a read-modify-write of the same body, so it loses an
    append landing beside it just as surely as another append would."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Edited")
    scenes.append_message(cid, sid, "user", "original")
    _widen_the_write_window(monkeypatch)

    _run_together([
        lambda: scenes.edit_message(cid, sid, 0, "edited"),
        lambda: scenes.append_message(cid, sid, "assistant", "appended"),
    ])

    contents = [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]
    assert sorted(contents) == ["appended", "edited"]


# ---- windowed reads (#94) ----


def _scene_of(monkeypatch, tmp_path, n):
    """A scene whose transcript is `n` messages, alternating user/assistant,
    each naming its own index so a window can be checked positionally."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Long Scene")
    for i in range(n):
        scenes.append_message(cid, sid, "user" if i % 2 == 0 else "assistant", f"post {i}")
    return cid, sid


def _contents(page):
    return [m["content"] for m in page["messages"]]


def test_window_defaults_to_the_tail(monkeypatch, tmp_path):
    cid, sid = _scene_of(monkeypatch, tmp_path, 10)
    page = scenes.read_scene_window(cid, sid, 3)
    assert _contents(page) == ["post 7", "post 8", "post 9"]
    assert (page["offset"], page["total"], page["has_older"]) == (7, 10, True)
    assert page["meta"]["id"] == sid


def test_window_walks_backwards_by_its_own_offset(monkeypatch, tmp_path):
    """The cursor a page returns is what the next-older page is asked for, so
    walking it must reach the top exactly once with no gap and no repeat."""
    cid, sid = _scene_of(monkeypatch, tmp_path, 10)
    seen, before = [], None
    while True:
        page = scenes.read_scene_window(cid, sid, 4, before)
        seen = _contents(page) + seen
        if not page["has_older"]:
            break
        before = page["offset"]
    assert seen == [f"post {i}" for i in range(10)]


def test_window_offsets_are_the_indices_edit_message_takes(monkeypatch, tmp_path):
    """A windowed client addresses a message by the same index an unwindowed
    one does — otherwise an edit made from page 2 lands on the wrong post."""
    cid, sid = _scene_of(monkeypatch, tmp_path, 8)
    page = scenes.read_scene_window(cid, sid, 3, before=5)
    assert _contents(page) == ["post 2", "post 3", "post 4"]
    scenes.edit_message(cid, sid, page["offset"], "rewritten")
    assert scenes.read_scene(cid, sid)["messages"][2]["content"] == "rewritten"


def test_window_larger_than_the_transcript_is_the_whole_transcript(monkeypatch, tmp_path):
    cid, sid = _scene_of(monkeypatch, tmp_path, 3)
    page = scenes.read_scene_window(cid, sid, 50)
    assert _contents(page) == ["post 0", "post 1", "post 2"]
    assert (page["offset"], page["total"], page["has_older"]) == (0, 3, False)


def test_window_of_an_empty_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Empty")
    page = scenes.read_scene_window(cid, sid, 20)
    assert page["messages"] == []
    assert (page["offset"], page["total"], page["has_older"]) == (0, 0, False)


def test_window_clamps_a_cursor_past_either_end(monkeypatch, tmp_path):
    cid, sid = _scene_of(monkeypatch, tmp_path, 5)
    assert _contents(scenes.read_scene_window(cid, sid, 2, before=99)) == ["post 3", "post 4"]
    top = scenes.read_scene_window(cid, sid, 2, before=0)
    assert top["messages"] == [] and top["has_older"] is False
    assert scenes.read_scene_window(cid, sid, 2, before=-3)["messages"] == []


def test_window_reports_a_user_turn_outside_it(monkeypatch, tmp_path):
    """Reroll eligibility rides on this: the window holds only assistant posts,
    but the transcript opened with a player turn, so the run below IS an answer
    to something and regenerate will accept it."""
    cid, sid = _scene_of(monkeypatch, tmp_path, 6)
    page = scenes.read_scene_window(cid, sid, 1)
    assert [m["role"] for m in page["messages"]] == ["assistant"]
    assert page["has_user_message"] is True


def test_window_of_an_all_assistant_transcript_reports_no_user_turn(monkeypatch, tmp_path):
    """An offscreen scene never stores a player turn however long it runs, so
    "there is history above the window" must not be read as one existing."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Offscreen", pcless=True)
    for i in range(4):
        scenes.append_message(cid, sid, "assistant", f"narration {i}")
    page = scenes.read_scene_window(cid, sid, 2)
    assert page["has_older"] is True
    assert page["has_user_message"] is False


def test_window_of_an_unknown_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene_window(cid, "nope", 10)


# ---- label_preserved is a promise about the ROUND TRIP (#59 review) ----

def _round_trip(name):
    """The speaker a stored block parses back to, via the same two functions
    the real reader uses."""
    from grimoire.store.scenes import serialize
    block = serialize._block("assistant", name, "Some dialogue.")
    markers = serialize._markers(block)
    assert len(markers) == 1, f"{name!r} produced {len(markers)} markers"
    return serialize._speaker_and_role(markers[0], frozenset())


def test_label_preserved_means_the_speaker_survives_a_round_trip():
    """The predicate exists so callers can reason about a character by their
    transcript label, so "preserved" has to mean the speaker comes back — not
    merely that a regex liked it. `$` used to match before a trailing newline,
    so "Aese\\n" passed and `_label` emitted a marker split across two lines
    that nothing could parse: the message folded into the previous speaker."""
    from grimoire.store.scenes import serialize

    for name in ["Aese", "Aese Vane", "Winifred (the elder)", "Zoë-Ann", "A" * 64]:
        assert serialize.label_preserved(name), name
        assert _round_trip(name) == (name, "assistant")

    for name in ["Aese\n", "Aese\r\n", "Aese\r", "Aese\nVane", "A" * 65,
                 "Aese *the Grey*", "", None, "You", "Grimoire",
                 # RESERVED IN SUB-SPEAKER FORM. `_MARKER` splits a trailing
                 # " (...)" off as the sub, and `_speaker_and_role` hands a
                 # reserved base's message to that sub -- so these come back as
                 # "Alice"/"Bob", and the "You" one comes back as the PLAYER,
                 # filing an NPC's dialogue under the user.
                 "Grimoire (Alice)", "You (Bob)"]:
        assert not serialize.label_preserved(name), repr(name)


def test_no_name_is_preserved_unless_it_actually_round_trips():
    """The guard against this whole class: rather than trust the enumeration
    above, take names that probe each rule and assert the invariant directly --
    `label_preserved` is TRUE only where serialize-then-parse gives the name
    back. Both defects found here (a trailing newline, a reserved sub-speaker)
    were cases where the predicate said yes and the round trip disagreed."""
    from grimoire.store.scenes import serialize

    probes = ["Aese", "Aese Vane", "Winifred (the elder)", "A (B) (C)", "Zoë-Ann",
              "A" * 64, "A" * 65, "Aese\n", "Aese\r\n", "Aese\r", "Aese\nVane",
              "Aese *the Grey*", "You", "Grimoire", "You (Bob)", "Grimoire (Alice)",
              "(Alice)", "Aese (", "Aese )", ""]
    for name in probes:
        block = serialize._block("assistant", name, "Some dialogue.")
        markers = serialize._markers(block)
        got = (serialize._speaker_and_role(markers[0], frozenset())
               if len(markers) == 1 else None)
        assert serialize.label_preserved(name) == (got == (name, "assistant")), \
            f"{name!r}: preserved={serialize.label_preserved(name)} but round trip gave {got}"


def test_a_name_that_is_not_preserved_falls_back_to_the_role_label():
    """...and the fallback is what keeps the transcript parseable at all. The
    line reads as unstamped assistant prose, which is wrong-but-legible; the
    unfixed alternative was a block no marker matched."""
    assert _round_trip("Aese\n") == (None, "assistant")


def test_a_prefix_boundary_is_checked_in_the_lowercased_name():
    """Casing is not length-preserving -- "İ".lower() is two code points -- so
    indexing the ORIGINAL name by the normalized prefix's length lands past the
    boundary. "İpek" then failed to name "İpek Yılmaz", which reads as ambiguity
    and skips that character's voice checks with no competing speaker at all."""
    from grimoire.store.scenes import serialize

    assert serialize.match_name("İpek", ["İpek Yılmaz"]) == "İpek Yılmaz"
    assert not serialize.confusable("İpek Yılmaz", ["İpek Yılmaz", "You", "Grimoire"])
    # ...and a real collision in the same alphabet is still ambiguous
    assert serialize.match_name("İpek", ["İpek Yılmaz", "İpek Demir"]) is None
    assert serialize.confusable("İpek Yılmaz", ["İpek Yılmaz", "İpek Demir"])
    # ASCII behaviour is unchanged
    assert serialize.match_name("Winifred", ["Winifred Vance"]) == "Winifred Vance"
    assert serialize.match_name("Winifred", ["Winifred Vance", "Winifred Vale"]) is None


def test_a_whole_name_that_shadows_a_longer_ones_prefix_is_confusable():
    """`match_name` breaks a tie by exact match, so the label "Mara" resolves to
    the cast member literally named "Mara" even with "Mara Vell" beside her --
    cleanly, and wrongly, because the model writing `**Mara:**` may have been
    shortening the longer name. Asking only "where does this label land?" cannot
    see that; the question is also "who else could have written it?"."""
    from grimoire.store.scenes import serialize

    roster = ["Mara", "Mara Vell"]
    # the resolver is unchanged -- exact match still wins, as every other caller
    # (role assignment, drift canonicalization) needs it to
    assert serialize.match_name("Mara", roster) == "Mara"
    # ...but neither actor owns a label unambiguously, so neither is usable as an
    # identity: "Mara" has none at all, and "Mara Vell" only if the model never
    # abbreviates, which is exactly what it does
    assert serialize.confusable("Mara", roster)
    assert serialize.confusable("Mara Vell", roster)
    # alone, the long name is fine -- its own prefix resolves to it
    assert not serialize.confusable("Mara Vell", ["Mara Vell", "You", "Grimoire"])
    # and a shared STEM is not a shared label: "Marabel" has no word-boundary
    # prefix "Mara", so that label can only ever have meant Mara
    assert not serialize.confusable("Mara", ["Mara", "Marabel"])


def test_a_name_the_transcript_cannot_hold_is_never_expanded():
    """`_labels` allocates a prefix per separator, so a card name of a few
    thousand alternating letters and spaces is quadratic -- and it runs on the
    generation hot path, before anything checks whether the character even has a
    drift flag. An imported card can carry one, so this is reachable.

    The bound falls out of the meaning rather than being bolted on: a name the
    serializer cannot write never appears as a transcript label (its blocks read
    as "Grimoire"), so it owns no label, and `label_preserved` already caps a
    writable one at 64 characters."""
    from grimoire.store.scenes import serialize

    monstrous = "A " * 4000
    expanded = []
    real = serialize._labels
    try:
        serialize._labels = lambda n: (expanded.append(n), real(n))[1]
        # as the target: unusable as an identity, and rejected before expansion
        assert serialize.confusable(monstrous, [monstrous, "You", "Grimoire"])
        # as a BYSTANDER: it cannot own a label, so it makes no one else ambiguous
        assert not serialize.confusable("Mara", ["Mara", monstrous, "You", "Grimoire"])
    finally:
        serialize._labels = real
    assert monstrous not in expanded
    # Target and bystander ask different questions, and the reserved labels are
    # where they come apart. A character CARDED "You" is unusable -- the
    # serializer writes their lines as "Grimoire" -- while the reserved label
    # "You" in the roster still owns its label, because that is precisely what
    # the transcript writes for the player.
    assert serialize.confusable("You", ["You", "Mara"])
    assert serialize.confusable("You (Bob)", ["You (Bob)", "Mara"])
    assert not serialize.confusable("Mara", ["Mara", "You", "Grimoire"])
    assert serialize.confusable("You Vell", ["You Vell", "You", "Grimoire"])
