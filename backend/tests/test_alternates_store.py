"""The non-destructive-reroll sidecar: `<campaign>/scenes/<sid>.alts.json`."""

import json
import pathlib

import pytest

from grimoire.store import (alternates, appearances, atomic, campaigns, pcs, scene_ids, scenes,
                            scene_refs, worlds)
from grimoire.store.scenes import paths as scenes_paths


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


def _seg(text, speaker=None):
    return {"speaker": speaker, "content": text}


def _scene_with_reply(cid, title="Saltmarch"):
    """A scene whose last generation is one model block, tracked."""
    sid = scenes.create_scene(cid, title)
    scenes.append_message(cid, sid, "user", "Describe the harbour.")
    scenes.append_reply(cid, sid, [_seg("Fog over the pilings.")])
    return sid


def _reroll(cid, sid, segments, guidance=""):
    """What the regenerate route does: archive, drop the run, stream a new one —
    and persist the reconciliation the reply's landing produced, which the
    route does from `_persist_reply`."""
    alternates.archive(cid, sid, guidance)
    scenes.remove_trailing_assistant_run(cid, sid)
    scenes.append_reply(cid, sid, segments)
    alternates.reconcile(cid, sid)


def _texts(cid, sid):
    return [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]


def test_a_scene_that_was_never_rerolled_has_no_alternates(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    assert alternates.state(cid, sid) == {"active": None, "runs": []}
    assert not scenes_paths._alts_path(cid, sid).exists()


def test_regenerate_archives_the_outgoing_run_instead_of_destroying_it(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)

    _reroll(cid, sid, [_seg("Gulls over the pilings.")], guidance="colder")

    state = alternates.state(cid, sid)
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog over the pilings.")],
        [_seg("Gulls over the pilings.")],
    ]
    assert state["active"] == 1                        # the new run is live
    assert state["runs"][1]["guidance"] == "colder"    # the hint that produced it
    assert _texts(cid, sid) == ["Describe the harbour.", "Gulls over the pilings."]


def test_cycling_back_swaps_the_live_run_in_place(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])

    alternates.promote(cid, sid, 0)

    assert _texts(cid, sid) == ["Describe the harbour.", "Fog over the pilings."]
    state = alternates.state(cid, sid)
    assert state["active"] == 0
    # nothing was lost -- the run that was live is still an alternate
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog over the pilings.")],
        [_seg("Gulls over the pilings.")],
    ]
    assert scenes.get_turn_sizes(cid, sid) == [1]


