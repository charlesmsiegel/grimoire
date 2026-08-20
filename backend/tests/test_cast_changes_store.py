"""In-turn cast-change detection (#97) and unknown-name routing (#98).

Every test here drives `appearances.cast_changes`, which reads the newest turn
and proposes -- it never writes. The confirm half is the ordinary cast routes,
covered in `test_routes.py`.
"""

import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, entities, pcs, scenes, worlds


@pytest.fixture
def campaign(monkeypatch, tmp_path):
    """A world with two characters, a campaign, and a scene Seraphine is in."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine")
    characters.create_character(wroot, "Mara")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    return cid, sid


def _turn(cid, sid, post, reply, speaker="Seraphine"):
    """One exchange, persisted the way a real turn is.

    `append_reply` rather than `append_message`, because it is the only entry
    point that records a turn boundary -- and the boundary is what the scan
    counts back through. Driving these tests with bare appends would have
    exercised the untracked FALLBACK throughout and left the tracked path,
    which is every turn in production, to one test."""
    scenes.append_message(cid, sid, "user", post)
    scenes.append_reply(cid, sid, [{"speaker": speaker, "content": reply}])


# ---- enter ----
def test_a_character_named_in_the_new_turn_is_an_enter_candidate(campaign):
    cid, sid = campaign
    _turn(cid, sid, "Who else is here?", "Mara is already at the table.")
    assert ap.cast_changes(cid, sid)["enter"] == [
        {"kind": "characters", "id": "mara", "name": "Mara", "mentioned_by": ["Seraphine"]}]


def test_the_players_own_post_counts_as_this_turns_prose(campaign):
    """A name the player introduces is this turn's news too, not only the reply."""
    cid, sid = campaign
    _turn(cid, sid, "I look for Mara in the crowd.", "The crowd parts.")
    assert [e["id"] for e in ap.cast_changes(cid, sid)["enter"]] == ["mara"]
    assert ap.cast_changes(cid, sid)["enter"][0]["mentioned_by"] == ["You"]


def test_a_cast_member_is_never_an_enter_candidate(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "Seraphine looks up.")
    assert ap.cast_changes(cid, sid)["enter"] == []


def test_only_the_newest_turn_is_scanned(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "Mara waves from the door.")
    _turn(cid, sid, "and now?", "The room is quiet.")
    assert ap.cast_changes(cid, sid)["enter"] == []


def test_a_dismissed_character_is_not_offered_again(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "Mara waves from the door.")
    scenes.add_dismissed(cid, sid, "mara")
    assert ap.cast_changes(cid, sid)["enter"] == []


def test_a_transition_line_never_re_suggests_the_actor_it_reports(campaign):
    """`leave` narrates '*Mara leaves the scene.*'. Reading that back would
    re-offer Mara the moment she was removed, forever."""
    cid, sid = campaign
    _turn(cid, sid, "hello", "The hall is loud.")
    ap.appear(cid, sid, "characters", "mara", "default", "npc")
    ap.leave(cid, sid, "characters", "mara")
    changes = ap.cast_changes(cid, sid)
    assert changes["enter"] == [] and changes["unknown"] == []


# ---- leave ----
def test_a_departure_cue_proposes_removing_the_cast_member(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "Seraphine slips out through the side door.")
    assert ap.cast_changes(cid, sid)["leave"] == [
        {"kind": "characters", "id": "seraphine", "name": "Seraphine",
         "quote": "Seraphine slips out through the side door."}]


def test_the_nearest_name_before_the_cue_is_the_one_that_left(campaign):
    cid, sid = campaign
    ap.appear(cid, sid, "characters", "mara", "default", "npc")
    _turn(cid, sid, "hello", "Mara watched as Seraphine slipped out.")
    assert [d["id"] for d in ap.cast_changes(cid, sid)["leave"]] == ["seraphine"]


def test_left_as_a_direction_is_not_a_departure(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "Seraphine raised her left hand.")
    assert ap.cast_changes(cid, sid)["leave"] == []


def test_leaves_as_a_noun_is_not_a_departure(campaign):
    """A determiner in front of the cue settles which part of speech it is."""
    cid, sid = campaign
    _turn(cid, sid, "hello", "Seraphine watched the leaves fall past the window.")
    assert ap.cast_changes(cid, sid)["leave"] == []


def test_a_cue_with_no_name_in_front_of_it_proposes_nothing(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "The tide went out and the lanterns left the quay dark.")
    assert ap.cast_changes(cid, sid)["leave"] == []


