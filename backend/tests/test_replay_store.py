"""Retcon replay: re-run each later turn against the edited post (#79) —
`store/replay.py`.

The invariant this file exists for is that the backlog is the ONLY copy of the
posts the replay's cut removed. So the tests that matter most are the ones
where the walk is abandoned: cancelling has to put back a transcript that is
byte-for-byte what was there, turn boundaries included, and every refusal has
to happen BEFORE the cut rather than after it.
"""

import pytest

from grimoire.store import campaigns, chronicle, replay, scene_refs, scenes, worlds
from grimoire.store.scenes import serialize as scenes_serialize
from grimoire.store.scenes import turns as scenes_turns


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


@pytest.fixture
def sid(cid):
    """Four turns: the player speaks, the model answers, twice over."""
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", "player one")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "reply one"}])
    scenes.append_message(cid, sid, "user", "player two")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "reply two"}])
    return sid


def _contents(cid, sid):
    return [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]


# --- starting one ----------------------------------------------------------


def test_beginning_cuts_the_scene_and_keeps_the_rest(cid, sid):
    session = replay.begin(cid, sid, 1)
    assert _contents(cid, sid) == ["player one"]
    assert session["cut"] == 1 and session["done"] == 0
    # The reply carries the walk's position, not the backlog: `steps` is a
    # count there, and the segmentation is read off the record itself.
    assert session["steps"] == 3 and session["turns_left"] == 2
    assert [s["kind"] for s in replay.read(cid)["steps"]] == [
        "generation", "verbatim", "generation"]


def test_the_cut_is_the_cascade_so_the_scene_is_un_absorbed(cid, sid):
    """A replay redoes the scene from a post onwards, so everything the absorb
    wrote about the posts it removes has to come back out — which is
    `cascade.delete_from`'s whole job, not a plain truncation's."""
    chronicle.absorb(cid, {"id": sid, "one_line": "They swore.", "summary": "",
                           "keywords": [], "cast": [], "location": "", "date": ""})
    scenes.mark_absorbed(cid, sid, "They swore.", "A long night.")
    session = replay.begin(cid, sid, 1)
    assert session["cascade"]["was_absorbed"] is True
    assert chronicle.read_chronicle(cid) == {}
    assert "done" not in scenes.read_scene(cid, sid)["meta"]


def test_two_replays_at_once_in_one_campaign_are_refused(cid, sid):
    replay.begin(cid, sid, 1)
    other = scenes.create_scene(cid, "The Long Quay")
    scenes.append_message(cid, other, "user", "hello")
    scenes.append_reply(cid, other, [{"speaker": None, "content": "answer"}])
    with pytest.raises(replay.ReplayError) as refused:
        replay.begin(cid, other, 1)
    # Named: one replay runs per campaign, so the reviewer has to be able to go
    # to the one that is already open.
    assert "Saltmarch" in str(refused.value)
    assert _contents(cid, other) == ["hello", "answer"]


def test_a_span_with_no_model_turn_in_it_is_refused(cid, sid):
    scenes.append_message(cid, sid, "user", "player three")
    with pytest.raises(replay.ReplayError):
        replay.begin(cid, sid, 4)
    assert len(_contents(cid, sid)) == 5, "the refusal must land before the cut"


def test_a_span_that_moves_the_scene_is_refused_before_anything_is_cut(cid, sid):
    """`delete_from` rewinds `location_history` with the transcript, and nothing
    can re-derive the entry from the line's prose — so the honest answer is to
    refuse rather than leave the scene prompted somewhere it never goes."""
    scenes.append_message(cid, sid, "assistant",
                          scenes_serialize.LOCATION_MOVE.format(name="the wharf"),
                          speaker=scenes_serialize.TRANSITION_SPEAKER)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "reply three"}])
    with pytest.raises(replay.ReplayError):
        replay.begin(cid, sid, 1)
    assert len(_contents(cid, sid)) == 6
    assert replay.state(cid) is None


def test_replaying_from_the_first_post_is_refused(cid, sid):
    """It would leave an empty transcript with nothing for the model to answer."""
    for index in (0, -1, 4, 99):
        with pytest.raises(IndexError):
            replay.begin(cid, sid, index)


def test_unknown_scene_raises(cid):
    with pytest.raises(scenes.SceneNotFound):
        replay.begin(cid, "no-such-scene", 1)


# --- pricing one (#80) -----------------------------------------------------


def test_the_preview_counts_the_model_turns_it_would_redo(cid, sid):
    assert replay.preview(cid, sid, 1)["turns"] == 2
    assert replay.preview(cid, sid, 3)["turns"] == 1
    assert replay.preview(cid, sid, 1)["posts"] == 3


def test_the_preview_nudges_a_fork_past_the_threshold(cid, sid, monkeypatch):
    from grimoire.store import config
    assert replay.preview(cid, sid, 1)["fork"] is False
    monkeypatch.setattr(config, "replay_fork_threshold", lambda: 1)
    assert replay.preview(cid, sid, 1)["fork"] is True


