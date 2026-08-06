"""Transient state end to end: the tracker block never reaches a transcript
(#120), the ledger follows a scene's id through rename and delete, the prompt
sections come and go with the setting, and a reinforced value is offered as a
character-state edit at absorb (#121)."""

from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.routes.streaming import _persist_reply
from grimoire.store import (absorb, appearances, campaigns, characters, config, context,
                            playstate, scenes, turnstate, worlds)


def _scene(monkeypatch, tmp_path, name="Winifred Ash"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    card = characters.blank_card(name)
    card["data"]["description"] = "The house steward."
    char_id, _ = characters.create_character(croot, name, "default", card)
    sid = scenes.create_scene(cid, "Saltmarch")
    appearances.appear(cid, sid, "characters", char_id, "default", "npc")
    return cid, sid, char_id


def _block(payload: str) -> str:
    return f"```state\n{payload}\n```"


# ---- the persist path ------------------------------------------------------

def test_the_block_is_stripped_from_the_transcript_and_filed_at_the_last_post(
        monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, "**Winifred Ash:** I have nothing to say.\n\n"
                   + _block('{"Winifred Ash": {"mood": "guarded"}}'))
    messages = scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 1
    assert "state" not in messages[0]["content"] and "```" not in messages[0]["content"]
    assert turnstate.entries(cid, sid) == [(0, {f"characters:{char_id}": {"mood": "guarded"}})]


def test_the_entry_lands_on_the_last_post_of_a_multi_speaker_reply(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    _persist_reply(cid, sid, "**Winifred Ash:** Gone.\n\n**Grimoire:** The lamp gutters.\n\n"
                   + _block('{"Winifred Ash": {"mood": "guarded"}}'))
    # user post + two model blocks -> the tracker describes index 2
    assert len(scenes.read_scene(cid, sid)["messages"]) == 3
    assert turnstate.entries(cid, sid)[0][0] == 2


def test_stripping_happens_even_with_the_feature_off(monkeypatch, tmp_path):
    # The instruction is gone but a model still complying from the visible scene
    # must not start writing blocks into transcripts.
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    assert config.turnstate_depth() == 0
    _persist_reply(cid, sid, "**Winifred Ash:** Quiet.\n\n"
                   + _block('{"Winifred Ash": {"mood": "guarded"}}'))
    assert "```" not in scenes.read_scene(cid, sid)["messages"][0]["content"]


def test_an_unresolvable_name_costs_the_entry_not_the_reply(monkeypatch, tmp_path):
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, "**Winifred Ash:** Quiet.\n\n"
                   + _block('{"Someone Else": {"mood": "guarded"}}'))
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1
    assert turnstate.read(cid) == {}


def test_a_reply_that_is_only_a_block_records_nothing(monkeypatch, tmp_path):
    # append_reply keeps no empty post, so there is no index to file against.
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, _block('{"Winifred Ash": {"mood": "guarded"}}'))
    assert scenes.read_scene(cid, sid)["messages"] == []
    assert turnstate.read(cid) == {}


def test_a_rerolled_reply_does_not_inherit_the_discarded_variants_mood(
        monkeypatch, tmp_path):
    """The failing case supersede exists for: the replacement lands at the same
    index, so the tail filter cannot tell the dead entry from a live one."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, "**Winifred Ash:** Get out.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    assert turnstate.entries(cid, sid)[0][1] == {f"characters:{char_id}": {"mood": "furious"}}
    scenes.remove_trailing_assistant_run(cid, sid)
    _persist_reply(cid, sid, "**Winifred Ash:** ...if you would.")   # no block this time
    assert turnstate.entries(cid, sid) == []


def test_a_shortened_speaker_label_still_files_its_state(monkeypatch, tmp_path):
    """The reply labels her `**Winifred:**`, which the transcript grammar
    accepts for `Winifred Ash` — so the tracker key does too."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, "**Winifred:** Nothing to say.\n\n"
                   + _block('{"Winifred": {"mood": "guarded"}}'))
    assert turnstate.entries(cid, sid) == [(0, {f"characters:{char_id}": {"mood": "guarded"}})]


