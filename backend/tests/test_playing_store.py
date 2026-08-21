import threading

import pytest

from grimoire.store import appearances as ap
from grimoire.store import (
    campaigns,
    characters,
    entities,
    greetings,
    overlay,
    pcs,
    playing,
    scenes,
    suggest,
    tags,
    worlds,
)


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


def test_start_from_greeting_expands_roll_macro(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default",
                                  body="You roll {{roll:1d20}} to begin.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    content = scenes.read_scene(cid, sid)["messages"][0]["content"]
    assert "{{roll" not in content
    n = int(content.removeprefix("You roll ").removesuffix(" to begin."))
    assert 1 <= n <= 20


def test_start_from_greeting_expands_date_macro_against_a_prior_datetime(monkeypatch, tmp_path):
    """The confirm pane sets the scene's date BEFORE calling start_from_greeting
    specifically so {{date}} in the greeting body expands against a real value
    instead of the empty one a dateless scene would leave. Pin the payoff, not
    just the call order: a greeting body containing {{date}} must expand
    against the date the scene was given before seeding."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default", body="It is {{date}}.")
    cid, sid = _campaign_after_seed(wid)
    sid = scenes.set_datetime(cid, sid, "2026-06-29")["id"]
    sid = playing.start_from_greeting(cid, sid, g)
    content = scenes.read_scene(cid, sid)["messages"][0]["content"]
    assert content == "It is 29 June 2026."


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


def test_start_from_greeting_records_one_turn_per_parsed_block(monkeypatch, tmp_path):
    """A greeting written as several **Name:** blocks reads back as several
    messages, so the turn it records must have that many blocks. Storing it as
    one unsplit segment claims turn_sizes [1] while the file parses as three —
    and drift then measures only the trailing block of the very turn that sets
    the scene's length anchor."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    body = ('The hall is cold.\n\n'
            '**Seraphine:** "You came."\n\n'
            '**Mara:** "I did."')
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default",
                                  present=["mara"], body=body)
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    messages = scenes.read_scene(cid, sid)["messages"]
    assert [m.get("speaker") for m in messages] == [None, "Seraphine", "Mara"]
    assert scenes.get_turn_sizes(cid, sid) == [len(messages)] == [3]


def test_start_from_greeting_keeps_a_single_block_greeting_at_one(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default",
                                  body="The hall is cold.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_played_greeting_is_not_available(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "B", "s", "default", body="B.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g1)
    playing.mark_greeting(cid, g2, "completed")
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert got[g1]["available"] is False and "already played" in got[g1]["reasons"]
    assert got[g2]["available"] is False and "already played" in got[g2]["reasons"]


def test_replaying_a_played_greeting_raises(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g)
    sid2 = scenes.create_scene(cid, "Second")
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid2, g)


def test_mark_played_runs_after_the_body_is_appended(monkeypatch, tmp_path):
    """A failure before append_reply must leave the greeting startable: that is
    the whole reason the mark moved."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    # append_reply, not expand_macros: patching the LAST write before the mark
    # is what makes the test reject a mark placed anywhere earlier. A mark moved
    # only past expansion would still pass an expand_macros patch.
    def _explode(*a, **k):
        raise RuntimeError("append blew up")
    monkeypatch.setattr(playing.scenes_write, "append_reply", _explode)
    with pytest.raises(RuntimeError):
        playing.start_from_greeting(cid, sid, g)
    assert g not in playing.read_played(cid)
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True


def test_stamping_scene_finds_the_scene_that_played_a_greeting(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    assert playing.stamping_scene(cid, g) is None
    sid = playing.start_from_greeting(cid, sid, g)
    assert playing.stamping_scene(cid, g) == sid


def test_orphaned_played_mark_can_be_cleared(monkeypatch, tmp_path):
    """The scene that justified the mark is gone (an interrupted start, cleaned
    up by the chooser), so the mark is orphaned and must be recoverable."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    scenes.delete_scene(cid, sid)
    playing.mark_greeting(cid, g, "none")
    assert g not in playing.read_played(cid)
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True


