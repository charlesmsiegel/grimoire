"""The transient per-turn state ledger (#120): its block grammar, the stream
redactor that keeps the block off the player's screen, and the two projections
the prompt and promotion read."""

import json

import pytest

from grimoire.store import campaigns, playstate, scenes, turnstate, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


def _block(payload: str) -> str:
    return f"```state\n{payload}\n```"


# ---- the block grammar -----------------------------------------------------

def test_a_trailing_block_is_split_off_the_narration():
    text = "Winifred sets the lamp down.\n\n" + _block('{"Winifred": {"mood": "guarded"}}')
    narration, states = turnstate.split_block(text)
    assert narration.strip() == "Winifred sets the lamp down."
    assert states == {"Winifred": {"mood": "guarded"}}


def test_a_reply_with_no_block_is_returned_unchanged():
    assert turnstate.split_block("Just prose.") == ("Just prose.", {})


def test_a_block_with_narration_after_it_is_left_in_place():
    # Deleting from the middle of a reply is the one failure that loses story.
    text = _block('{"Winifred": {"mood": "guarded"}}') + "\n\nShe turns back."
    narration, states = turnstate.split_block(text)
    assert narration == text
    assert states == {}


def test_an_unterminated_trailing_opener_is_stripped_with_no_data():
    text = 'She waits.\n\n```state\n{"Winifred": {"mood": "gu'
    narration, states = turnstate.split_block(text)
    assert narration.strip() == "She waits."
    assert states == {}


def test_malformed_json_costs_the_data_not_the_reply():
    narration, states = turnstate.split_block("She waits.\n\n" + _block("{not json"))
    assert narration.strip() == "She waits."
    assert states == {}


def test_the_state_envelope_form_is_accepted_too():
    _, states = turnstate.split_block(_block('{"state": {"Winifred": {"mood": "tense"}}}'))
    assert states == {"Winifred": {"mood": "tense"}}


def test_unknown_fields_and_non_string_values_are_dropped():
    _, states = turnstate.split_block(
        _block('{"Winifred": {"mood": "wry", "hp": 4, "intent": null, "posture": "  leaning  in  "}}'))
    assert states == {"Winifred": {"mood": "wry", "posture": "leaning in"}}


def test_an_over_long_value_is_dropped_rather_than_truncated():
    long = "x" * (turnstate.MAX_VALUE + 1)
    _, states = turnstate.split_block(_block(json.dumps({"W": {"mood": long, "intent": "wait"}})))
    assert states == {"W": {"intent": "wait"}}


def test_a_character_with_nothing_usable_is_dropped_entirely():
    _, states = turnstate.split_block(_block('{"Winifred": {"hp": 3}, "Mara": {"mood": "calm"}}'))
    assert states == {"Mara": {"mood": "calm"}}


def test_the_last_of_several_blocks_is_the_one_that_counts():
    text = (_block('{"Mara": {"mood": "early"}}') + "\n\nShe moves.\n\n"
            + _block('{"Mara": {"mood": "late"}}'))
    narration, states = turnstate.split_block(text)
    assert states == {"Mara": {"mood": "late"}}
    assert "early" in narration and "late" not in narration


# ---- name resolution -------------------------------------------------------

CAST = [{"kind": "characters", "id": "winifred", "role": "npc", "name": "Winifred Ash"},
        {"kind": "pcs", "id": "mara", "role": "player", "name": "Mara"}]


def _resolve(states, cast=CAST):
    # scenes.match_name is what the transcript grammar resolves labels with, and
    # what the route injects.
    return turnstate.resolve(states, cast, scenes.match_name)


def test_names_resolve_case_insensitively_to_actor_tokens():
    assert _resolve({"winifred ash": {"mood": "wry"}}) == {
        "characters:winifred": {"mood": "wry"}}


def test_a_shortened_label_resolves_the_way_the_transcript_does():
    """`**Winifred:**` is a valid transcript label for Winifred Ash, so it is
    the label the tracker instruction asks the model to reuse. Exact-matching
    dropped every block from a model that used one."""
    assert _resolve({"Winifred": {"mood": "wry"}}) == {
        "characters:winifred": {"mood": "wry"}}


def test_an_unknown_name_is_dropped():
    assert _resolve({"Nobody": {"mood": "wry"}}) == {}


def test_players_are_not_tracked():
    assert _resolve({"Mara": {"mood": "wry"}}) == {}