def test_cycling_keeps_turn_sizes_in_step_with_a_differently_sized_run(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Mara waits.", "Mara"), _seg("Winifred does not.", "Winifred")])
    assert scenes.get_turn_sizes(cid, sid) == [2]

    alternates.promote(cid, sid, 0)                    # back to the one-block run

    assert scenes.get_turn_sizes(cid, sid) == [1]
    assert _texts(cid, sid) == ["Describe the harbour.", "Fog over the pilings."]

    alternates.promote(cid, sid, 1)                    # and forward again

    assert scenes.get_turn_sizes(cid, sid) == [2]
    assert _texts(cid, sid) == ["Describe the harbour.", "Mara waits.", "Winifred does not."]


def test_promoting_an_unknown_alternate_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    for index in (-1, 2):
        with pytest.raises(alternates.AlternateNotFound):
            alternates.promote(cid, sid, index)


def test_an_identical_reroll_does_not_become_a_second_alternate(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Fog over the pilings.")])   # the model repeated itself
    state = alternates.state(cid, sid)
    assert len(state["runs"]) == 1 and state["active"] == 0


def test_retention_caps_the_set_and_ages_out_the_oldest(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    takes = [f"Take {n}." for n in range(alternates.MAX_ALTERNATES + 3)]
    for text in takes:
        _reroll(cid, sid, [_seg(text)])

    runs = alternates.state(cid, sid)["runs"]
    # the opening reply and the earliest takes aged out; the newest cap survives
    assert [r["segments"][0]["content"] for r in runs] == takes[-alternates.MAX_ALTERNATES:]


def test_retention_never_drops_the_run_that_is_live(monkeypatch, tmp_path):
    """An oversized set — a file written when the cap was higher — still gets
    trimmed to the cap, but never past the variant the transcript is showing."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    scenes_paths._alts_path(cid, sid).write_text(json.dumps({
        "anchor": 1, "next_guidance": "",
        "runs": [{"created": "", "guidance": "", "segments": [_seg("Fog over the pilings.")]},
                 *({"created": "", "guidance": "", "segments": [_seg(f"Take {n}.")]}
                   for n in range(alternates.MAX_ALTERNATES + 2))],
    }), encoding="utf-8")

    state = alternates.state(cid, sid)

    assert len(state["runs"]) == alternates.MAX_ALTERNATES
    assert state["active"] == 0
    assert state["runs"][0]["segments"] == [_seg("Fog over the pilings.")]


def test_a_new_turn_retires_the_set(monkeypatch, tmp_path):
    """Alternates belong to the generation reroll targets. Once play moves on,
    that generation is no longer the trailing one and its set is dropped."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])

    scenes.append_message(cid, sid, "user", "Go inside.")
    scenes.append_reply(cid, sid, [_seg("The taproom is warm.")])

    assert alternates.state(cid, sid) == {"active": None, "runs": []}
    # and the stale file is cleared the next time the store writes
    _reroll(cid, sid, [_seg("The taproom is empty.")])
    assert [r["segments"] for r in alternates.state(cid, sid)["runs"]] == [
        [_seg("The taproom is warm.")], [_seg("The taproom is empty.")]]


def test_a_hand_edit_that_splices_a_block_in_retires_the_set(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    # the edited user line parses into a user message plus a model block, so the
    # slot the alternates were keyed to is no longer where reroll would write
    scenes.edit_message(cid, sid, 0, "Describe the harbour.\n\n**Mara:** And the boats.")

    assert alternates.state(cid, sid)["runs"] == []


def test_an_edit_inside_the_live_run_keeps_the_set(monkeypatch, tmp_path):
    """Rewriting a message does not move the generation, so the set stays
    reachable and can still be promoted from. (What the edit does to the text
    it replaced is
    `test_a_landed_replacement_survives_an_edit_of_the_text_it_landed_as`.)"""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    scenes.edit_message(cid, sid, 1, "Gulls, and the smell of tar.")

    state = alternates.state(cid, sid)
    assert state["active"] == 2
    assert state["runs"][2]["segments"] == [_seg("Gulls, and the smell of tar.")]
    alternates.promote(cid, sid, 0)
    assert _texts(cid, sid) == ["Describe the harbour.", "Fog over the pilings."]
    # the edited text is what gets parked, not the text it replaced
    assert alternates.state(cid, sid)["runs"][2]["segments"] == [
        _seg("Gulls, and the smell of tar.")]


def test_trailing_transitions_are_stepped_over(monkeypatch, tmp_path):
    """A join/leave/location line sits on top of the run reroll targets; the
    alternates are keyed to the generation beneath it, not to a raw index."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    scenes.append_message(cid, sid, "assistant", "Mara arrives.",
                          speaker=scenes.TRANSITION_SPEAKER)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])

    state = alternates.state(cid, sid)
    assert state["active"] == 1 and len(state["runs"]) == 2
    alternates.promote(cid, sid, 0)
    assert _texts(cid, sid) == ["Describe the harbour.", "Mara arrives.",
                                "Fog over the pilings."]


def test_a_failed_reroll_leaves_the_archived_run_recoverable(monkeypatch, tmp_path):
    """Archive-then-remove, and then the stream dies: the slot is empty but the
    outgoing run is still on disk, so cycling puts it back."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    alternates.archive(cid, sid, "")
    scenes.remove_trailing_assistant_run(cid, sid)

    state = alternates.state(cid, sid)
    assert state["active"] is None and len(state["runs"]) == 1
    alternates.promote(cid, sid, 0)
    assert _texts(cid, sid) == ["Describe the harbour.", "Fog over the pilings."]


def test_a_later_user_turn_retires_an_empty_slot(monkeypatch, tmp_path):
    """The stream died, so the slot is empty and the block count in front of it
    never moved. But the conversation did: promoting now would append a reply
    written for the earlier prompt after the newer one."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    alternates.archive(cid, sid, "")
    scenes.remove_trailing_assistant_run(cid, sid)
    assert len(alternates.state(cid, sid)["runs"]) == 1     # still offered here

    scenes.append_message(cid, sid, "user", "Never mind, go inside.")

    assert alternates.state(cid, sid) == {"active": None, "runs": []}
    with pytest.raises(alternates.AlternateNotFound):
        alternates.promote(cid, sid, 0)


def test_a_transition_landing_after_the_reply_keeps_the_set(monkeypatch, tmp_path):
    """The counterpart to the test above: a transition line is not a turn.
    Reroll steps over it, so the set it sits on top of must survive it."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    scenes.append_message(cid, sid, "assistant", "Mara arrives.",
                          speaker=scenes.TRANSITION_SPEAKER)

    state = alternates.state(cid, sid)
    assert state["active"] == 1 and len(state["runs"]) == 2


def test_editing_the_live_reply_parks_the_text_it_replaced(monkeypatch, tmp_path):
    """An edit is reconciled by the same rule as a reroll — a live run matching
    no stored variant is a new one — so the pre-edit text stays reachable
    instead of being overwritten. Holds after a reroll that repeated itself
    word-for-word, where an earlier design disagreed with itself."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Fog over the pilings.")])   # the model repeated itself
    assert len(alternates.state(cid, sid)["runs"]) == 1

    scenes.edit_message(cid, sid, 1, "Fog, and the smell of tar.")

    state = alternates.state(cid, sid)
    assert state["active"] == 1
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog over the pilings.")], [_seg("Fog, and the smell of tar.")]]
    alternates.promote(cid, sid, 0)
    assert _texts(cid, sid) == ["Describe the harbour.", "Fog over the pilings."]


def test_a_landed_replacement_survives_an_edit_of_the_text_it_landed_as(monkeypatch, tmp_path):
    """The parking promise covers *generated* variants too, which needs them on
    disk: a replacement exists only in the transcript until `reconcile` writes
    it down, and an edit rewrites exactly that text. Without the persist the
    edit does not park the reply, it erases it."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Ice on the ropes.")])

    scenes.edit_message(cid, sid, 1, "Ice on the ropes, and a bell.")

    state = alternates.state(cid, sid)
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog over the pilings.")], [_seg("Ice on the ropes.")],
        [_seg("Ice on the ropes, and a bell.")]]
    assert state["active"] == 2


