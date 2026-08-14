"""True undo of an applied write (#31).

The pipeline under test is: `absorb.apply_edits` writes and journals a reversal
snapshotted just before the write, and `undo.undo` puts that snapshot back --
but only if the record still reads exactly what the write produced. The
compare-and-swap is the point of the whole module: without it, undoing an older
edit would silently discard everything that landed on the record since.

Each kind gets a round trip through the real writers rather than a unit test of
`write_value`, because what is being checked is that the value the store hands
back is the value the store would accept -- `playstate.compose_body`,
`plot.set_movement`'s beat append and the card's locked-version resolution are
all places where a plausible inverse is not one.
"""

import pytest

from grimoire.store import (absorb, appearances, campaigns, characters, commitments,
                            dossiers, entities, groupstate, journal, playstate, plot,
                            relationships, scenes, undo, worlds)


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


@pytest.fixture
def sid(cid):
    return scenes.create_scene(cid, "The blockade")


def _only(cid) -> dict:
    entries = journal.read(cid)
    assert len(entries) == 1, entries
    return entries[0]


def _cast(cid, sid, name="Mara", **card_fields):
    """A character locked into the scene, which is what makes a card edit and a
    dossier edit resolvable at all."""
    croot = campaigns.campaign_root(cid)
    card = characters.blank_card(name)
    card["data"].update(card_fields)
    aid = characters.create_character(croot, name, "main", card)[0]
    appearances.appear(cid, sid, "characters", aid, "main", "npc")
    return aid


# --- the kinds that replace a stored text value -----------------------------

def _lore_edit(before, after):
    return {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body",
            "before": before, "after": after, "authored": False}


def test_lore_edit_is_journalled_with_a_reversal(cid, sid):
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    entry = _only(cid)
    assert entry["source"] == "absorb" and entry["scene"] == sid
    assert entry["ref"] == {"kind": "lore", "id": "pact"}
    assert entry["before"] == "old body" and entry["after"] == "new body"
    assert entry["undo"]["target"] == {"w": "entity", "kind": "lore", "id": "pact"}


def test_undoing_a_lore_edit_puts_the_body_back(cid, sid):
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    undo.undo(cid, "j1")
    assert overlay.read_entity(cid, "lore", "pact")["body"].strip() == "old body"


def test_undo_stamps_the_entry_and_journals_the_reversal(cid, sid):
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    written = undo.undo(cid, "j1")
    assert written["id"] == "j2" and written["source"] == "undo"
    assert written["reverted"] == "j1"
    # The reversal reads as the diff run backwards, which is what it is.
    assert written["before"] == "new body" and written["after"] == "old body"
    assert journal.get(cid, "j1")["undone"]["by"] == "j2"


def test_undoing_the_undo_is_redo(cid, sid):
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    undo.undo(cid, "j1")
    undo.undo(cid, "j2")
    assert overlay.read_entity(cid, "lore", "pact")["body"].strip() == "new body"


def test_undoing_twice_is_refused(cid, sid):
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    undo.undo(cid, "j1")
    with pytest.raises(undo.AlreadyUndone):
        undo.undo(cid, "j1")


def test_an_unknown_entry_is_refused(cid):
    with pytest.raises(undo.EntryNotFound):
        undo.undo(cid, "j9")


def test_a_record_that_moved_since_refuses_the_undo(cid, sid):
    """The whole point of the compare-and-swap: the reader asked to take back one
    edit, not to discard what landed on the record afterwards."""
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    overlay.update_entity(cid, "lore", "pact", body="somebody else's edit")
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, "j1")
    assert overlay.read_entity(cid, "lore", "pact")["body"].strip() == "somebody else's edit"


def test_a_deleted_record_refuses_the_undo(cid, sid):
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    overlay.delete_entity(cid, "lore", "pact")
    with pytest.raises(undo.UndoConflict):
        undo.undo(cid, "j1")