def test_an_ambiguous_shared_name_resolves_to_neither():
    cast = [{"kind": "characters", "id": "a", "role": "npc", "name": "Mara Vance"},
            {"kind": "characters", "id": "b", "role": "npc", "name": "Mara Vance"}]
    assert _resolve({"Mara Vance": {"mood": "wry"}}, cast) == {}


def test_a_prefix_two_characters_share_resolves_to_neither():
    cast = [{"kind": "characters", "id": "a", "role": "npc", "name": "Mara Vance"},
            {"kind": "characters", "id": "b", "role": "npc", "name": "Mara Chen"}]
    assert _resolve({"Mara": {"mood": "wry"}}, cast) == {}
    assert _resolve({"Mara Chen": {"mood": "wry"}}, cast) == {"characters:b": {"mood": "wry"}}


# ---- the stream redactor ---------------------------------------------------

def _stream(chunks):
    r = turnstate.StreamRedactor()
    return "".join(r.feed(c) for c in chunks) + r.finish()


def test_the_redactor_passes_ordinary_prose_through():
    assert _stream(["She ", "sets the ", "lamp down."]) == "She sets the lamp down."


def test_the_redactor_swallows_the_block_even_split_across_deltas():
    assert _stream(["She waits.\n\n", "``", "`sta", "te\n{\"W\": ", "{}}\n```"]) == "She waits.\n\n"


def test_backticks_that_are_not_an_opener_are_released():
    assert _stream(["a ``", "code`` span"]) == "a ``code`` span"
    assert _stream(["```py", "thon\nx = 1\n```"]) == "```python\nx = 1\n```"


def test_a_trailing_backtick_run_survives_end_of_stream():
    assert _stream(["done ``", "`"]) == "done ```"


def test_statement_is_not_an_opener():
    assert _stream(["```statement of intent"]) == "```statement of intent"


def test_nothing_escapes_after_a_trailing_block():
    # The fence starts a line — an inline one is not a block at all, and
    # `test_an_inline_fence_is_never_withheld` covers that case.
    r = turnstate.StreamRedactor()
    assert r.feed("hi\n```state\n") == "hi\n"
    assert r.feed('{"W": {}}') == ""
    assert r.feed("\n```") == ""
    assert r.finish() == ""


def test_a_block_with_narration_after_it_is_released_whole():
    """The transcript keeps a mid-reply block (split_block only strips a
    trailing one), so a redactor that dropped it would end the streamed reply
    early and disagree with what was stored."""
    text = 'She waits.\n\n```state\n{"W": {"mood": "wry"}}\n```\n\nShe turns back.'
    assert _stream([text]) == text
    assert turnstate.split_block(text)[0] == text


def test_an_unterminated_block_is_still_swallowed():
    assert _stream(['She waits.\n\n```state\n{"W": {"mo']) == "She waits.\n\n"


# ---- the ledger ------------------------------------------------------------

def test_record_and_read_back(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 4, {"characters:w": {"mood": "guarded"}})
    assert turnstate.entries(cid, "s1") == [(4, {"characters:w": {"mood": "guarded"}})]


def test_recording_nothing_writes_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 4, {})
    assert turnstate.read(cid) == {}


def test_entries_past_the_transcript_tail_are_dropped(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 9, {"characters:w": {"mood": "stale"}})
    assert turnstate.entries(cid, "s1", tail=5) == []


def test_a_relanded_reply_overwrites_the_entry_at_its_index(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 4, {"characters:w": {"mood": "first"}})
    turnstate.record(cid, "s1", 4, {"characters:w": {"mood": "second"}})
    assert turnstate.entries(cid, "s1") == [(4, {"characters:w": {"mood": "second"}})]


def test_a_garbled_file_reads_as_empty_rather_than_raising(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 1, {"characters:w": {"mood": "x"}})
    (campaigns.campaign_root(cid) / "turnstate.json").write_text("{ nope", encoding="utf-8")
    assert turnstate.read(cid) == {}
    assert turnstate.entries(cid, "s1") == []
    assert turnstate.current(cid, "s1", 10, 4) == {}


@pytest.mark.parametrize("payload", [
    '{"s1": "not a dict"}',
    '{"s1": {"two": {"characters:w": {"mood": "x"}}}}',      # non-numeric index
    '{"s1": {"1": ["not", "a", "dict"]}}',
    '{"s1": {"1": {"characters:w": "not a dict"}}}',
    '{"s1": {"-2": {"characters:w": {"mood": "x"}}}}',
])
def test_malformed_levels_are_skipped_not_fatal(monkeypatch, tmp_path, payload):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "turnstate.json").write_text(payload, encoding="utf-8")
    assert turnstate.entries(cid, "s1") == []