def test_the_preview_says_why_a_span_cannot_be_replayed(cid, sid):
    scenes.append_message(cid, sid, "assistant",
                          scenes_serialize.TIME_ADVANCE.format(friendly="dusk"),
                          speaker=scenes_serialize.TRANSITION_SPEAKER)
    assert replay.preview(cid, sid, 1)["blocked"]
    assert replay.preview(cid, sid, 1)["blocked"] == replay.BLOCKED_TRANSITION


def test_the_preview_changes_nothing(cid, sid):
    replay.preview(cid, sid, 1)
    assert len(_contents(cid, sid)) == 4 and replay.state(cid) is None


# --- walking one -----------------------------------------------------------


def test_the_next_step_is_the_generation_the_cut_landed_on(cid, sid):
    replay.begin(cid, sid, 1)
    assert replay.state(cid)["next"] == "generation"
    assert replay.state(cid)["turns_left"] == 2


def test_staging_posts_the_players_own_words_back_verbatim(cid, sid):
    """No model rewrites what the player said."""
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "a fresh reply one"}])
    replay.accept(cid)
    replay.stage(cid)
    assert _contents(cid, sid) == ["player one", "a fresh reply one", "player two"]


def test_staging_twice_appends_once(cid, sid):
    """A turn whose stream died is retried by calling the same route again."""
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh"}])
    replay.accept(cid)
    replay.stage(cid)
    replay.stage(cid)
    assert _contents(cid, sid).count("player two") == 1


def test_accepting_with_nothing_generated_is_refused(cid, sid):
    """Accepting an empty step would drop the original turn and put nothing in
    its place — a deletion wearing the word "accept"."""
    replay.begin(cid, sid, 1)
    with pytest.raises(replay.ReplayError):
        replay.accept(cid)
    assert replay.state(cid)["done"] == 0


def test_the_walk_ends_when_the_last_turn_is_accepted(cid, sid):
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh one"}])
    replay.accept(cid)
    replay.stage(cid)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh two"}])
    assert replay.accept(cid) is None
    assert replay.state(cid) is None
    assert _contents(cid, sid) == ["player one", "fresh one", "player two", "fresh two"]


def test_a_trailing_player_post_is_written_back_rather_than_left_pending(cid, sid):
    """The scene ended on the player's own post, which no model turn answers.
    There is nothing to review there, so the walk finishes rather than parking
    on a step whose button would do nothing."""
    scenes.append_message(cid, sid, "user", "player three")
    replay.begin(cid, sid, 3)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh two"}])
    assert replay.accept(cid) is None
    assert _contents(cid, sid) == ["player one", "reply one", "player two",
                                   "fresh two", "player three"]


# --- abandoning one --------------------------------------------------------


def test_cancelling_puts_the_rest_of_the_scene_back(cid, sid):
    before = _contents(cid, sid)
    replay.begin(cid, sid, 1)
    report = replay.cancel(cid)
    assert report["restored"] == 3
    assert _contents(cid, sid) == before
    assert replay.state(cid) is None


def test_cancelling_restores_the_turn_boundaries_too(cid, sid):
    """Appending a generation's blocks one at a time would leave reroll counting
    back through a boundary that no longer describes a generation."""
    before = scenes_turns.get_turn_sizes(cid, sid)
    replay.begin(cid, sid, 1)
    replay.cancel(cid)
    assert scenes_turns.get_turn_sizes(cid, sid) == before


def test_cancelling_drops_a_reply_the_reviewer_never_accepted(cid, sid):
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "an unwanted take"}])
    replay.cancel(cid)
    assert "an unwanted take" not in _contents(cid, sid)
    assert _contents(cid, sid) == ["player one", "reply one", "player two", "reply two"]


def test_cancelling_keeps_what_was_already_accepted(cid, sid):
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh one"}])
    replay.accept(cid)
    replay.cancel(cid)
    assert _contents(cid, sid) == ["player one", "fresh one", "player two", "reply two"]


def test_cancelling_without_restore_drops_the_rest(cid, sid):
    replay.begin(cid, sid, 1)
    report = replay.cancel(cid, restore=False)
    assert report["dropped"] == 3 and report["restored"] == 0
    assert _contents(cid, sid) == ["player one"]


def test_cancelling_with_no_session_is_refused(cid, sid):
    with pytest.raises(replay.ReplayError):
        replay.cancel(cid)


# --- the session's own bookkeeping -----------------------------------------


def test_a_renamed_scene_takes_its_replay_with_it(cid, sid):
    """The backlog is the only copy of that scene's removed posts, so a rename
    that left the session behind would strand them."""
    replay.begin(cid, sid, 1)
    new_sid = scenes.rename_scene(cid, sid, "The Long Quay")
    assert new_sid != sid
    assert replay.state(cid)["scene"] == new_sid
    replay.cancel(cid)
    assert _contents(cid, new_sid) == ["player one", "reply one", "player two", "reply two"]