def test_the_reroll_hint_is_spent_by_the_run_it_steered(monkeypatch, tmp_path):
    """One-shot. Carried forward, it re-labels whatever fills the slot next —
    a hand edit of the guided reply, which no guidance produced."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Ice on the ropes.")], guidance="colder")
    assert alternates.state(cid, sid)["runs"][1]["guidance"] == "colder"

    scenes.edit_message(cid, sid, 1, "Ice on the ropes, and a bell.")

    runs = alternates.state(cid, sid)["runs"]
    assert runs[1]["guidance"] == "colder"       # the take it did steer keeps it
    assert runs[2]["guidance"] == ""             # the edit was not steered at all


def test_reconcile_leaves_a_set_alone_when_the_reply_lands_outside_it(monkeypatch, tmp_path):
    """It persists a resolution, never invents one: a reply that opens a new
    generation moves the anchor, so the stale set must not be rewritten under
    it — that would silently re-point variants at a generation nobody archived
    them for, which is what the anchor exists to prevent."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Ice on the ropes.")])
    before = scenes_paths._alts_path(cid, sid).read_bytes()

    scenes.append_message(cid, sid, "user", "Go inside.")
    scenes.append_reply(cid, sid, [_seg("The taproom is warm.")])
    alternates.reconcile(cid, sid)

    assert scenes_paths._alts_path(cid, sid).read_bytes() == before
    assert alternates.state(cid, sid) == {"active": None, "runs": []}


def test_a_reconciled_variant_is_stamped_when_its_reply_landed(monkeypatch, tmp_path):
    """Not at read time: `state` is a pure read, so a `now` stamp would be the
    reader's clock and would move on every call until something persisted it."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    landed = scenes.read_scene_meta(cid, sid)["updated"]

    first = alternates.state(cid, sid)["runs"][1]["created"]

    assert first == landed
    assert alternates.state(cid, sid)["runs"][1]["created"] == first   # stable


def test_a_failed_reroll_over_consecutive_generations_stays_recoverable(monkeypatch, tmp_path):
    """An empty send (or a director turn) persists no player message, so two
    generations can sit back to back. Removing the newer one then leaves the
    OLDER one at the tail — the slot is empty but the transcript does not end
    on a user line, and the parked reply must still be reachable."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    scenes.append_reply(cid, sid, [_seg("Gulls circle.")])      # no user turn between
    assert scenes.get_turn_sizes(cid, sid) == [1, 1]

    alternates.archive(cid, sid, "")
    scenes.remove_trailing_assistant_run(cid, sid)              # and the stream dies

    state = alternates.state(cid, sid)
    assert state["active"] is None
    assert [r["segments"] for r in state["runs"]] == [[_seg("Gulls circle.")]]

    alternates.promote(cid, sid, 0)

    assert _texts(cid, sid) == ["Describe the harbour.", "Fog over the pilings.",
                                "Gulls circle."]
    assert scenes.get_turn_sizes(cid, sid) == [1, 1]            # both turns intact


def test_a_second_reroll_does_not_archive_over_a_parked_reply(monkeypatch, tmp_path):
    """The failure mode the test above guards: if the empty slot went stale, the
    next reroll would build a fresh set from the *older* generation and write it
    over the sidecar holding the reply nobody has seen since."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    scenes.append_reply(cid, sid, [_seg("Gulls circle.")])
    alternates.archive(cid, sid, "")
    scenes.remove_trailing_assistant_run(cid, sid)

    alternates.archive(cid, sid, "colder")                      # reroll again

    assert [r["segments"] for r in alternates.state(cid, sid)["runs"]] == [
        [_seg("Gulls circle.")]]


def test_a_variant_naming_someone_who_has_since_become_a_player_is_unforged(
        monkeypatch, tmp_path):
    """Cast joins append a transition line, which deliberately does not retire
    the set. So a stored NPC label can come to name a seated player — and
    replaying it verbatim would write a block `turn_sizes` counts but the next
    read parses as a user message, desyncing the boundaries."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    pcs.create_pc(wroot, "Winifred Vance", [], persona=pcs.blank_persona("Winifred Vance"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", "Describe the harbour.")
    scenes.append_reply(cid, sid, [_seg("Winifred nods.", "Winifred")])   # an NPC then
    _reroll(cid, sid, [_seg("Fog over the pilings.")])
    appearances.appear(cid, sid, "pcs", "winifred-vance", "default", "player")

    alternates.promote(cid, sid, 0)

    messages = scenes.read_scene(cid, sid)["messages"]
    assert [m["role"] for m in messages][-1] == "assistant"     # not re-read as the player
    assert scenes.get_turn_sizes(cid, sid) == [1]
    assert alternates.state(cid, sid)["active"] == 0            # boundaries still agree