def test_undoing_a_character_state_edit(cid, sid):
    croot = campaigns.campaign_root(cid)
    aid = _cast(cid, sid)
    playstate.write_state(croot, aid, "calm")
    edit = {"id": "cs", "kind": "character_state",
            "target": {"kind": "characters", "id": aid},
            "label": "Mara — current state", "field": "current_state",
            "before": "calm", "after": "shaken", "authored": False}
    absorb.apply_edits(cid, [edit], sid)
    assert playstate.read_state(croot, aid)["current_state"] == "shaken"
    undo.undo(cid, "j1")
    assert playstate.read_state(croot, aid)["current_state"] == "calm"


def test_undoing_a_card_field_edit(cid, sid):
    aid = _cast(cid, sid, personality="aloof")
    edit = {"id": "au", "kind": "authored",
            "target": {"kind": "characters", "id": aid},
            "label": "Mara — personality (card edit)", "field": "personality",
            "before": "aloof", "after": "warmer", "authored": True}
    absorb.apply_edits(cid, [edit], sid)
    aroot = appearances.locked_actor_root(cid)
    vid = appearances.locked_version(cid, "characters", aid)
    assert characters.read_card(aroot, aid, vid)["data"]["personality"] == "warmer"
    undo.undo(cid, "j1")
    assert characters.read_card(aroot, aid, vid)["data"]["personality"] == "aloof"


def test_undoing_a_dossier_edit(cid, sid):
    croot = campaigns.campaign_root(cid)
    aid = _cast(cid, sid)
    dossiers.write(croot, aid, "Guarded.")
    edit = {"id": "d", "kind": "dossier", "target": {"kind": "characters", "id": aid},
            "label": "Mara — dossier", "field": "dossier",
            "before": "Guarded.", "after": "Warmer, but watchful.", "authored": False}
    absorb.apply_edits(cid, [edit], sid)
    undo.undo(cid, "j1")
    assert dossiers.read(croot, aid) == "Guarded."


def test_undoing_a_group_state_edit(cid, sid):
    croot = campaigns.campaign_root(cid)
    gid = entities.create_entity(croot, "groups", "The Harbourmen")
    groupstate.write_state(croot, gid, groupstate.compose_body({"goals": "hold the pier"}))
    edit = {"id": "gs", "kind": "group_state", "target": {"kind": "groups", "id": gid},
            "label": "The Harbourmen — goals", "field": "goals",
            "before": "hold the pier", "after": "take the pier", "authored": False}
    absorb.apply_edits(cid, [edit], sid)
    undo.undo(cid, "j1")
    assert groupstate.read_state(croot, gid)["goals"] == "hold the pier"


# --- the record kinds, where the reversal is the whole record ----------------

def _feeling_edit(after, before="", **payload):
    base = {"from": "characters:mara", "to": "characters:seraphine",
            "trust": 4, "affection": 3, "tension": 1, "note": ""}
    return {"id": "rel", "kind": "relationship",
            "target": {"kind": "relationships", "id": "characters:mara"},
            "label": "Mara → Seraphine", "field": "feeling", "before": before,
            "after": after, "authored": False, "payload": {**base, **payload}}


def test_undoing_a_new_feeling_removes_it(cid, sid):
    absorb.apply_edits(cid, [_feeling_edit("trust 4, affection 3, tension 1")], sid)
    assert relationships.get_feeling(cid, "characters:mara", "characters:seraphine")
    undo.undo(cid, "j1")
    assert relationships.get_feeling(cid, "characters:mara", "characters:seraphine") is None


def test_undoing_a_changed_feeling_restores_the_numbers(cid, sid):
    relationships.set_feeling(cid, "characters:mara", "characters:seraphine", 1, 1, 4, "wary")
    absorb.apply_edits(cid, [_feeling_edit("trust 4, affection 3, tension 1",
                                           before="trust 1, affection 1, tension 4 (wary)")], sid)
    undo.undo(cid, "j1")
    assert relationships.get_feeling(cid, "characters:mara", "characters:seraphine") == {
        "trust": 1, "affection": 1, "tension": 4, "note": "wary"}


def test_undoing_a_bond_removes_it(cid, sid):
    edit = {"id": "bond", "kind": "bond",
            "target": {"kind": "relationships", "id": "characters:mara"},
            "label": "Mara ↔ Seraphine", "field": "bond", "before": "",
            "after": "allies", "authored": False,
            "payload": {"a": "characters:mara", "b": "characters:seraphine",
                        "type": "allies"}}
    absorb.apply_edits(cid, [edit], sid)
    assert relationships.get_bond(cid, "characters:mara", "characters:seraphine")
    undo.undo(cid, "j1")
    assert relationships.get_bond(cid, "characters:mara", "characters:seraphine") is None