# ---- decay -----------------------------------------------------------------

def test_only_the_window_is_live_and_the_newest_value_wins(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 1, {"characters:w": {"mood": "old", "intent": "wait"}})
    turnstate.record(cid, "s1", 8, {"characters:w": {"mood": "new"}})
    live = turnstate.current(cid, "s1", tail=10, depth=4)
    assert live == {"characters:w": {"mood": "new"}}   # "wait" decayed out with post 1


def test_a_field_the_newest_entry_omits_survives_inside_the_window(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 7, {"characters:w": {"intent": "reach the door"}})
    turnstate.record(cid, "s1", 8, {"characters:w": {"mood": "new"}})
    assert turnstate.current(cid, "s1", tail=10, depth=4) == {
        "characters:w": {"intent": "reach the door", "mood": "new"}}


def test_depth_zero_disables_the_projection(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 8, {"characters:w": {"mood": "new"}})
    assert turnstate.current(cid, "s1", tail=10, depth=0) == {}


# ---- streaks ---------------------------------------------------------------

def _run(cid, values, sid="s1"):
    for i, v in enumerate(values):
        if v is not None:
            turnstate.record(cid, sid, i, {"characters:w": {"mood": v}})


def test_a_run_of_three_promotes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _run(cid, ["guarded", "guarded", "guarded"])
    assert turnstate.streaks(cid, "s1", tail=9, need=3) == {"characters:w": {"mood": "guarded"}}


def test_a_shorter_run_does_not(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _run(cid, ["guarded", "guarded"])
    assert turnstate.streaks(cid, "s1", tail=9, need=3) == {}


def test_the_run_measured_is_the_final_one(monkeypatch, tmp_path):
    # Four posts guarded, then it changes: the character is not guarded now, and
    # promoting the scene's abandoned middle into standing state would be wrong.
    cid = _campaign(monkeypatch, tmp_path)
    _run(cid, ["guarded", "guarded", "guarded", "guarded", "open"])
    assert turnstate.streaks(cid, "s1", tail=9, need=3) == {}


def test_casing_and_trailing_punctuation_do_not_break_a_run(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _run(cid, ["Guarded.", "guarded", "GUARDED"])
    # The value KEPT is the last one as the model spelled it.
    assert turnstate.streaks(cid, "s1", tail=9, need=3) == {"characters:w": {"mood": "GUARDED"}}


def test_a_post_that_does_not_mention_the_actor_is_skipped_not_breaking(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 0, {"characters:w": {"mood": "guarded"}})
    turnstate.record(cid, "s1", 1, {"characters:other": {"mood": "bored"}})
    turnstate.record(cid, "s1", 2, {"characters:w": {"mood": "guarded"}})
    turnstate.record(cid, "s1", 3, {"characters:w": {"mood": "guarded"}})
    assert turnstate.streaks(cid, "s1", tail=9, need=3)["characters:w"] == {"mood": "guarded"}


def test_streaks_ignore_entries_past_the_tail(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _run(cid, ["guarded", "guarded", "guarded"])
    assert turnstate.streaks(cid, "s1", tail=2, need=3) == {}


# ---- id lifecycle ----------------------------------------------------------

def test_supersede_clears_the_slots_a_landing_reply_takes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 2, {"characters:w": {"mood": "kept"}})
    turnstate.record(cid, "s1", 3, {"characters:w": {"mood": "discarded"}})
    turnstate.supersede(cid, "s1", 3)
    assert turnstate.entries(cid, "s1") == [(2, {"characters:w": {"mood": "kept"}})]


def test_supersede_with_nothing_to_clear_does_not_rewrite(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 2, {"characters:w": {"mood": "kept"}})
    path = campaigns.campaign_root(cid) / "turnstate.json"
    before = path.read_text(encoding="utf-8")
    turnstate.supersede(cid, "s1", 9)
    turnstate.supersede(cid, "other", 0)
    assert path.read_text(encoding="utf-8") == before


def test_repoint_follows_a_renamed_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "old", 1, {"characters:w": {"mood": "x"}})
    turnstate.repoint_scenes(cid, {"old": "new"})
    assert turnstate.entries(cid, "old") == []
    assert turnstate.entries(cid, "new") == [(1, {"characters:w": {"mood": "x"}})]


def test_repoint_with_no_match_leaves_the_file_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 1, {"characters:w": {"mood": "x"}})
    before = (campaigns.campaign_root(cid) / "turnstate.json").read_text(encoding="utf-8")
    turnstate.repoint_scenes(cid, {"other": "another"})
    assert (campaigns.campaign_root(cid) / "turnstate.json").read_text(encoding="utf-8") == before