def test_variants_that_replay_identically_collapse_to_one(monkeypatch, tmp_path):
    """Two takes differing only by a speaker label become the same take once
    that character is seated: `_unforged` strips the label from both. Left as
    two entries the later one is unreachable — promoting it rewrites the same
    transcript and resolves straight back to the earlier index, wedging the
    ‹/› control and retiring a roll proposal for a swap nobody can see."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    pcs.create_pc(worlds.world_root(wid), "Winifred Vance", [],
                  persona=pcs.blank_persona("Winifred Vance"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", "Describe the harbour.")
    scenes.append_reply(cid, sid, [_seg("Fog over the pilings.", "Winifred")])
    _reroll(cid, sid, [_seg("Fog over the pilings.")])   # same words, no label
    assert len(alternates.state(cid, sid)["runs"]) == 2  # distinct while she is an NPC

    appearances.appear(cid, sid, "pcs", "winifred-vance", "default", "player")

    state = alternates.state(cid, sid)
    # the earliest survives, still in its stored form — the label is stripped at
    # replay, not in the record, same as every other variant
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog over the pilings.", "Winifred")]]
    assert state["active"] == 0


def test_repointing_moves_an_undecodable_sidecar_instead_of_raising(monkeypatch, tmp_path):
    """The transcript has already been renamed by the time repoint runs, so a
    hand-mangled sidecar must not abandon the rename half-done."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    scenes_paths._alts_path(cid, sid).write_bytes(b"\xff\xfe not utf-8")

    new_sid = scenes.rename_scene(cid, sid, "Saltmarch Harbour")

    assert new_sid != sid
    assert scenes_paths._alts_path(cid, new_sid).read_bytes() == b"\xff\xfe not utf-8"
    assert alternates.state(cid, new_sid) == {"active": None, "runs": []}


def test_an_interrupted_repoint_leaves_every_set_on_disk(monkeypatch, tmp_path):
    """The transcript is already renamed by the time repoint runs, so a set that
    exists only in this process's memory when a write fails is gone for good —
    the scene moved and its parked replies did not. Publish before clearing.

    And do not raise: `scene_refs.repoint` still owes appearances, audit,
    chronicle, plot and rolls their new id, and failing here to save a sidecar
    strands all of them on an id whose scene no longer exists."""
    cid = _campaign(monkeypatch, tmp_path)
    a, b = _scene_with_reply(cid, "Saltmarch"), _scene_with_reply(cid, "Winterhold")
    _reroll(cid, a, [_seg("Gulls over the pilings.")])
    _reroll(cid, b, [_seg("Snow on the gate.")])
    boom = {"n": 0}
    real = atomic.write_bytes            # captured: the patch replaces this name

    def half_a_write(path, body):
        boom["n"] += 1
        if boom["n"] > 1:
            raise OSError("disk full")
        real(path, body)

    monkeypatch.setattr(alternates.atomic, "write_bytes", half_a_write)
    alternates.repoint_scenes(cid, {a: "0001--moved-a", b: "0002--moved-b"})

    # one landed, one did not — and the one that did not is still at its source
    landed = [s for s in ("0001--moved-a", "0002--moved-b")
              if scenes_paths._alts_path(cid, s).exists()]
    assert len(landed) == 1
    stranded = a if landed == ["0002--moved-b"] else b
    assert scenes_paths._alts_path(cid, stranded).exists()


def test_repointing_survives_a_sidecar_it_cannot_read(monkeypatch, tmp_path):
    """Moving bytes covers content it cannot *decode*, but the read itself can
    still fail. The transcript has already moved by then, so raising would 500
    the rename with the old id gone and the other six stores un-repointed."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    doomed = scenes_paths._alts_path(cid, sid)
    real = pathlib.Path.read_bytes

    def unreadable(self):
        if self == doomed:
            raise PermissionError(self)  # as a hand-chmod'd file would read
        return real(self)

    # patching the read, not `_path`: `_clear` and the writes resolve paths too,
    # and only the read is what a protected file actually refuses
    monkeypatch.setattr(pathlib.Path, "read_bytes", unreadable)
    new_sid = scenes.rename_scene(cid, sid, "Saltmarch Harbour")

    # no monkeypatch.undo() here: it would also revert GRIMOIRE_HOME. The
    # assertions below never touch the doomed path, so the patch is harmless.
    assert new_sid != sid
    assert scenes.read_scene(cid, new_sid)["messages"]        # the rename completed
    assert alternates.state(cid, new_sid) == {"active": None, "runs": []}


def test_a_guided_reroll_that_repeats_itself_is_credited_to_the_new_hint(monkeypatch, tmp_path):
    """Deduplicating is right — two identical takes are one variant — but the
    hint is spent either way, so the matched run must not stay labelled with the
    instruction it was *not* generated from."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")], guidance="warmer")
    alternates.promote(cid, sid, 0)                      # back to the opening reply

    # rerolled with a new hint, and the model returns text the set already has
    _reroll(cid, sid, [_seg("Gulls over the pilings.")], guidance="colder")

    state = alternates.state(cid, sid)
    assert len(state["runs"]) == 2                       # still deduplicated
    assert state["runs"][state["active"]]["guidance"] == "colder"


def test_repadding_two_scenes_onto_one_id_keeps_both_transcripts(monkeypatch, tmp_path):
    """`repad` renames without allocating, so two sources whose targets coincide
    had the second rename overwrite the first. Numbering keeps app-created
    scenes apart, but a store is plain files the user owns — two hand-placed
    transcripts carrying the same number at different widths collide here."""
    cid = _campaign(monkeypatch, tmp_path)
    for sid, body in (("01--saltmarch", "harbour"), ("001--saltmarch", "hollow")):
        scenes_paths._scene_path(cid, sid).write_text(
            f"---\ntitle: Saltmarch\ncreated: x\nupdated: x\n---\n\n{body}\n",
            encoding="utf-8")

    scenes.repad(cid, 4)

    surviving = sorted(p.stem for p in scenes_paths._scenes_dir(cid).glob("*.md"))
    assert len(surviving) == 2                      # neither was overwritten
    bodies = {scenes_paths._scene_path(cid, sid).read_text(encoding="utf-8").strip()
              .splitlines()[-1] for sid in surviving}
    assert bodies == {"harbour", "hollow"}