def test_editing_a_post_retires_the_state_it_recorded(monkeypatch, tmp_path):
    """An edit is the one transcript change the tail filter cannot see: the
    length is unchanged, so the entry keeps claiming a mood for text that no
    longer says it."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    _persist_reply(cid, sid, "**Winifred Ash:** Get OUT.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    assert "furious" in dict((s["label"], s["text"]) for s in
                             context.context_sections(cid, sid))["Transient state"]
    client = TestClient(create_app())
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0",
                   json={"content": "**Winifred Ash:** Please, sit."})
    assert r.status_code == 200
    assert turnstate.entries(cid, sid) == []
    assert "Transient state" not in _labels(cid, sid)


def test_superseding_leaves_earlier_posts_alone(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, "**Winifred Ash:** One.\n\n"
                   + _block('{"Winifred Ash": {"mood": "wary"}}'))
    _persist_reply(cid, sid, "**Winifred Ash:** Two.")
    assert turnstate.entries(cid, sid) == [(0, {f"characters:{char_id}": {"mood": "wary"}})]


# ---- id lifecycle ----------------------------------------------------------

def test_a_renamed_scene_keeps_its_ledger(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    turnstate.record(cid, sid, 0, {f"characters:{char_id}": {"mood": "guarded"}})
    new_sid = scenes.rename_scene(cid, sid, "Saltmarch, later")
    assert new_sid != sid
    assert turnstate.entries(cid, new_sid) == [(0, {f"characters:{char_id}": {"mood": "guarded"}})]


def test_a_deleted_scene_cannot_hand_its_moods_to_the_id_that_replaces_it(
        monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    turnstate.record(cid, sid, 0, {f"characters:{char_id}": {"mood": "guarded"}})
    scenes.delete_scene(cid, sid)
    assert turnstate.read(cid) == {}
    reused = scenes.create_scene(cid, "Saltmarch")
    assert turnstate.entries(cid, reused) == []


# ---- the prompt sections ---------------------------------------------------

def _labels(cid, sid):
    return [s["label"] for s in context.context_sections(cid, sid)]


def test_both_sections_are_absent_while_the_feature_is_off(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    turnstate.record(cid, sid, 0, {f"characters:{char_id}": {"mood": "guarded"}})
    labels = _labels(cid, sid)
    assert "Transient state" not in labels and "Transient state tracker" not in labels


def test_switching_it_on_adds_the_instruction_and_the_live_values(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    scenes.append_message(cid, sid, "user", "hello")
    turnstate.record(cid, sid, 0, {f"characters:{char_id}": {"mood": "guarded"}})
    sections = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "guarded" in sections["Transient state"]
    assert "Winifred Ash" in sections["Transient state"]
    assert "```state" in sections["Transient state tracker"]


def test_a_value_outside_the_window_is_not_injected(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="2")
    turnstate.record(cid, sid, 0, {f"characters:{char_id}": {"mood": "guarded"}})
    for _ in range(5):
        scenes.append_message(cid, sid, "user", "and then?")
    assert "Transient state" not in _labels(cid, sid)


def test_the_tracker_instruction_is_kept_off_the_opener(monkeypatch, tmp_path):
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    system = context.build_opener_messages(cid, sid, "Open on the hall.")[0]["content"]
    assert "Transient state tracker" not in system and "```state" not in system


# ---- promotion -------------------------------------------------------------

def _reinforce(cid, sid, char_id, value, times=3, field="mood"):
    # Promotion is gated on the feature being on, so every promotion test runs
    # with it on — that is the only configuration it can happen in.
    config.write_config(turnstate_depth="4")
    for _ in range(times):
        i = len(scenes.read_scene(cid, sid)["messages"])
        scenes.append_message(cid, sid, "assistant", f"beat {i}", speaker="Winifred Ash")
        turnstate.record(cid, sid, i, {f"characters:{char_id}": {field: value}})


def _state_edits(cid, sid, parsed=None):
    return [e for e in absorb.materialize(cid, sid, parsed or {})
            if e["kind"] == "character_state"]


def test_a_streak_is_offered_as_a_character_state_edit(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _reinforce(cid, sid, char_id, "guarded")
    edits = _state_edits(cid, sid)
    assert len(edits) == 1
    assert edits[0]["id"] == f"character_state:{char_id}"
    assert edits[0]["before"] == "" and edits[0]["after"] == "Mood: guarded"
    assert edits[0]["authored"] is False


def test_a_broken_streak_proposes_nothing(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _reinforce(cid, sid, char_id, "guarded", times=2)
    assert _state_edits(cid, sid) == []


def test_promotion_is_switched_off_by_a_zero_streak(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(promote_streak="0")
    _reinforce(cid, sid, char_id, "guarded")
    assert _state_edits(cid, sid) == []


def test_promotion_preserves_the_stored_prose_and_knowledge(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    playstate.write_state(croot, char_id, playstate.compose_body(
        "She is hiding a key.", "The ledger is real.", ""))
    _reinforce(cid, sid, char_id, "guarded")
    after = _state_edits(cid, sid)[0]["after"]
    parsed = playstate.parse_body(after)
    assert parsed["current_state"] == "She is hiding a key.\nMood: guarded"
    assert parsed["knows"] == "The ledger is real."


def test_a_second_absorb_over_the_same_ledger_proposes_nothing(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    _reinforce(cid, sid, char_id, "guarded")
    playstate.write_state(croot, char_id, _state_edits(cid, sid)[0]["after"])
    assert _state_edits(cid, sid) == []


def test_promotion_merges_into_the_models_own_edit_for_that_character(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _reinforce(cid, sid, char_id, "guarded")
    edits = _state_edits(cid, sid, {"character_state_edits": [
        {"id": f"characters/{char_id}", "current_state": "Cornered in the study."}]})
    assert len(edits) == 1                      # one row, not two
    assert edits[0]["after"] == "Cornered in the study.\nMood: guarded"


def test_a_merge_that_cancels_the_models_edit_out_removes_the_row(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    playstate.write_state(croot, char_id, "Cornered in the study.\nMood: guarded")
    _reinforce(cid, sid, char_id, "guarded")
    # The model proposes dropping the mood line; promotion puts it straight back,
    # which lands on exactly what is stored -- so there is nothing to review.
    assert _state_edits(cid, sid, {"character_state_edits": [
        {"id": f"characters/{char_id}", "current_state": "Cornered in the study."}]}) == []


def test_promotion_ignores_a_character_that_no_longer_exists(monkeypatch, tmp_path):
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    for i in range(3):
        turnstate.record(cid, sid, i, {"characters:ghost": {"mood": "guarded"}})
        scenes.append_message(cid, sid, "assistant", f"beat {i}", speaker="Winifred Ash")
    assert _state_edits(cid, sid) == []


def test_a_character_no_longer_in_the_cast_is_not_injected(monkeypatch, tmp_path):
    """The ledger outlives a departure — the section is built from the cast, so
    a character who has left the scene stops being described by it."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    scenes.append_message(cid, sid, "user", "hello")
    turnstate.record(cid, sid, 0, {f"characters:{char_id}": {"mood": "guarded"}})
    assert "Transient state" in _labels(cid, sid)
    appearances.leave(cid, sid, "characters", char_id)
    assert "Transient state" not in _labels(cid, sid)


