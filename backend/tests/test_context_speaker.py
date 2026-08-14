"""Who leads the turn in a group scene (#29's active-speaker layer)."""

from grimoire.store.context import speaker
from grimoire.store.scenes import serialize as scenes_serialize

NPCS = ["Seraphine Vale", "Mara Quist", "Winifred Ash"]


def _npc(name, text="..."):
    return {"role": "assistant", "speaker": name, "content": text}


def _player(text):
    return {"role": "user", "speaker": None, "content": text}


# ----------------------------------------------------------------- silence

def test_no_section_below_two_npcs():
    """Turn-taking is a group problem. In a two-hander "Seraphine leads this
    turn" is tokens spent saying what the cast list already said."""
    assert speaker.nominate([], []) is None
    assert speaker.nominate(["Seraphine Vale"], [_player("hello")]) is None


def test_blank_and_non_string_names_are_dropped():
    assert speaker.nominate(["Seraphine Vale", "  ", None], []) is None


def test_a_duplicate_name_is_not_a_second_actor():
    assert speaker.nominate(["Seraphine Vale", "Seraphine Vale"], []) is None


# ------------------------------------------------------------ being named

def test_naming_an_npc_by_full_name_wins():
    hist = [_npc("Seraphine Vale"), _npc("Mara Quist"),
            _player("Winifred Ash, what did you see?")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash" and out["reason"] == "named"


def test_naming_an_npc_by_unique_first_name_wins():
    hist = [_npc("Winifred Ash"), _player("Mara, hold the door.")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Mara Quist" and out["reason"] == "named"


def test_naming_an_npc_beats_the_rotation():
    """Winifred has never spoken, so rotation would pick her; the post names
    Mara, and being singled out outranks having been quiet."""
    hist = [_npc("Seraphine Vale"), _player("Mara, hold the door.")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Mara Quist" and out["reason"] == "named"


def test_a_pending_input_outranks_the_last_stored_post():
    """A director note and an opener prompt are the turn's actual text and are
    never persisted -- which is why `_assemble` already feeds them to world-info
    activation. Ignoring them here would be the same bug one seam over."""
    hist = [_npc("Seraphine Vale"), _player("Mara, hold the door.")]
    out = speaker.nominate(NPCS, hist, pending="Winifred steps out of the dark.")
    assert out["lead"] == "Winifred Ash" and out["reason"] == "named"


def test_a_blank_pending_input_falls_back_to_the_last_post():
    hist = [_npc("Seraphine Vale"), _player("Mara, hold the door.")]
    assert speaker.nominate(NPCS, hist, pending="   ")["lead"] == "Mara Quist"


def test_a_pending_input_naming_nobody_does_not_resurrect_the_last_post():
    """The note is newer, so it REPLACES the post as the text being read --
    it does not fall back to it and nominate whoever the player named a turn
    ago."""
    hist = [_npc("Seraphine Vale"), _player("Mara, hold the door.")]
    out = speaker.nominate(NPCS, hist, pending="The storm reaches the pier.")
    assert out["reason"] == "rotation"


def test_two_npcs_named_in_one_post_singles_out_neither():
    hist = [_npc("Winifred Ash"), _player("Mara and Seraphine, both of you.")]
    assert speaker.nominate(NPCS, hist)["reason"] == "rotation"


def test_an_ambiguous_first_name_names_nobody():
    """The guard `_voice_notes` applies, for the same reason: an instruction
    pointed at the wrong character is worse than no instruction."""
    npcs = ["Winifred Ash", "Winifred Vale", "Mara Quist"]
    hist = [_npc("Mara Quist"), _player("Winifred, answer me.")]
    assert speaker.nominate(npcs, hist)["reason"] == "rotation"


def test_a_first_name_that_is_another_actors_whole_name_names_nobody():
    npcs = ["Mara Quist", "Mara", "Winifred Ash"]
    hist = [_npc("Winifred Ash"), _player("Mara, wait.")]
    assert speaker.nominate(npcs, hist)["reason"] == "rotation"


def test_only_the_last_player_post_is_read_for_names():
    hist = [_player("Winifred Ash, wait."), _npc("Winifred Ash"),
            _player("What now?")]
    assert speaker.nominate(NPCS, hist)["reason"] == "rotation"


def test_a_name_inside_a_longer_word_is_not_a_mention():
    hist = [_npc("Winifred Ash"), _player("The maraud was loud.")]
    assert speaker.nominate(NPCS, hist)["reason"] == "rotation"


def test_name_matching_ignores_case():
    hist = [_npc("Winifred Ash"), _player("mara, hold the door.")]
    assert speaker.nominate(NPCS, hist)["lead"] == "Mara Quist"


def test_a_scene_with_no_player_post_at_all_falls_back_to_rotation():
    assert speaker.nominate(NPCS, [_npc("Seraphine Vale")])["reason"] == "rotation"


# --------------------------------------------------------------- rotation

def test_never_spoken_leads_over_merely_quiet():
    hist = [_npc("Seraphine Vale"), _npc("Mara Quist"), _npc("Seraphine Vale")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash" and out["spoken"] is False


def test_least_recently_spoken_leads():
    hist = [_npc("Winifred Ash"), _npc("Seraphine Vale"), _npc("Mara Quist")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash"
    assert out["spoken"] is True and out["silent_for"] == 2


def test_equal_silence_breaks_toward_the_fewest_blocks():
    """Both last spoke two blocks back; Mara has said less overall."""
    hist = [_npc("Mara Quist"), _npc("Seraphine Vale"), _npc("Seraphine Vale"),
            _npc("Mara Quist"), _npc("Seraphine Vale"), _npc("Winifred Ash")]
    out = speaker.nominate(["Seraphine Vale", "Mara Quist"], hist)
    assert out["lead"] == "Mara Quist"


def test_a_full_tie_breaks_toward_cast_order():
    """Nobody has spoken, so a given store always nominates the same way."""
    assert speaker.nominate(NPCS, [])["lead"] == "Seraphine Vale"
    assert speaker.nominate(list(reversed(NPCS)), [])["lead"] == "Winifred Ash"


def test_quiet_lists_the_others_and_never_the_lead():
    out = speaker.nominate(NPCS, [_npc("Seraphine Vale")])
    assert out["lead"] not in out["quiet"]
    assert sorted(out["quiet"] + [out["lead"]]) == sorted(NPCS)


def test_quiet_is_listed_in_nomination_order():
    """Next-most-overdue first, so the ordering means something."""
    hist = [_npc("Winifred Ash"), _npc("Mara Quist"), _npc("Seraphine Vale")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Winifred Ash"
    assert out["quiet"] == ["Mara Quist", "Seraphine Vale"]


def test_partial_speaker_labels_canonicalize_to_the_cast_name():
    """The transcript stamps "Winifred" — that is still Winifred Ash speaking,
    and counting it as a stranger would nominate a talkative character as
    never having spoken."""
    hist = [_npc("Winifred"), _npc("Seraphine Vale")]
    assert speaker.nominate(NPCS, hist)["lead"] == "Mara Quist"


def test_synthetic_speakers_are_not_npc_turns():
    hist = [_npc("Seraphine Vale"), _npc("Mara Quist")]
    hist += [{"role": "assistant", "speaker": s, "content": "x"}
             for s in scenes_serialize.SYNTHETIC_SPEAKERS]
    assert speaker.nominate(NPCS, hist)["lead"] == "Winifred Ash"


def test_a_speaker_who_is_not_in_the_cast_is_not_counted():
    """A character who has left the scene still has blocks in the transcript.
    They are not a candidate and must not shift anyone else's silence."""
    hist = [_npc("Winifred Ash"), _npc("Someone Departed"), _npc("Seraphine Vale")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Mara Quist"


def test_player_posts_do_not_count_as_blocks():
    hist = [_npc("Winifred Ash"), _player("go on"), _npc("Seraphine Vale")]
    out = speaker.nominate(NPCS, hist)
    assert out["lead"] == "Mara Quist"


def test_an_unstamped_assistant_block_is_ignored():
    hist = [_npc("Winifred Ash"),
            {"role": "assistant", "speaker": None, "content": "The rain kept on."},
            _npc("Seraphine Vale")]
    assert speaker.nominate(NPCS, hist)["lead"] == "Mara Quist"


def test_the_result_is_stable_across_repeat_calls():
    """Derived, not advanced: a regenerate must reproduce the same nomination
    rather than move a rotation the reader never saw."""
    hist = [_npc("Winifred Ash"), _npc("Seraphine Vale")]
    assert speaker.nominate(NPCS, hist) == speaker.nominate(NPCS, hist)


def test_history_is_not_mutated():
    hist = [_npc("Winifred Ash"), _player("go on")]
    before = [dict(m) for m in hist]
    speaker.nominate(NPCS, hist)
    assert hist == before