def test_repad_targets_that_differ_only_by_case_are_kept_apart(monkeypatch, tmp_path):
    """A planned target is checked against other *planned* targets — nothing is
    on disk yet for the filesystem to answer for. Compared exactly, two ids that
    a case-insensitive volume treats as one file both pass."""
    cid = _campaign(monkeypatch, tmp_path)
    for sid, body in (("01--Saltmarch", "harbour"), ("001--saltmarch", "hollow")):
        scenes_paths._scene_path(cid, sid).write_text(
            f"---\ntitle: Saltmarch\ncreated: x\nupdated: x\n---\n\n{body}\n",
            encoding="utf-8")

    scenes.repad(cid, 4)

    ids = sorted(p.stem for p in scenes_paths._scenes_dir(cid).glob("*.md"))
    assert len({s.casefold() for s in ids}) == 2      # distinct even case-blind


def test_a_calendar_plugins_long_date_cannot_overflow_the_id(monkeypatch, tmp_path):
    """`format_sid`'s date section is formatted by a calendar provider, and
    plugins are user-authored — so it is no more bounded than a pasted title.
    The title cap alone still appended a character to an oversized head."""
    sid = scene_ids.format_sid(1, 4, "d" * 240, "saltmarch-harbour")

    assert len(sid) <= scene_ids.MAX_SID
    assert sid.startswith("0001--")
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert len(f"{sid}.alts.json") <= 255


def test_repad_finds_an_unclearable_destination_before_it_moves_anything(monkeypatch, tmp_path):
    """The destination clear is allowed to fail loudly — but `repad` renames
    every transcript before `scene_refs.repoint` runs, so discovering it there
    means the whole campaign has already moved. Clearing up front costs the
    request and nothing else."""
    cid = _campaign(monkeypatch, tmp_path)
    narrow = _scene_with_reply(cid, "Saltmarch")
    widened = scene_ids.format_sid(1, 4, None, "saltmarch")
    scenes_paths._alts_path(cid, widened).write_text("{}", encoding="utf-8")
    real = pathlib.Path.unlink

    def stuck(self, missing_ok=False):
        if self == scenes_paths._alts_path(cid, widened):
            raise PermissionError(13, "in use")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", stuck)
    with pytest.raises(OSError):
        scenes.repad(cid, 4)

    # nothing moved: the scene is still at its old id, with its transcript
    assert scenes_paths._scene_path(cid, narrow).exists()
    assert not scenes_paths._scene_path(cid, widened).exists()


def test_a_destination_orphan_that_will_not_clear_is_not_reported_as_moved(monkeypatch, tmp_path):
    """The tolerance the source cleanup earns does not extend to a destination.
    A destination is changing hands, so a sidecar left there attaches another
    scene's variants to the transcript moving in — there is no safe copy to fall
    back on, the way a published source has."""
    cid = _campaign(monkeypatch, tmp_path)
    a = _scene_with_reply(cid, "Saltmarch")
    dest = "0001--moved-a"
    scenes_paths._alts_path(cid, dest).write_text(json.dumps({
        "anchor": 1, "next_guidance": "",
        "runs": [{"created": "", "guidance": "", "segments": [_seg("A ghost's take.")]}],
    }), encoding="utf-8")
    real = pathlib.Path.unlink

    def stuck(self, missing_ok=False):
        if self == scenes_paths._alts_path(cid, dest):
            raise PermissionError(13, "in use")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", stuck)
    with pytest.raises(OSError):
        alternates.repoint_scenes(cid, {a: dest})


def test_a_source_that_will_not_unlink_still_lets_the_repoint_finish(monkeypatch, tmp_path):
    """Third place the same judgement applies. The bytes are already published
    at the destination, so a source that will not unlink costs an orphan —
    raising would abort the fan-out and leave every other store keyed to a
    scene id that no longer exists."""
    cid = _campaign(monkeypatch, tmp_path)
    a = _scene_with_reply(cid, "Saltmarch")
    _reroll(cid, a, [_seg("Gulls over the pilings.")])
    source = scenes_paths._alts_path(cid, a)
    real = pathlib.Path.unlink

    def stuck(self, missing_ok=False):
        if self == source:
            raise PermissionError(13, "in use")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", stuck)
    alternates.repoint_scenes(cid, {a: "0001--moved-a"})     # does not raise

    moved = scenes_paths._alts_path(cid, "0001--moved-a")
    assert moved.exists()                                    # published anyway
    assert json.loads(moved.read_text(encoding="utf-8"))["runs"]