def test_greeting_candidates_omits_played_greetings(monkeypatch, tmp_path):
    """The ranker filters on `available`, so it inherits the fix — assert it
    rather than assuming, because the ranker is the second place #315 leaked."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    ids = [greetings.create_greeting(wroot, n, "s", "default", body=f"{n}.")
           for n in ("A", "B", "C", "D")]
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, ids[0])
    assert ids[0] not in {c["id"] for c in suggest.greeting_candidates(cid)}


def test_played_mark_still_refuses_while_a_scene_stamps_it(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g)
    with pytest.raises(playing.PlayError):
        playing.mark_greeting(cid, g, "none")


def test_concurrent_starts_of_the_same_greeting_only_one_wins(monkeypatch, tmp_path):
    """#318 made a played greeting unavailable and a replay raise, which
    raised the cost of a pre-existing gap: `start_from_greeting` checked
    availability and later read-modify-wrote `played.json` with nothing
    locking the two together, so two concurrent starts of the same
    never-before-played greeting could both pass the guard and both report
    success -- this is the race `store.playing` joining the lock domain
    closes.

    A `threading.Barrier` forces both threads past the unlocked availability
    check and the scene stamp, releasing them into `expand_macros`
    (deliberately unlocked -- it resolves the campaign's calendar provider,
    user-authored code) at the same instant, so both reach the locked
    recheck-and-mark as genuine racers on every run rather than by
    scheduling luck. The greeting casts no character: two threads racing to
    materialize the *same* actor into the campaign is a real gap of its own
    (`store.appearances` is still `UNREVIEWED`), but it is not the gap this
    test is for, so it is avoided rather than incidentally exercised.
    """
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    g = greetings.create_greeting(wroot, "A", "", "", body="A.")
    cid, sid_a = _campaign_after_seed(wid)
    sid_b = scenes.create_scene(cid, "Second")

    barrier = threading.Barrier(2)
    real_expand = playing.context_macros.expand_macros

    def _synced_expand(*a, **k):
        barrier.wait(timeout=5)
        return real_expand(*a, **k)

    monkeypatch.setattr(playing.context_macros, "expand_macros", _synced_expand)

    results: dict[str, object] = {}

    def worker(name, sid):
        try:
            results[name] = playing.start_from_greeting(cid, sid, g)
        except playing.PlayError as exc:
            results[name] = exc

    t1 = threading.Thread(target=worker, args=("a", sid_a))
    t2 = threading.Thread(target=worker, args=("b", sid_b))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive(), "a worker thread never finished"

    outcomes = [results["a"], results["b"]]
    successes = [o for o in outcomes if isinstance(o, str)]
    failures = [o for o in outcomes if isinstance(o, playing.PlayError)]
    assert len(successes) == 1, outcomes
    assert len(failures) == 1, outcomes
    assert "claimed by a concurrent start" in str(failures[0])
    assert playing.read_played(cid) == {g}


def _lock_is_free(cid) -> bool:
    """Whether some *other* thread could take the campaign lock right now.

    Probing from this thread would prove nothing: the lock is reentrant, so a
    holder's own `acquire` always succeeds. Mirrors the helper of the same
    name in `test_locks_store.py` -- kept local rather than imported so this
    file's races don't reach into that module's private test scaffolding.
    """
    seen = []

    def probe():
        lock = playing.locks.campaign_lock(cid)
        got = lock.acquire(timeout=0.3)
        seen.append(got)
        if got:
            lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "the probe never finished"
    return seen[0]


def test_mark_greeting_none_holds_the_lock_across_its_scan(monkeypatch, tmp_path):
    """`mark_greeting("none")` scans every scene for one stamping `gid`
    before clearing an orphaned mark -- #318's accepted risk named this scan
    as a TOCTOU: it can find nothing and clear the mark while a concurrent
    `start_from_greeting` is mid-flight, about to stamp a scene with the
    same `gid`. `stamp_greeting` (what a concurrent start does at that
    point) takes the campaign lock itself (`scenes._serialized`), so the fix
    only holds if `mark_greeting`'s lock spans the WHOLE scan-then-write,
    not just the final write -- a lock taken only around `_write_marks`
    would still let a stamp land between the scan and that write.

    Proven directly, the same way `test_locks_store.
    test_calendar_plugin_code_never_runs_under_the_campaign_lock` proves the
    opposite (that a lock is NOT held across a call): probe whether another
    thread can take the lock at the exact moment the scan runs.
    """
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    scenes.delete_scene(cid, sid)   # orphan the mark, as in the recovery test

    free = []
    real_list_scenes = playing.scenes_read.list_scenes

    def _watched(*a, **k):
        free.append(_lock_is_free(cid))
        return real_list_scenes(*a, **k)

    monkeypatch.setattr(playing.scenes_read, "list_scenes", _watched)

    playing.mark_greeting(cid, g, "none")

    assert free, "the scan never ran -- the test proves nothing"
    assert not any(free), "a concurrent writer was not excluded during the scan"
    assert g not in playing.read_played(cid)  # the clear itself still went through


def _world_with_location(monkeypatch, tmp_path, name="The Counting House"):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    eid = entities.create_entity(wroot, "locations", name)
    return wid, wroot, eid


def test_start_from_greeting_seeds_the_scenes_location(monkeypatch, tmp_path):
    """A greeting is a scene opener and play happens somewhere (#218): the
    setting it names becomes the scene's, silently -- an empty scene has no
    location to move from, so `set_location` writes no transition line."""
    wid, wroot, loc = _world_with_location(monkeypatch, tmp_path)
    g = greetings.create_greeting(wroot, "At the Ledger", "seraphine", "default",
                                  body="She looks up.", location=loc)
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.get_location_history(cid, sid) == [loc]
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == ["She looks up."]


def test_start_from_greeting_keeps_a_location_the_scene_already_has(monkeypatch, tmp_path):
    """Seeding, not overriding. The confirm form applies the reader's own pick
    before it seeds, so a greeting's location is the default they were offered
    and already had the chance to change -- re-imposing it here would make that
    picker a decoration."""
    wid, wroot, loc = _world_with_location(monkeypatch, tmp_path)
    other = entities.create_entity(wroot, "locations", "The Quay")
    g = greetings.create_greeting(wroot, "At the Ledger", "seraphine", "default",
                                  body="She looks up.", location=loc)
    cid, sid = _campaign_after_seed(wid)
    scenes.set_location(cid, sid, other)
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.get_location_history(cid, sid) == [other]


def test_start_from_greeting_survives_a_location_the_campaign_deleted(monkeypatch, tmp_path):
    """An inherited greeting can name a location this campaign has since
    deleted. The setting is one optional piece of metadata; losing it costs the
    scene nothing it cannot be given by hand, so it must not cost the reader
    the opener itself."""
    wid, wroot, loc = _world_with_location(monkeypatch, tmp_path)
    g = greetings.create_greeting(wroot, "At the Ledger", "seraphine", "default",
                                  body="She looks up.", location=loc)
    cid, sid = _campaign_after_seed(wid)
    overlay.delete_entity(cid, "locations", loc)
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.get_location_history(cid, sid) == []
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == ["She looks up."]


def test_available_greetings_blanks_a_location_the_campaign_deleted(monkeypatch, tmp_path):
    """The confirm form pre-fills its picker straight from this payload (#218),
    and a picker cannot show an id the campaign has no record of -- it would
    render blank while still holding the value, then 409/404 on Create. Every
    other location reaching that form is server-validated; this one has to be
    too."""
    wid, wroot, loc = _world_with_location(monkeypatch, tmp_path)
    g = greetings.create_greeting(wroot, "At the Ledger", "seraphine", "default",
                                  location=loc)
    cid, _sid = _campaign_after_seed(wid)
    assert {x["id"]: x["location"] for x in playing.available_greetings(cid)}[g] == loc
    overlay.delete_entity(cid, "locations", loc)
    assert {x["id"]: x["location"] for x in playing.available_greetings(cid)}[g] == ""


def test_available_greetings_skips_the_location_sweep_when_nothing_names_one(
        monkeypatch, tmp_path):
    """One file read per location, on the scene picker's open path: a world that
    does not use the field must not pay for it."""
    wid, wroot, _loc = _world_with_location(monkeypatch, tmp_path)
    greetings.create_greeting(wroot, "Cold open", "seraphine", "default")
    cid, _sid = _campaign_after_seed(wid)
    calls: list[str] = []
    real = overlay.list_entities

    def spy(c, kind):
        calls.append(kind)
        return real(c, kind)

    monkeypatch.setattr(overlay, "list_entities", spy)
    playing.available_greetings(cid)
    assert "locations" not in calls


def test_start_from_greeting_can_be_told_not_to_seed(monkeypatch, tmp_path):
    """The confirm pane pre-fills its picker from the greeting, so an empty
    location there means the reader CLEARED it -- indistinguishable, from the
    scene alone, from nobody having looked. A caller that has already decided
    says so, and the greeting's location is not put back."""
    wid, wroot, loc = _world_with_location(monkeypatch, tmp_path)
    g = greetings.create_greeting(wroot, "At the Ledger", "seraphine", "default",
                                  body="She looks up.", location=loc)
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g, seed_location=False)
    assert scenes.get_location_history(cid, sid) == []


def test_start_from_greeting_without_a_location_leaves_the_scene_alone(monkeypatch, tmp_path):
    wid, wroot, _loc = _world_with_location(monkeypatch, tmp_path)
    g = greetings.create_greeting(wroot, "Cold open", "seraphine", "default", body="Silence.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    assert scenes.get_location_history(cid, sid) == []