def test_drop_scene_forgets_one_scene_only(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 1, {"characters:w": {"mood": "x"}})
    turnstate.record(cid, "s2", 1, {"characters:w": {"mood": "y"}})
    turnstate.drop_scene(cid, "s1")
    assert turnstate.entries(cid, "s1") == []
    assert turnstate.entries(cid, "s2") == [(1, {"characters:w": {"mood": "y"}})]


# ---- the playstate fold ----------------------------------------------------

def test_fold_appends_a_new_label_and_capitalizes_it():
    assert playstate.fold_fields("She is tired.", {"mood": "guarded"}) == (
        "She is tired.\nMood: guarded")


def test_fold_replaces_an_existing_label_in_place():
    body = "Mood: open\nShe is tired."
    assert playstate.fold_fields(body, {"mood": "guarded"}) == "Mood: guarded\nShe is tired."


def test_fold_is_idempotent():
    once = playstate.fold_fields("She is tired.", {"mood": "guarded"})
    assert playstate.fold_fields(once, {"mood": "guarded"}) == once


def test_fold_onto_an_empty_body():
    assert playstate.fold_fields("", {"mood": "guarded", "intent": "leave"}) == (
        "Mood: guarded\nIntent: leave")


def test_a_scenes_ledger_is_capped_at_the_newest_entries(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    for i in range(turnstate.MAX_ENTRIES + 10):
        turnstate.record(cid, "s1", i, {"characters:w": {"mood": f"m{i}"}})
    kept = turnstate.entries(cid, "s1")
    assert len(kept) == turnstate.MAX_ENTRIES
    assert kept[0][0] == 10                       # the oldest ten are gone
    assert kept[-1][1] == {"characters:w": {"mood": f"m{turnstate.MAX_ENTRIES + 9}"}}


def test_the_cap_leaves_other_scenes_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s2", 0, {"characters:w": {"mood": "kept"}})
    for i in range(turnstate.MAX_ENTRIES + 5):
        turnstate.record(cid, "s1", i, {"characters:w": {"mood": "x"}})
    assert turnstate.entries(cid, "s2") == [(0, {"characters:w": {"mood": "kept"}})]


# ---- CRLF and end-of-stream edges ------------------------------------------

def test_a_crlf_block_is_recognized():
    """A provider returning CRLF otherwise matched neither boundary — and the
    failure was total and silent: the block persisted into the transcript as
    narration and its state was never recorded."""
    text = 'She waits.\r\n\r\n```state\r\n{"W": {"mood": "wry"}}\r\n```\r\n'
    narration, states = turnstate.split_block(text)
    assert narration.strip() == "She waits."
    assert states == {"W": {"mood": "wry"}}


def test_a_crlf_block_is_swallowed_by_the_redactor_too():
    text = 'She waits.\r\n\r\n```state\r\n{"W": {"mood": "wry"}}\r\n```\r\n'
    assert _stream([text]).strip() == "She waits."


def test_a_complete_opener_held_at_end_of_stream_is_not_leaked():
    """`split_block` strips an EOF-terminated opener as an unterminated block,
    so emitting it would show the player text the transcript does not have —
    and on a reroll, show it instead of the reply the server just restored."""
    assert _stream(["She waits.\n\n```state"]).strip() == "She waits."
    assert turnstate.split_block("She waits.\n\n```state")[0].strip() == "She waits."


def test_a_partial_opener_held_at_end_of_stream_still_comes_back():
    for held in ("`", "``", "```", "```s", "```stat"):
        assert _stream([f"done {held}"]) == f"done {held}"


def test_a_character_actually_called_state_is_not_read_as_the_envelope():
    """`{"state": {"mood": "calm"}}` is the instructed bare form for a character
    whose display name is `state`. Unwrapping on the key alone turned it into a
    cast of one named `mood` and lost the block outright."""
    _, states = turnstate.split_block(_block('{"state": {"mood": "calm"}}'))
    assert states == {"state": {"mood": "calm"}}


def test_the_envelope_still_unwraps_when_its_values_are_field_maps():
    _, states = turnstate.split_block(
        _block('{"state": {"Winifred": {"mood": "calm"}, "Mara": {"mood": "wry"}}}'))
    assert states == {"Winifred": {"mood": "calm"}, "Mara": {"mood": "wry"}}


def test_an_inline_fence_is_never_withheld():
    """`_OPEN` needs a line boundary, so an inline fence is not a block —
    persistence keeps it. Withholding it detached it from the context that
    disqualified it, and `finish` then stripped it as if it were trailing."""
    text = "Use ```state\n{\"W\": {\"mood\": \"wry\"}}\n```"
    assert turnstate.split_block(text)[0] == text      # persistence keeps it
    assert _stream([text]) == text                     # so the stream must too


def test_an_indented_fence_at_a_line_start_is_still_a_block():
    text = 'She waits.\n\n  ```state\n{"W": {"mood": "wry"}}\n  ```'
    assert turnstate.split_block(text)[1] == {"W": {"mood": "wry"}}
    assert _stream([text]).strip() == "She waits."


def test_a_fence_after_a_newline_split_across_deltas_is_still_caught():
    assert _stream(["She waits.", "\n\n", "```state\n{}\n```"]).strip() == "She waits."


def test_expand_values_resolves_once_and_recleans():
    states = {"W": {"mood": "{{x}}", "intent": "{{gone}}"}}
    out = turnstate.expand_values(
        states, lambda v: {"{{x}}": "calm", "{{gone}}": ""}.get(v, v))
    assert out == {"W": {"mood": "calm"}}          # the empty one drops out


def test_expand_values_drops_a_character_left_with_nothing():
    assert turnstate.expand_values({"W": {"mood": "{{gone}}"}}, lambda v: "") == {}


def test_a_wide_window_still_shows_every_entry_the_cap_kept(monkeypatch, tmp_path):
    """`depth` counts posts and `MAX_ENTRIES` counts entries, and entries are
    sparse. Clamping the window to the cap would drop an actor tracked at post
    0 from a 1001-post window she is inside AND still retained — a real loss,
    to guard a dense case the cap has already decided."""
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 0, {"characters:early": {"mood": "wary"}})
    turnstate.record(cid, "s1", 1000, {"characters:w": {"mood": "calm"}})
    live = turnstate.current(cid, "s1", tail=1001, depth=1001)
    assert set(live) == {"characters:early", "characters:w"}