def _plot_edit(after, status="advanced", before=""):
    return {"id": "plot:the-map", "kind": "plot",
            "target": {"kind": "plot", "id": "the-map"},
            "label": "The map — advanced", "field": "beat", "before": before,
            "after": after, "authored": False,
            "payload": {"id": "the-map", "title": "The map", "status": status,
                        "scene": "s1"}}


def test_undoing_a_new_plot_thread_removes_it(cid, sid):
    absorb.apply_edits(cid, [_plot_edit("It moved.")], sid)
    assert plot.get(cid, "the-map")
    undo.undo(cid, "j1")
    assert plot.get(cid, "the-map") is None


def test_undoing_a_plot_beat_removes_only_that_beat(cid, sid):
    plot.set_movement(cid, "the-map", "The map", "open", "It surfaced.", "s0")
    absorb.apply_edits(cid, [_plot_edit("It moved.", before="open — It surfaced.")], sid)
    assert len(plot.get(cid, "the-map")["beats"]) == 2
    undo.undo(cid, "j1")
    thread = plot.get(cid, "the-map")
    assert [b["text"] for b in thread["beats"]] == ["It surfaced."]
    assert thread["status"] == "open" and thread["last_scene"] == "s0"


def test_undoing_a_commitment_beat_restores_the_record(cid, sid):
    commitments.set_movement(cid, "pay-mara", "Pay Mara", "promise", "open", None,
                             "He promised.", "s0")
    edit = {"id": "com", "kind": "commitment",
            "target": {"kind": "commitments", "id": "pay-mara"},
            "label": "Pay Mara — broken", "field": "beat",
            "before": "promise, open — He promised. [1 beat, last moved in s0]",
            "after": "He did not.", "authored": False,
            "payload": {"id": "pay-mara", "title": "Pay Mara", "kind": "promise",
                        "status": "broken", "due": None, "scene": "s1"}}
    absorb.apply_edits(cid, [edit], sid)
    assert commitments.get(cid, "pay-mara")["status"] == "broken"
    undo.undo(cid, "j1")
    record = commitments.get(cid, "pay-mara")
    assert record["status"] == "open" and len(record["beats"]) == 1


# --- the kinds that carry no reversal ---------------------------------------

def test_a_created_lore_entry_is_journalled_but_not_undoable(cid, sid):
    edit = {"id": "new:lore", "kind": "new_lore",
            "target": {"kind": "lore", "id": ""}, "label": "The Tithe — new lore",
            "field": "body", "before": "", "after": "A debt owed each spring.",
            "authored": False, "payload": {"name": "The Tithe", "keys": ""}}
    absorb.apply_edits(cid, [edit], sid)
    entry = _only(cid)
    assert entry["undo"] is None
    assert entry["ref"]["id"] == "the-tithe"     # the id the creation allocated
    assert "deleting" in entry["why"]
    with pytest.raises(undo.NotUndoable):
        undo.undo(cid, entry["id"])


def test_a_fact_is_journalled_but_not_undoable(cid, sid):
    edit = {"id": "fact", "kind": "fact", "target": {"kind": "facts", "id": ""},
            "label": "recorded", "field": "text", "before": "",
            "after": "The pier is closed.", "authored": False,
            "payload": {"text": "The pier is closed.", "date": "", "scene": "s1"}}
    absorb.apply_edits(cid, [edit], sid)
    entry = _only(cid)
    assert entry["undo"] is None and entry["why"] == undo.NOT_UNDOABLE["fact"]


def test_every_declined_kind_has_a_reason(cid):
    assert all(text.strip() for text in undo.NOT_UNDOABLE.values())
    assert undo.why("no such kind") == undo.GENERIC
    assert undo.why(None) == undo.GENERIC


# --- hand edits made outside the absorb pipeline ----------------------------