def test_a_pasted_title_still_makes_a_scene_the_sidecar_can_shadow(monkeypatch, tmp_path):
    """Title slugs are unbounded. Budgeting the id against `.md` alone let one
    fit the transcript and overflow `<sid>.alts.json` — and `_sid_taken` stats
    the sidecar on every allocation, so such a scene could not even be created.
    """
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Saltmarch " * 60)

    assert len(scenes_paths._alts_path(cid, sid).name) <= 255
    scenes.append_message(cid, sid, "user", "Describe the harbour.")
    scenes.append_reply(cid, sid, [_seg("Fog over the pilings.")])
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    assert len(alternates.state(cid, sid)["runs"]) == 2      # the sidecar wrote
    scenes.delete_scene(cid, sid)                            # and cleans up


def test_renaming_a_legacy_scene_to_a_pasted_title_is_bounded_too(monkeypatch, tmp_path):
    """`format_sid` is not the only place an id is built: `rename_scene` keeps
    the pre-migration `<created>-<slug>` scheme for legacy ids, and that branch
    was still unbounded — the rename lands, then repointing the sidecar raises
    with the transcript already moved."""
    cid = _campaign(monkeypatch, tmp_path)
    legacy = "2024-01-01-saltmarch"
    scenes_paths._scene_path(cid, legacy).write_text(
        "---\ntitle: Saltmarch\ncreated: 2024-01-01T00:00:00Z\n"
        "updated: 2024-01-01T00:00:00Z\n---\n\n", encoding="utf-8")

    new_sid = scenes.rename_scene(cid, legacy, "Saltmarch " * 60)

    assert len(scenes_paths._alts_path(cid, new_sid).name) <= 255
    assert scenes_paths._scene_path(cid, new_sid).exists()


def test_a_sidecar_that_will_not_go_fails_the_delete_instead_of_faking_it(monkeypatch, tmp_path):
    """Suppressing every OSError reported success while the parked replies
    stayed on disk. Only the overlong-name case is a sidecar that cannot
    exist; everything else is one that does and would not go."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    real = pathlib.Path.unlink

    def stuck(self, missing_ok=False):
        if self == scenes_paths._alts_path(cid, sid):
            raise PermissionError(13, "in use")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", stuck)
    with pytest.raises(PermissionError):
        scenes.delete_scene(cid, sid)
    # (no `monkeypatch.undo()` — it would revert GRIMOIRE_HOME too, and only
    # `unlink` is patched, so the path resolvers below are already honest)
    assert scenes_paths._scene_path(cid, sid).exists()   # the scene is still there


def test_a_sidecar_it_cannot_read_is_left_alone_not_deleted(monkeypatch, tmp_path):
    """Skipping the read is only half of it: the cleanup sweeps every source id,
    so a file that could not be *carried* would be unlinked instead — turning
    "we could not move your variants" into "we deleted them"."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    doomed = scenes_paths._alts_path(cid, sid)
    body = doomed.read_bytes()
    real = pathlib.Path.read_bytes

    def unreadable(self):
        if self == doomed:
            raise PermissionError(self)
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", unreadable)
    scenes.rename_scene(cid, sid, "Saltmarch Harbour")

    assert doomed.exists()                    # still there for whoever fixes the mode
    assert real(doomed) == body               # and untouched


def test_a_date_stamp_rename_never_lands_on_an_orphaned_sidecar(monkeypatch, tmp_path):
    """The first date set renames the scene file too (`_stamp_start_date`), so
    it allocates an id like every other path and must skip one a leftover
    sidecar still occupies. Reachable because numbering restarts once the
    transcript is gone: the next scene of the same title and date builds the
    very id the orphan sits on."""
    cid = _campaign(monkeypatch, tmp_path)
    doomed = _scene_with_reply(cid, "Saltmarch")
    stamped = scenes.set_datetime(cid, doomed, "2026-07-04")["id"]
    _reroll(cid, stamped, [_seg("Gulls over the pilings.")])
    scenes_paths._scene_path(cid, stamped).unlink()     # transcript gone, sidecar left
    parked = [r["segments"] for r in
              json.loads(scenes_paths._alts_path(cid, stamped).read_text(encoding="utf-8"))["runs"]]

    fresh = scenes.create_scene(cid, "Saltmarch")       # numbering restarts at 001
    landed = scenes.set_datetime(cid, fresh, "2026-07-04")["id"]

    assert landed != stamped                            # did not take the orphan's id
    # and the orphan is untouched — not adopted, not overwritten
    assert [r["segments"] for r in json.loads(
        scenes_paths._alts_path(cid, stamped).read_text(encoding="utf-8"))["runs"]] == parked