def test_a_promotion_for_one_character_leaves_the_models_edit_for_another_alone(
        monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    other, _ = characters.create_character(croot, "Mara Vance", "default",
                                           characters.blank_card("Mara Vance"))
    _reinforce(cid, sid, char_id, "guarded")
    edits = _state_edits(cid, sid, {"character_state_edits": [
        {"id": f"characters/{other}", "current_state": "Waiting in the yard."}]})
    assert {e["id"]: e["after"] for e in edits} == {
        f"character_state:{other}": "Waiting in the yard.",
        f"character_state:{char_id}": "Mood: guarded"}


def test_promotion_is_bounded_by_the_callers_snapshot(monkeypatch, tmp_path):
    """A turn landing while the extraction call is in flight must not
    contribute a promoted value to a review built from a transcript that did
    not contain it."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _reinforce(cid, sid, char_id, "guarded", times=2)          # inside the snapshot
    snapshot = turnstate.entries(cid, sid, len(scenes.read_scene(cid, sid)["messages"]))
    _reinforce(cid, sid, char_id, "guarded", times=1)          # landed mid-absorb
    # Live tail sees three and promotes; the snapshot saw two and does not.
    assert absorb.materialize(cid, sid, {}) != []
    assert absorb.materialize(cid, sid, {}, turn_ledger=snapshot) == []


def test_promotion_stops_when_the_feature_is_switched_off(monkeypatch, tmp_path):
    """`turnstate_depth = 0` is the whole feature's switch, and the
    Configuration page says so. A retained ledger — or a block a model
    volunteered while the feature was off all along — must not keep proposing
    canonical state behind that promise."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _reinforce(cid, sid, char_id, "guarded")
    assert _state_edits(cid, sid) != []
    config.write_config(turnstate_depth="0")
    assert _state_edits(cid, sid) == []


def test_a_streak_longer_than_the_ledger_can_hold_still_promotes(monkeypatch, tmp_path):
    """A threshold above MAX_ENTRIES could never be met, and nothing in the UI
    distinguishes "very strict" from "impossible" — so it saturates at the
    memory the ledger actually has."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(promote_streak=str(turnstate.MAX_ENTRIES + 50))
    _reinforce(cid, sid, char_id, "guarded", times=turnstate.MAX_ENTRIES)
    assert _state_edits(cid, sid) != []


def test_a_reroll_beneath_a_transition_line_still_retires_its_state(monkeypatch, tmp_path):
    """`remove_trailing_assistant_run` preserves trailing transitions and
    re-appends them, so the replacement lands ABOVE where the old generation
    sat. Superseding from the new landing index would step straight over the
    dead entry."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    _persist_reply(cid, sid, "**Winifred Ash:** Get out.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    scenes.append_message(cid, sid, "assistant", "— the hall —",
                          speaker=scenes.TRANSITION_SPEAKER)
    scenes.remove_trailing_assistant_run(cid, sid)          # steps over the transition
    _persist_reply(cid, sid, "**Winifred Ash:** ...if you would.")   # no block
    assert turnstate.entries(cid, sid) == []
    assert "Transient state" not in _labels(cid, sid)


def test_swapping_in_a_parked_alternate_does_not_keep_the_live_takes_state(
        monkeypatch, tmp_path):
    """`alternates.promote` swaps narration through the same remove/append pair
    but knows nothing of this ledger, so the restored take would otherwise be
    described by the mood of the take it replaced."""
    from grimoire.store import alternates
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    _persist_reply(cid, sid, "**Winifred Ash:** Get out.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    # Exactly what the regenerate route does: archive, then remove, and let the
    # replacement join the set when the next read reconciles.
    alternates.archive(cid, sid, "")
    scenes.remove_trailing_assistant_run(cid, sid)
    _persist_reply(cid, sid, "**Winifred Ash:** Please, sit.\n\n"
                   + _block('{"Winifred Ash": {"mood": "gracious"}}'))
    assert len(alternates.state(cid, sid)["runs"]) == 2      # both takes parked
    assert turnstate.entries(cid, sid)[0][1] == {
        f"characters:{char_id}": {"mood": "gracious"}}       # the live take's
    alternates.promote(cid, sid, 0)
    assert turnstate.entries(cid, sid) == []


def test_a_reroll_that_produces_nothing_restores_the_state_with_the_reply(
        monkeypatch, tmp_path):
    """Reroll deletes before it generates, so the removal drops the ledger entry
    too. A generation that then says nothing puts the reply back — and the mood
    has to come back with it, or the restored reply is present in the transcript
    and silently absent from the next prompt."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    _persist_reply(cid, sid, "**Winifred Ash:** Get out.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    live = turnstate.entries(cid, sid)
    token = scenes.remove_trailing_assistant_run(cid, sid)
    assert turnstate.entries(cid, sid) == []          # gone while the reroll runs
    assert scenes.restore_trailing_assistant_run(cid, sid, token) is True
    assert turnstate.entries(cid, sid) == live        # and back with the reply
    assert "furious" in dict((s["label"], s["text"]) for s in
                             context.context_sections(cid, sid))["Transient state"]


def test_a_refused_restore_does_not_refile_the_state(monkeypatch, tmp_path):
    """The restore declines when the transcript moved on. Re-filing the ledger
    anyway would describe whatever took the reply's place."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    _persist_reply(cid, sid, "**Winifred Ash:** Get out.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    token = scenes.remove_trailing_assistant_run(cid, sid)
    _persist_reply(cid, sid, "**Winifred Ash:** Please, sit.")   # something else landed
    assert scenes.restore_trailing_assistant_run(cid, sid, token) is False
    assert turnstate.entries(cid, sid) == []


def test_trimming_a_crashed_continuation_retires_its_state(monkeypatch, tmp_path):
    """`trim_continuation` compacts preserved dice-roll lines DOWN to
    `from_index`, so a roll can land on the index a crashed continuation's
    tracker entry holds — an index the tail filter still accepts."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    config.write_config(turnstate_depth="4")
    scenes.append_message(cid, sid, "user", "Where is the ledger?")
    at = len(scenes.read_scene(cid, sid)["messages"])
    _persist_reply(cid, sid, "**Winifred Ash:** Get out.\n\n"
                   + _block('{"Winifred Ash": {"mood": "furious"}}'))
    scenes.append_message(cid, sid, "assistant", "🎲 Steady Hand — 14",
                          speaker=scenes.ROLL_SPEAKER)
    assert turnstate.entries(cid, sid)[0][0] == at
    scenes.trim_continuation(cid, sid, at)      # the roll compacts onto index `at`
    assert turnstate.entries(cid, sid) == []
    assert "Transient state" not in _labels(cid, sid)


def test_a_macro_in_a_tracker_value_is_resolved_once_at_persist_time(
        monkeypatch, tmp_path):
    """Every rendered section is macro-expanded on each context build, so a
    stored `{{random}}` would re-roll every prompt — and a value that never
    holds still can never streak into a promotion either."""
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    _persist_reply(cid, sid, "**Winifred Ash:** Quiet.\n\n"
                   + _block('{"Winifred Ash": {"mood": "{{random:calm,calm}}"}}'))
    stored = turnstate.entries(cid, sid)[0][1][f"characters:{char_id}"]
    assert stored == {"mood": "calm"}
    assert "{{" not in stored["mood"]


def test_adopting_an_opener_that_is_only_a_tracker_block_is_refused(
        monkeypatch, tmp_path):
    """`first-post` reported success on text that stripped to nothing, so the
    opener the user was adopting vanished with no error to show for it."""
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    client = TestClient(create_app())
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                    json={"text": _block('{"Winifred Ash": {"mood": "guarded"}}')})
    assert r.status_code == 400
    assert scenes.read_scene(cid, sid)["messages"] == []


def test_adopting_an_opener_that_is_only_an_unterminated_opener_is_refused(
        monkeypatch, tmp_path):
    cid, sid, _ = _scene(monkeypatch, tmp_path)
    client = TestClient(create_app())
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                    json={"text": '```state\n{"Winifred Ash": {"mo'})
    assert r.status_code == 400
    assert scenes.read_scene(cid, sid)["messages"] == []


def test_a_real_opener_with_a_trailing_block_is_still_adopted(monkeypatch, tmp_path):
    cid, sid, char_id = _scene(monkeypatch, tmp_path)
    client = TestClient(create_app())
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/first-post",
                    json={"text": "**Winifred Ash:** The hall is cold.\n\n"
                                  + _block('{"Winifred Ash": {"mood": "guarded"}}')})
    assert r.status_code == 200
    messages = scenes.read_scene(cid, sid)["messages"]
    assert len(messages) == 1 and "```" not in messages[0]["content"]