def test_an_offscreen_scenes_whole_turn_is_read_not_just_its_last_post(monkeypatch, tmp_path):
    """An offscreen scene is all-assistant, so "everything after the last player
    post" finds no boundary in it at all. The generation's own recorded extent
    does (#96's per-NPC posts land as several)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine")
    characters.create_character(wroot, "Mara")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch", pcless=True)
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_reply(cid, sid, [
        {"speaker": "Narrator", "content": "Mara arrives at the gate."},
        {"speaker": "Seraphine", "content": "Seraphine slips out the back."}])

    changes = ap.cast_changes(cid, sid)
    assert [e["id"] for e in changes["enter"]] == ["mara"]      # the FIRST post
    assert [d["id"] for d in changes["leave"]] == ["seraphine"]  # and the last


def test_an_older_player_post_does_not_widen_the_window(monkeypatch, tmp_path):
    """Model-only turns can follow a player post; reaching back to it would
    re-offer every name since."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "Mara waves from the door."}])
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "The hall empties."}])
    assert ap.cast_changes(cid, sid)["enter"] == []


def test_a_player_actor_is_never_proposed_for_removal(monkeypatch, tmp_path):
    """Which posts parse as player-side is derived from the scene's player
    names, so dropping one rewrites the transcript's own history."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    pcs.create_pc(worlds.world_root(wid), "Elara", [])
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    scenes.append_message(cid, sid, "assistant", "Elara walks away into the fog.",
                          speaker="Narrator")
    assert ap.cast_changes(cid, sid)["leave"] == []


# ---- unknown names (#98) ----
def test_a_name_matching_no_record_lands_in_the_unknown_bucket(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "The innkeeper's daughter Winifred brings the bowls.")
    assert ap.cast_changes(cid, sid)["unknown"] == [
        {"name": "Winifred", "mentioned_by": ["Seraphine"]}]


def test_a_title_in_front_of_an_unknown_name_is_stripped(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "They bow to Lady Winifred.")
    assert [u["name"] for u in ap.cast_changes(cid, sid)["unknown"]] == ["Winifred"]


def test_known_records_are_not_unknown_names(campaign):
    cid, sid = campaign
    entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Quay", "")
    _turn(cid, sid, "hello", "At the Saltmarch Quay, Mara greets Lady Seraphine.")
    assert ap.cast_changes(cid, sid)["unknown"] == []


def test_a_word_that_is_only_ever_sentence_initial_is_not_a_name(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "Meanwhile the fog thickened. Rain followed it in.")
    assert ap.cast_changes(cid, sid)["unknown"] == []


def test_a_dismissed_unknown_name_stays_dismissed(campaign):
    cid, sid = campaign
    _turn(cid, sid, "hello", "The girl Winifred brings the bowls.")
    scenes.add_dismissed(cid, sid, "winifred")   # the slug the create route would use
    assert ap.cast_changes(cid, sid)["unknown"] == []


def test_the_unknown_bucket_is_capped(campaign):
    cid, sid = campaign
    crowd = " ".join(f"Then in came {n}." for n in
                     ("Alder", "Bracken", "Cobble", "Dunmore", "Everly", "Fennick",
                      "Garrow", "Hollis"))
    # each name also appears mid-sentence, so all eight are real candidates
    _turn(cid, sid, "hello", f"{crowd} The hall held Alder, Bracken, Cobble, Dunmore, "
                             "Everly, Fennick, Garrow and Hollis.")
    unknown = ap.cast_changes(cid, sid)["unknown"]
    assert len(unknown) == ap.detect.MAX_UNKNOWN
    assert [u["name"] for u in unknown] == ["Alder", "Bracken", "Cobble", "Dunmore",
                                            "Everly", "Fennick"]


def test_a_scene_with_no_turn_boundaries_falls_back_to_the_trailing_run(campaign):
    """Scenes written before turn tracking, and any whose `turn_sizes` no longer
    fits, have no boundary to count back through. The trailing model run is the
    same fallback reroll takes for them."""
    cid, sid = campaign
    scenes.append_message(cid, sid, "user", "hello")
    scenes.append_message(cid, sid, "assistant", "Mara waves from the door.", speaker="Narrator")
    assert [e["id"] for e in ap.cast_changes(cid, sid)["enter"]] == ["mara"]


def test_a_scene_with_no_posts_yet_proposes_nothing(campaign):
    cid, sid = campaign
    assert ap.cast_changes(cid, sid) == {"enter": [], "leave": [], "unknown": []}