def test_seating_a_player_who_shares_an_npcs_name_keeps_the_set(monkeypatch, tmp_path):
    """Role is derived, not stored, so seating a player whose name matches an
    NPC label re-reads that historical block as a user message. The transcript
    did not change and the slot did not move, so the set must survive — which is
    why the anchor counts messages rather than model blocks."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    pcs.create_pc(worlds.world_root(wid), "Winifred Vance", [],
                  persona=pcs.blank_persona("Winifred Vance"))
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", "Describe the harbour.")
    # untracked, so `turn_sizes` still fits once this block is reclassified —
    # otherwise reroll refuses on the desync and there is no slot to key at all
    scenes.append_message(cid, sid, "assistant", "An NPC speaks.", speaker="Winifred")
    scenes.append_message(cid, sid, "user", "Go on.")
    scenes.append_reply(cid, sid, [_seg("Fog over the pilings.")])
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    assert len(alternates.state(cid, sid)["runs"]) == 2

    appearances.appear(cid, sid, "pcs", "winifred-vance", "default", "player")

    state = alternates.state(cid, sid)
    assert state["active"] == 1
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog over the pilings.")], [_seg("Gulls over the pilings.")]]


def test_a_hand_written_variant_is_read_as_the_writer_would_store_it(monkeypatch, tmp_path):
    """`append_reply` strips content, drops blank segments and refuses a speaker
    it cannot label. A sidecar spelling the same run any other way would never
    match what promoting it produces, so each promotion would file a normalised
    duplicate and report *that* as active instead of the index asked for."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    scenes_paths._alts_path(cid, sid).write_text(json.dumps({
        "anchor": 1, "next_guidance": "",
        "runs": [{"created": "", "guidance": "", "segments": [
            {"speaker": "", "content": "  A hand-written take.  "},   # empty speaker, padded
            {"speaker": "Mara", "content": "   "},                    # blank: dropped
        ]}],
    }), encoding="utf-8")

    assert alternates.state(cid, sid)["runs"][0]["segments"] == [_seg("A hand-written take.")]

    alternates.promote(cid, sid, 0)

    state = alternates.state(cid, sid)
    assert _texts(cid, sid) == ["Describe the harbour.", "A hand-written take."]
    assert state["active"] == 0                        # the index asked for
    assert len(state["runs"]) == 2                     # the parked live run, not a duplicate
    alternates.promote(cid, sid, 0)                    # and it stays put
    assert len(alternates.state(cid, sid)["runs"]) == 2


def test_untracked_legacy_scenes_get_alternates_too(monkeypatch, tmp_path):
    """A transcript written before turn boundaries existed carries no
    `turn_sizes`; reroll takes the whole trailing model run, and so do we."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Legacy")
    scenes.append_message(cid, sid, "user", "Describe the harbour.")
    scenes.append_message(cid, sid, "assistant", "Fog.")
    scenes.append_message(cid, sid, "assistant", "And gulls.")
    assert scenes.get_turn_sizes(cid, sid) == []

    _reroll(cid, sid, [_seg("Rain.")])

    state = alternates.state(cid, sid)
    assert [r["segments"] for r in state["runs"]] == [
        [_seg("Fog."), _seg("And gulls.")], [_seg("Rain.")]]
    alternates.promote(cid, sid, 0)
    assert _texts(cid, sid) == ["Describe the harbour.", "Fog.", "And gulls."]


def test_archive_writes_nothing_for_a_scene_that_has_no_generation(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Empty")
    alternates.archive(cid, sid, "")
    assert not scenes_paths._alts_path(cid, sid).exists()


def test_a_second_reroll_over_an_empty_slot_re_aims_the_hint(monkeypatch, tmp_path):
    """The first reroll's stream died, so there is nothing left to archive —
    but the new hint still belongs to whatever lands next."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    alternates.archive(cid, sid, "colder")
    scenes.remove_trailing_assistant_run(cid, sid)

    alternates.archive(cid, sid, "warmer")
    scenes.append_reply(cid, sid, [_seg("Sun on the pilings.")])

    assert alternates.state(cid, sid)["runs"][1]["guidance"] == "warmer"


def test_a_stored_hint_is_bounded(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls.")], guidance="x" * 5000)
    assert alternates.state(cid, sid)["runs"][1]["guidance"] == \
        "x" * alternates.MAX_GUIDANCE_CHARS


def test_a_speaker_the_writer_would_sanitise_is_normalised_not_rejected(monkeypatch, tmp_path):
    """`**You (Mara):**` and `Grimoire (⁣Roll)` used to be rejected here: written
    verbatim they read back as a player line or a synthetic one, and `turn_sizes`
    would count a block the transcript does not have. `_block` sanitises both to
    the narrator label now (#95), so replaying them is faithful and the variant is
    kept with the label dropped.

    Which is the point of round-tripping through the real serializer rather than
    copying its rules: the rules changed under this store and the judgement
    followed on its own."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    for spoiled in ("You (Mara)", "Grimoire (\u2063Roll)"):
        scenes_paths._alts_path(cid, sid).write_text(json.dumps({
            "anchor": 1, "next_guidance": "",
            "runs": [{"created": "", "guidance": "", "segments":
                      [{"speaker": spoiled, "content": "A ghost's take."}]}],
        }), encoding="utf-8")
        runs = alternates.state(cid, sid)["runs"]
        assert [r["segments"] for r in runs][0] == [_seg("A ghost's take.")]


@pytest.mark.parametrize("body", [
    "{not json",                                              # half-written
    '{"anchor": 1, "runs": "everything"}',                     # wrong container
    '{"anchor": 1, "runs": [{"segments": ["a bare string"]}]}',  # wrong segment shape
    '{"anchor": 1, "runs": [{"segments": [{"content": 7}]}]}',  # unusable content
    '{"anchor": 1, "runs": [{"segments": []}]}',               # a variant of nothing
    '{"anchor": 1, "runs": [{"segments": [{"content": "  "}]}]}',  # promotes to nothing
    # A synthetic speaker is internal metadata, never model output: appending it
    # would count in turn_sizes but not as a model block, desyncing on the spot.
    # These two carry a matching `anchor` on purpose — without it the record is
    # rejected as stale and the test would pass without exercising the guard.
    '{"anchor": 1, "runs": [{"segments": [{"content": "x", "speaker": "\\u2063Roll"}]}]}',
    '{"anchor": 1, "runs": [{"segments": [{"content": "x", "speaker": "\\u2063Scene"}]}]}',
    # a marker buried in the content splits one segment into two blocks
    '{"anchor": 1, "runs": [{"segments": [{"content": "a\\n\\n**Mara:** b"}]}]}',
])
def test_an_unusable_sidecar_reads_as_no_alternates(monkeypatch, tmp_path, body):
    """It never reaches the transcript writer: `promote` hands `segments`
    straight to `append_reply`, so a shape this store cannot vouch for has to
    stop here rather than surface as a 500 mid-swap."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    scenes_paths._alts_path(cid, sid).write_text(body, encoding="utf-8")
    assert alternates.state(cid, sid) == {"active": None, "runs": []}
    with pytest.raises(alternates.AlternateNotFound):
        alternates.promote(cid, sid, 0)


def test_renaming_a_scene_carries_its_alternates(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])

    new_sid = scenes.rename_scene(cid, sid, "Saltmarch Harbour")

    assert new_sid != sid
    assert not scenes_paths._alts_path(cid, sid).exists()
    assert len(alternates.state(cid, new_sid)["runs"]) == 2