def test_journalled_records_a_hand_edit(cid):
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    target = {"w": "entity", "kind": "lore", "id": "pact"}
    with undo.journalled(cid, target, kind="lore",
                         ref={"kind": "lore", "id": "pact"}, field="body",
                         label="The Pact — lore"):
        overlay.update_entity(cid, "lore", "pact", body="typed by hand")
    entry = _only(cid)
    assert entry["source"] == "manual" and entry["scene"] == ""
    assert entry["before"].strip() == "old body"
    assert entry["after"].strip() == "typed by hand"
    undo.undo(cid, entry["id"])
    assert overlay.read_entity(cid, "lore", "pact")["body"].strip() == "old body"


def test_journalled_records_nothing_when_the_write_raises(cid):
    target = {"w": "entity", "kind": "lore", "id": "pact"}
    with pytest.raises(RuntimeError):
        with undo.journalled(cid, target, kind="lore",
                             ref={"kind": "lore", "id": "pact"}, field="body",
                             label="The Pact — lore"):
            raise RuntimeError("the write failed")
    assert journal.read(cid) == []


def test_journalled_never_sinks_the_write_it_wraps(cid, monkeypatch):
    """The edit has already landed by the time the append runs, so a journal
    failure has to cost the history rather than the edit."""
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    monkeypatch.setattr(journal, "append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with undo.journalled(cid, {"w": "entity", "kind": "lore", "id": "pact"},
                         kind="lore", ref={"kind": "lore", "id": "pact"},
                         field="body", label="The Pact — lore"):
        overlay.update_entity(cid, "lore", "pact", body="typed by hand")
    assert overlay.read_entity(cid, "lore", "pact")["body"].strip() == "typed by hand"


# --- the rolling panel follows the reversal ---------------------------------

def test_undo_repoints_the_rolling_delta(cid, sid):
    """`changes.json` means "how this record last moved"; after a reversal it
    last moved back."""
    from grimoire.store import changes
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    assert changes.read(cid)["lore/pact"]["fields"][0]["after"] == "new body"
    undo.undo(cid, "j1")
    field = changes.read(cid)["lore/pact"]["fields"][0]
    assert field["before"] == "new body" and field["after"] == "old body"


def test_undo_of_a_non_browsable_kind_leaves_the_rolling_delta_alone(cid, sid):
    from grimoire.store import changes
    absorb.apply_edits(cid, [_plot_edit("It moved.")], sid)
    undo.undo(cid, "j1")
    assert changes.read(cid) == {}


# --- writers with no absorb fixture of their own ----------------------------

def test_voice_drift_round_trips_note_and_provenance(cid, sid):
    """The one writer whose value is a pair: a note and the anchor fingerprint it
    was judged against. Restoring the note alone would leave a live corrective
    citing whichever anchor happened to be recorded."""
    from grimoire.store import voice_drift
    croot = campaigns.campaign_root(cid)
    aid = _cast(cid, sid)
    target = {"w": "voice_drift", "id": aid}
    assert undo.read_value(cid, target) == {"note": "", "anchor": ""}
    voice_drift.write(croot, aid, "She has stopped hedging.", "fp-1")
    prior = undo.read_value(cid, target)
    assert prior == {"note": "She has stopped hedging.", "anchor": "fp-1"}
    voice_drift.write(croot, aid, "Something else.", "fp-2")
    undo.write_value(cid, target, prior)
    assert undo.read_value(cid, target) == prior
    # Clearing is a delete, not a blank file — the restore has to reach that too.
    undo.write_value(cid, target, {"note": "", "anchor": ""})
    assert not voice_drift.flag_path(croot, aid).exists()


def test_a_sheet_edit_is_journalled_without_a_reversal(scene_with_sheeted_cast):
    """Every applied edit lands in the history, including the kinds that cannot
    be taken back through it."""
    from grimoire.store import audit, sheets
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "hp",
         "value": {"current": live["current"] - 2}, "note": ""}]})
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied and not failures
    entry = _only(cid)
    assert entry["kind"] == "sheet" and entry["undo"] is None
    assert entry["why"] == undo.NOT_UNDOABLE["sheet"]
    assert entry["ref"] == {"kind": "characters", "id": "mara"}
    with pytest.raises(undo.NotUndoable):
        undo.undo(cid, entry["id"])