def test_the_fan_out_reaches_this_store(cid, sid):
    replay.begin(cid, sid, 1)
    scene_refs.repoint(cid, {sid: "renamed"})
    assert replay.read(cid)["scene"] == "renamed"


def test_a_session_whose_scene_is_gone_says_so_rather_than_vanishing(cid, sid):
    """Reported, not silently cleared: dropping the backlog on a READ would
    destroy the only copy of those posts without anyone asking."""
    replay.begin(cid, sid, 1)
    scenes.delete_scene(cid, sid)
    assert replay.state(cid)["gone"] is True
    # Discarding it is the player's decision, and it goes through the same call
    # the panel's Stop does -- there is nowhere to restore those posts TO.
    replay.cancel(cid, restore=False)
    assert replay.state(cid) is None


def test_a_garbled_session_file_reads_as_no_session(cid, sid):
    (campaigns.campaign_root(cid) / "replay.json").write_text("{not json", encoding="utf-8")
    assert replay.state(cid) is None


def test_consecutive_generations_stay_separate_steps(cid, sid):
    """Two generations with no player post between them (an empty send, a
    director turn) read as one long run to anyone parsing the transcript. The
    recorded turn boundaries are what keeps them apart."""
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "and again"}])
    replay.begin(cid, sid, 3)
    assert [s["kind"] for s in replay.read(cid)["steps"]] == ["generation", "generation"]
    replay.cancel(cid)
    assert scenes_turns.get_turn_sizes(cid, sid) == [1, 1, 1]


def test_staged_player_posts_are_not_mistaken_for_a_replayed_reply(cid, sid):
    """`stage` puts the player's own posts back, which lengthens the transcript
    on its own. Accepting on the strength of that alone would step past an
    original model turn with nothing in its place."""
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh one"}])
    replay.accept(cid)
    replay.stage(cid)                       # re-posts "player two", nothing more
    with pytest.raises(replay.ReplayError):
        replay.accept(cid)
    # ... and the original second reply is still on file, unaccepted.
    assert replay.state(cid)["turns_left"] == 1
    replay.cancel(cid)
    assert _contents(cid, sid) == ["player one", "fresh one", "player two", "reply two"]


def test_a_turn_cannot_be_run_twice_over_one_unanswered_reply(cid, sid):
    """The refusal that makes a lost client state harmless. A reload forgets
    that a turn was run; without this the next click generates a SECOND reply
    beside the first, and one accept steps past both."""
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "fresh one"}])
    assert replay.state(cid)["pending"] is True
    with pytest.raises(replay.ReplayError):
        replay.stage(cid)
    replay.accept(cid)
    assert replay.state(cid)["pending"] is False


def test_cancelling_cleans_up_after_its_own_cut(cid, sid):
    """A raw truncation is not the whole of a truncation anywhere else in this
    store. The ledger entry describes a post the restore has just replaced."""
    from grimoire.store import turnstate
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "a take nobody kept"}])
    turnstate.record(cid, sid, 1, {"seraphine": {"mood": "furious"}})
    replay.cancel(cid)
    assert [i for i, _ in turnstate.entries(cid, sid)] == []
    assert _contents(cid, sid) == ["player one", "reply one", "player two", "reply two"]


def test_restoring_fences_a_review_opened_mid_walk(cid, sid):
    """The cut un-absorbs the scene, so a review CAN be opened while the walk is
    running — and its token would still be valid over a transcript the restore
    is about to replace."""
    from grimoire.store import commits
    replay.begin(cid, sid, 1)
    before = commits.scene_epoch(cid, sid)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "a take nobody kept"}])
    replay.cancel(cid)
    assert commits.scene_epoch(cid, sid) > before


def test_the_discarded_replays_variants_go_with_it(cid, sid):
    """A parked set belongs to the trailing generation and the next post retires
    it, so the only set standing when a cancel truncates is one parked on the
    reply the cancel is discarding. Left behind, it would offer that discarded
    take as an alternate of the original reply the restore puts back."""
    from grimoire.store import alternates
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "the first take"}])
    alternates.archive(cid, sid, "")                     # ... and reroll it
    scenes.remove_trailing_assistant_run(cid, sid)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "a second take"}])
    assert len(alternates.state(cid, sid)["runs"]) == 2

    replay.cancel(cid)
    assert alternates.state(cid, sid)["runs"] == []
    assert _contents(cid, sid) == ["player one", "reply one", "player two", "reply two"]


def test_a_cancel_survives_a_transcript_somebody_else_shortened(cid, sid):
    """`mark` is this store's memory of a transcript the gutter's cut can shorten
    without ever hearing about the replay. Past the end it would raise out of the
    one call whose whole job is to be the way back."""
    from grimoire.store import cascade
    replay.begin(cid, sid, 1)
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "a fresh reply"}])
    replay.accept(cid)                        # mark now sits past the cut
    cascade.delete_from(cid, sid, 1)          # ... and somebody cuts underneath it
    report = replay.cancel(cid)
    assert report["restored"] == 2            # the unreplayed originals, appended
    assert _contents(cid, sid) == ["player one", "player two", "reply two"]
