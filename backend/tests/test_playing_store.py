import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, greetings, pcs, playing, scenes, tags, worlds


def _world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("W")


def _campaign_after_seed(wid):
    """Create the campaign (and one scene) AFTER the world is seeded — the
    campaign copies the world at creation and play reads only the copy."""
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return cid, sid


def _campaign(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid, sid = _campaign_after_seed(wid)
    return wid, cid, sid


def test_played_roundtrip(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    assert playing.read_played(cid) == set()
    playing._mark_played(cid, "g1")
    playing._mark_played(cid, "g1")  # idempotent
    playing._mark_played(cid, "g2")
    assert playing.read_played(cid) == {"g1", "g2"}


def test_player_tags_unions_player_pcs_only(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    for t in ("student", "sailor"):
        tags.add_tag(wroot, t)
    pcs.create_pc(wroot, "Elara", ["student"])
    pcs.create_pc(wroot, "Bryn", ["sailor"])
    cid, sid = _campaign_after_seed(wid)
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    ap.appear(cid, sid, "pcs", "bryn", "default", "player")
    assert playing.player_tags(cid) == {"student", "sailor"}


def test_available_greetings_end_to_end(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    tags.add_tag(wroot, "vip")
    g = greetings.create_greeting(wroot, "Gala", "c", "v", requires_tags=["vip"])
    pcs.create_pc(wroot, "Elara", ["vip"])
    cid, sid = _campaign_after_seed(wid)
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is False
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True


def test_start_from_greeting_seeds_appears_marks(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Seraphine")
    card["data"].update(description="keeper")
    characters.create_character(wroot, "Seraphine", "default", card)
    pcs.create_pc(wroot, "Elara", [])
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default",
                                  body="{{char}} greets {{user}}.")
    cid, sid = _campaign_after_seed(wid)
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    sid = playing.start_from_greeting(cid, sid, g)
    scene = scenes.read_scene(cid, sid)
    assert scene["messages"][0]["role"] == "assistant"
    assert scene["messages"][0]["content"] == "Seraphine greets Elara."   # tokens substituted
    assert g in playing.read_played(cid)
    assert ap.is_appeared(cid, "characters", "seraphine")
    # second start on a now-nonempty scene -> PlayError
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)


def test_start_from_greeting_casts_all_present(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara", "main", characters.blank_card("Mara"))
    characters.create_character(wroot, "Rowan", "main", characters.blank_card("Rowan"))
    pcs.create_pc(wroot, "Elara", [])
    g = greetings.create_greeting(wroot, "Arrival: Mara & Rowan", "mara", "main",
                                  body="Mara and Rowan arrive.", present=["mara", "rowan"])
    cid, sid = _campaign_after_seed(wid)
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    playing.start_from_greeting(cid, sid, g)
    # both present characters cast as NPCs, each at their default version
    assert ap.is_appeared(cid, "characters", "mara")
    assert ap.is_appeared(cid, "characters", "rowan")


def test_start_from_greeting_no_character_casts_nobody(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    pcs.create_pc(wroot, "Elara", [])
    g = greetings.create_greeting(wroot, "Cold open", "", "", body="The tavern falls silent.")
    cid, sid = _campaign_after_seed(wid)
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    sid = playing.start_from_greeting(cid, sid, g)
    scene = scenes.read_scene(cid, sid)
    assert scene["messages"][0]["content"] == "The tavern falls silent."
    assert [a for a in ap.scene_cast(cid, sid) if a["kind"] == "characters"] == []


def test_start_unavailable_raises(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    tags.add_tag(wroot, "vip")
    g = greetings.create_greeting(wroot, "Gala", "s", "default", requires_tags=["vip"])
    cid, sid = _campaign_after_seed(wid)
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)


def test_start_from_greeting_stamps_greeting(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default", body="Hi.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.read_scene(cid, sid)["meta"]["greeting"] == g


def test_start_from_greeting_takes_greeting_title(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "A Chance Meeting", "seraphine", "default", body="Hi.")
    cid, sid = _campaign_after_seed(wid)
    new_sid = playing.start_from_greeting(cid, sid, g)
    assert new_sid != sid and "a-chance-meeting" in new_sid
    scene = scenes.read_scene(cid, new_sid)
    assert scene["meta"]["title"] == "A Chance Meeting"
    assert scene["meta"]["greeting"] == g            # stamp survived the rename


def test_stamp_greeting_missing_scene_raises(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.stamp_greeting(cid, "nope", "g1")


def test_available_greetings_after_flags_and_sorts_unlocked(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "Omega", "s", "default", body="O.")
    g3 = greetings.create_greeting(wroot, "Middle", "s", "default", body="M.")
    greetings.set_edges(wroot, g1, leads_to=[g3])
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g1)
    got = playing.available_greetings(cid, after=sid)
    assert got[0]["id"] == g3                      # the unlocked greeting sorts first
    assert {x["id"]: x["unlocked"] for x in got} == {g1: False, g2: False, g3: True}


def test_available_greetings_after_without_stamp_all_false(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    got = playing.available_greetings(cid, after=sid)   # scene never started from a greeting
    assert [x["unlocked"] for x in got] == [False]


def test_available_greetings_no_after_has_unlocked_false(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    cid, _sid = _campaign_after_seed(wid)
    assert [x["unlocked"] for x in playing.available_greetings(cid)] == [False]


def test_available_greetings_unknown_after_raises(monkeypatch, tmp_path):
    wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        playing.available_greetings(cid, after="nope")


def test_read_marks_migrates_legacy_list(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "played.json").write_text('["g1"]', encoding="utf-8")
    marks = playing.read_marks(cid)
    assert marks == {"played": {"g1"}, "completed": set(), "skipped": set()}


def test_mark_greeting_roundtrip(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    g = greetings.create_greeting(croot, "Gala", "c", "v")
    playing.mark_greeting(cid, g, "completed")
    assert playing.read_marks(cid)["completed"] == {g}
    playing.mark_greeting(cid, g, "skipped")
    marks = playing.read_marks(cid)
    assert marks["skipped"] == {g} and marks["completed"] == set()
    playing.mark_greeting(cid, g, "none")
    assert playing.read_marks(cid)["skipped"] == set()
    with pytest.raises(playing.PlayError):
        playing.mark_greeting(cid, g, "bogus")
    with pytest.raises(greetings.GreetingNotFound):
        playing.mark_greeting(cid, "nope", "completed")


def test_mark_greeting_refuses_played(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    g = greetings.create_greeting(croot, "Gala", "c", "v")
    playing._mark_played(cid, g)
    with pytest.raises(playing.PlayError):
        playing.mark_greeting(cid, g, "completed")


def test_mark_played_clears_offscreen_marks(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    g = greetings.create_greeting(croot, "Gala", "c", "v")
    playing.mark_greeting(cid, g, "completed")
    playing._mark_played(cid, g)
    marks = playing.read_marks(cid)
    assert g in marks["played"] and g not in marks["completed"]


def test_campaign_play_reads_live_world_greeting_until_deleted(monkeypatch, tmp_path):
    """A thin campaign never copies a greeting up front; play reads the world's
    greeting live until something materializes it. (Under the old full-copy
    campaigns, the campaign's own snapshot stayed isolated from subsequent world
    edits/deletion; that guarantee no longer holds for an unplayed greeting.)"""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "Open", "s", "default", body="Original.")
    cid, sid = _campaign_after_seed(wid)
    greetings.update_greeting(wroot, g, body="Edited in world.")   # after the fork
    assert {x["id"] for x in playing.available_greetings(cid)} == {g}
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.read_scene(cid, sid)["messages"][0]["content"] == "Edited in world."

    g2 = greetings.create_greeting(wroot, "Second", "s", "default", body="B.")
    sid2 = scenes.create_scene(cid, "S2")
    greetings.delete_greeting(wroot, g2)   # never materialized -> gone entirely
    assert g2 not in {x["id"] for x in playing.available_greetings(cid)}
    with pytest.raises(greetings.GreetingNotFound):
        playing.start_from_greeting(cid, sid2, g2)


def test_available_greetings_reports_marks_and_hides_skipped(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "B", "s", "default", body="B.")
    g3 = greetings.create_greeting(wroot, "C", "s", "default", body="C.")
    greetings.set_edges(wroot, g1, leads_to=[g3])
    cid, _sid = _campaign_after_seed(wid)
    playing.mark_greeting(cid, g1, "completed")   # unlocks g3 like a play would
    playing.mark_greeting(cid, g2, "skipped")
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert g2 not in got
    assert got[g1]["mark"] == "completed"
    assert got[g3]["mark"] is None and got[g3]["available"] is True


def test_start_from_greeting_locked_version_wins(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young", characters.blank_card("Mara"))
    characters.create_version(wroot, char_id, "veteran", characters.blank_card("Mara"))
    g = greetings.create_greeting(wroot, "Open", char_id, "young", body="Hi.")
    cid, sid = _campaign_after_seed(wid)
    other = scenes.create_scene(cid, "S0")
    ap.appear(cid, other, "characters", char_id, "veteran", "npc")  # lock veteran first
    playing.start_from_greeting(cid, sid, g)                        # greeting says young
    assert ap.locked_version(cid, "characters", char_id) == "veteran"


def test_start_from_greeting_refuses_campaign_purged_version(monkeypatch, tmp_path):
    """An inherited greeting names a version the campaign has purged from a
    materialized (but unlocked) actor: a materialized actor's version set is
    authoritative, so the start must fail rather than revive it from the world."""
    from grimoire.store import overlay
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young", characters.blank_card("Mara"))
    characters.create_version(wroot, char_id, "corrupted", characters.blank_card("Mara"))
    g = greetings.create_greeting(wroot, "Open", char_id, "corrupted", body="Hi.")
    cid, sid = _campaign_after_seed(wid)
    # materialize the actor campaign-side, then delete the greeting's version
    root = overlay.ensure_actor_writable(cid, "characters", char_id)
    characters.delete_version(root, char_id, "corrupted")
    assert not ap.is_appeared(cid, "characters", char_id)   # unlocked
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)
    # the purged version was not resurrected in the campaign copy
    assert characters.card_hash(root, char_id, "corrupted") is None