def test_repoint_leaves_a_scene_without_alternates_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_refs.repoint(cid, {"001--gone": "001--elsewhere"})   # must not raise or create


def test_repoint_does_not_overwrite_an_occupied_sidecar(monkeypatch, tmp_path):
    """A mapping that swaps two ids must move both sets, not let one land on
    the other — every source is read before any target is written."""
    cid = _campaign(monkeypatch, tmp_path)
    first, second = _scene_with_reply(cid, "One"), _scene_with_reply(cid, "Two")
    _reroll(cid, first, [_seg("First take.")])
    _reroll(cid, second, [_seg("Second take.")])
    before = {sid: scenes_paths._alts_path(cid, sid).read_text(encoding="utf-8")
              for sid in (first, second)}

    alternates.repoint_scenes(cid, {first: second, second: first})

    assert scenes_paths._alts_path(cid, second).read_text(encoding="utf-8") == before[first]
    assert scenes_paths._alts_path(cid, first).read_text(encoding="utf-8") == before[second]


def test_repad_does_not_hand_a_widened_scene_an_orphans_variants(monkeypatch, tmp_path):
    """`repad` re-numbers every scene to a uniform width and must land on the
    width-normalised id — it cannot skip a taken one the way the allocating
    paths do. So an orphaned sidecar sitting on that id is only cleanable here,
    and it must not come with the transcript that moves in."""
    cid = _campaign(monkeypatch, tmp_path)
    narrow = _scene_with_reply(cid, "Saltmarch")                # 001--saltmarch
    widened = scene_ids.format_sid(1, 4, None, "saltmarch")     # 0001--saltmarch
    scenes_paths._alts_path(cid, widened).write_text(json.dumps({
        "anchor": 1, "next_guidance": "",
        "runs": [{"created": "", "guidance": "", "segments": [_seg("A ghost's take.")]}],
    }), encoding="utf-8")

    scenes.repad(cid, 4)

    assert scenes_paths._scene_path(cid, widened).exists()      # the transcript moved
    assert alternates.state(cid, widened) == {"active": None, "runs": []}
    assert not scenes_paths._alts_path(cid, narrow).exists()


def test_deleting_a_scene_removes_its_sidecar(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])

    scenes.delete_scene(cid, sid)

    assert not scenes_paths._alts_path(cid, sid).exists()


def test_a_new_scene_never_adopts_an_orphaned_sidecar(monkeypatch, tmp_path):
    """Numbering comes from the `.md` files, so a deleted scene's id is free for
    the next one. An orphaned sidecar — a crash between the two unlinks, or one
    an older build left — must not come with it."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid, "Saltmarch")
    _reroll(cid, sid, [_seg("Gulls over the pilings.")])
    scenes_paths._scene_path(cid, sid).unlink()        # transcript gone, sidecar left
    assert scenes_paths._alts_path(cid, sid).exists()

    fresh = scenes.create_scene(cid, "Saltmarch")

    assert fresh != sid
    scenes.append_message(cid, fresh, "user", "Describe the harbour.")
    scenes.append_reply(cid, fresh, [_seg("A new reply.")])
    assert alternates.state(cid, fresh) == {"active": None, "runs": []}


def test_the_sidecar_is_json_a_human_can_read(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _scene_with_reply(cid)
    _reroll(cid, sid, [_seg("Gulls over the pilings.")], guidance="colder")
    data = json.loads(scenes_paths._alts_path(cid, sid).read_text(encoding="utf-8"))
    assert data["anchor"] == 1
    # both: the archived run, and the replacement `reconcile` wrote down when it
    # landed -- a run that lived only in the transcript would not survive an edit
    assert [r["segments"][0]["content"] for r in data["runs"]] == [
        "Fog over the pilings.", "Gulls over the pilings."]
    # the hint moved onto the run it steered and is spent, so it cannot re-label
    # whatever fills the slot next
    assert data["runs"][1]["guidance"] == "colder"
    assert data["next_guidance"] == ""
    # nothing about what is on screen is stored -- it is derived on every read
    assert "active" not in data