def test_journalled_skips_a_write_that_moved_nothing(cid):
    """An entity PUT that only touched `keys` still writes; a row with an empty
    diff and a no-op Undo button in front of the reader is not the record of it."""
    from grimoire.store import overlay
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    with undo.journalled(cid, {"w": "entity", "kind": "lore", "id": "pact"},
                         kind="lore", ref={"kind": "lore", "id": "pact"},
                         field="body", label="The Pact — lore"):
        overlay.update_entity(cid, "lore", "pact", keys="pact, oath")
    assert journal.read(cid) == []


# --- the display logs follow the reversal -----------------------------------

def _cited_lore_edit(before, after):
    return {**_lore_edit(before, after),
            "review": {"quote": "The pact is broken.", "speaker": "Winifred",
                       "certainty": 0.9, "authority": "stated", "band": "high"}}


def test_undo_clears_the_provenance_marker(cid, sid):
    """A citation explains the value a field HOLDS. The reversal put an older
    value back, and the quote that justified the edit does not justify it —
    there is no earlier citation to fall back to, so uncited is the honest
    state."""
    from grimoire.store import provenance
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_cited_lore_edit("old body", "new body")], sid)
    assert provenance.read(cid)["lore/pact#body"]["quote"] == "The pact is broken."
    undo.undo(cid, "j1")
    assert provenance.read(cid) == {}


def test_undo_clears_the_marker_for_a_kind_the_delta_never_covered(cid, sid):
    """Provenance covers every kind, so the clearing has to as well — a plot
    beat is cited and is not browsable."""
    from grimoire.store import provenance
    edit = {**_plot_edit("It moved."),
            "review": {"quote": "The map moved.", "speaker": "Mara",
                       "certainty": 0.8, "authority": "stated", "band": "high"}}
    absorb.apply_edits(cid, [edit], sid)
    assert provenance.read(cid)
    undo.undo(cid, "j1")
    assert provenance.read(cid) == {}


def test_undo_leaves_another_records_marker_alone(cid, sid):
    from grimoire.store import provenance
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    entities.create_entity(croot, "lore", "Tithe", body="old tithe")
    other = {**_cited_lore_edit("old tithe", "new tithe"),
             "id": "lore:tithe", "target": {"kind": "lore", "id": "tithe"},
             "label": "The Tithe — lore"}
    absorb.apply_edits(cid, [_cited_lore_edit("old body", "new body"), other], sid)
    undo.undo(cid, "j1")
    assert set(provenance.read(cid)) == {"lore/tithe#body"}


# --- the one branch where the write moves off the probed record -------------

def test_a_commitment_whose_id_was_reallocated_carries_no_reversal(cid, sid):
    """`materialize._new_commitment_id` moves a row staged as NEW off an id that
    a different, unresolved commitment already holds. The snapshot was taken from
    that other record, so it describes a commitment this edit never touched —
    offering it as the reversal would revert somebody else's promise."""
    from grimoire.store.absorb import conflicts
    commitments.set_movement(cid, "pay-mara", "Pay Mara", "promise", "open", None,
                             "He promised.", "s0")
    # Staged as NEW (`before` empty) against an id the slug collided with, and
    # answered over the conflict that collision raises -- the shape that reaches
    # the reallocation.
    edit = {"id": "com", "kind": "commitment",
            "target": {"kind": "commitments", "id": "pay-mara"},
            "label": "Pay, Mara — open", "field": "beat",
            "before": "", "after": "A different promise entirely.",
            "authored": False, "resolve": "replace",
            "resolve_from": conflicts.commitment_line(commitments.get(cid, "pay-mara")),
            "payload": {"id": "pay-mara", "title": "Pay, Mara", "kind": "promise",
                        "status": "open", "due": None, "scene": "s1"}}
    applied, failures = absorb.apply_edits(cid, [edit], sid)
    assert applied and not failures
    entry = _only(cid)
    assert entry["ref"]["id"] == "pay-mara-2"      # the reallocated id
    assert entry["undo"] is None and entry["why"] == undo.GENERIC
    # ...and the record the snapshot came from is untouched by any of it.
    assert len(commitments.get(cid, "pay-mara")["beats"]) == 1