def test_a_narrow_window_still_decays(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 0, {"characters:early": {"mood": "wary"}})
    turnstate.record(cid, "s1", 1000, {"characters:w": {"mood": "calm"}})
    assert set(turnstate.current(cid, "s1", tail=1001, depth=4)) == {"characters:w"}


def test_drop_scene_refuses_rather_than_pretending_on_an_unreadable_ledger(
        monkeypatch, tmp_path):
    """`read` turns an unreadable file into {} — right everywhere else, and
    exactly wrong here: `delete_scene` would take that as "nothing to purge"
    and free the id for a scene that then inherits these moods."""
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 0, {"characters:w": {"mood": "guarded"}})
    path = campaigns.campaign_root(cid) / "turnstate.json"

    def _boom(*a, **k):
        raise OSError("sharing violation")

    monkeypatch.setattr(type(path), "read_text", _boom)
    with pytest.raises(OSError):
        turnstate.drop_scene(cid, "s1")


def test_drop_scene_tolerates_an_unparseable_ledger(monkeypatch, tmp_path):
    """Nothing in a corrupt file can be inherited either, so this one is not
    worth refusing a delete over."""
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "s1", 0, {"characters:w": {"mood": "guarded"}})
    (campaigns.campaign_root(cid) / "turnstate.json").write_text("{ nope", encoding="utf-8")
    turnstate.drop_scene(cid, "s1")           # does not raise


def test_repoint_refuses_rather_than_pretending_on_an_unreadable_ledger(
        monkeypatch, tmp_path):
    """`rename_scene` moves the transcript before the fan-out runs, so a silent
    no-op here leaves the state under the old id — lost to the renamed scene
    and waiting for whatever later reuses that id."""
    cid = _campaign(monkeypatch, tmp_path)
    turnstate.record(cid, "old", 0, {"characters:w": {"mood": "guarded"}})
    path = campaigns.campaign_root(cid) / "turnstate.json"

    def _boom(*a, **k):
        raise OSError("sharing violation")

    monkeypatch.setattr(type(path), "read_text", _boom)
    with pytest.raises(OSError):
        turnstate.repoint_scenes(cid, {"old": "new"})
