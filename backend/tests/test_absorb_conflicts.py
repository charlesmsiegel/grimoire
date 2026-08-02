"""Contradiction detection against prior facts (#111).

A StagedEdit is materialized against the campaign copies at absorb time and
written back at save time, and the two moments can be far apart. Everything
here is about that gap: a record that moved in between must be reported, never
overwritten, and the reviewer's keep / replace / merge choice is what unblocks
it.
"""

from __future__ import annotations

from grimoire.store import (absorb, campaigns, characters, dossiers, entities,
                            groupstate, playstate, plot, relationships, scenes,
                            worlds)
from grimoire.store.absorb import conflicts


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch Chronicle", wid)


def _char(root, name):
    card = characters.blank_card(name)
    card["data"]["personality"] = "aloof"
    return characters.create_character(root, name, "main", card)[0]


def _lore_edit(before, after):
    return {"id": "lore:the-pact", "kind": "lore",
            "target": {"kind": "lore", "id": "the-pact"},
            "label": "The Pact — lore", "field": "body",
            "before": before, "after": after, "authored": False}


# --- the guard in apply_edits -------------------------------------------------

def test_a_lore_body_that_moved_since_staging_is_reported_not_overwritten(monkeypatch, tmp_path):
    """The hole this closes: `update_entity` replaces the body wholesale, so an
    append staged against an older body silently discards whatever landed in
    between."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")
    entities.update_entity(croot, "lore", "the-pact",
                           body="Signed at dusk.\n\nWitnessed by the harbour watch.")

    applied, failures = absorb.apply_edits(cid, [
        _lore_edit("Signed at dusk.", "Signed at dusk.\n\nBroken by morning.")])

    assert applied == []
    assert failures == [{"id": "lore:the-pact", "kind": "conflict",
                         "reason": "this entry changed since the scene was absorbed"}]
    assert entities.read_entity(croot, "lore", "the-pact")["body"].strip() == (
        "Signed at dusk.\n\nWitnessed by the harbour watch.")


def test_an_unchanged_target_still_applies(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")

    applied, failures = absorb.apply_edits(cid, [
        _lore_edit("Signed at dusk.", "Signed at dusk.\n\nBroken by morning.")])

    assert applied == ["lore:the-pact"] and failures == []


def test_replace_writes_the_staged_text_over_the_drift(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")
    entities.update_entity(croot, "lore", "the-pact", body="Witnessed by the harbour watch.")

    edit = _lore_edit("Signed at dusk.", "Signed at dusk.\n\nBroken by morning.")
    applied, failures = absorb.apply_edits(cid, [{**edit, "resolve": "replace"}])

    assert applied == ["lore:the-pact"] and failures == []
    assert entities.read_entity(croot, "lore", "the-pact")["body"].strip() == (
        "Signed at dusk.\n\nBroken by morning.")


def test_merge_writes_whatever_the_reviewer_assembled(monkeypatch, tmp_path):
    """`merge` is `replace` plus a reviewer-edited `after`; the store's job is
    only to honour the authorization, not to re-derive the text."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")
    entities.update_entity(croot, "lore", "the-pact",
                           body="Signed at dusk.\n\nWitnessed by the harbour watch.")

    merged = "Signed at dusk.\n\nWitnessed by the harbour watch.\n\nBroken by morning."
    applied, _ = absorb.apply_edits(cid, [
        {**_lore_edit("Signed at dusk.", "Signed at dusk.\n\nBroken by morning."),
         "resolve": "merge", "after": merged}])

    assert applied == ["lore:the-pact"]
    assert entities.read_entity(croot, "lore", "the-pact")["body"].strip() == merged


def test_an_unrecognised_resolve_value_does_not_authorize_a_write(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Moved on.")

    applied, failures = absorb.apply_edits(cid, [
        {**_lore_edit("Signed at dusk.", "Broken by morning."), "resolve": "yes please"}])

    assert applied == [] and [f["kind"] for f in failures] == ["conflict"]


def test_an_edit_with_no_staged_before_is_not_judged(monkeypatch, tmp_path):
    """No `before` key means no basis to compare against, so no drift can be
    shown. Every edit `materialize` emits carries one; a hand-written batch
    that omits it opts out."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")

    applied, _ = absorb.apply_edits(cid, [
        {"id": "lore:the-pact", "kind": "lore", "target": {"kind": "lore", "id": "the-pact"},
         "field": "body", "after": "Broken by morning."}])

    assert applied == ["lore:the-pact"]


def test_an_empty_before_is_a_real_basis(monkeypatch, tmp_path):
    """"Nothing was stored" is a claim about the record, unlike an absent key —
    a state written in between contradicts it."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    playstate.write_state(croot, ch, "Already shaken.")

    applied, failures = absorb.apply_edits(cid, [
        {"id": f"character_state:{ch}", "kind": "character_state",
         "target": {"kind": "characters", "id": ch}, "field": "current_state",
         "before": "", "after": "Loyal now."}])

    assert applied == [] and [f["kind"] for f in failures] == ["conflict"]
    assert playstate.read_state(croot, ch)["current_state"] == "Already shaken."


def test_a_conflicted_edit_does_not_block_the_rest_of_the_batch(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Moved on.")
    entities.create_entity(croot, "lore", "The Ledger", body="Kept dry.")

    applied, failures = absorb.apply_edits(cid, [
        _lore_edit("Signed at dusk.", "Broken by morning."),
        {"id": "lore:the-ledger", "kind": "lore",
         "target": {"kind": "lore", "id": "the-ledger"}, "field": "body",
         "before": "Kept dry.", "after": "Kept dry.\n\nSoaked."}])

    assert applied == ["lore:the-ledger"]
    assert [f["id"] for f in failures] == ["lore:the-pact"]


def test_the_batch_is_judged_before_it_writes_over_itself(monkeypatch, tmp_path):
    """Two narrated axes for one location: applying the first writes an override
    that changes what the second's `before` recomputes to. Checking the whole
    batch up front is what keeps the second from contradicting the first."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    sid = scenes.set_datetime(cid, sid, "2026-06-14T09:00").get("id", sid)
    rows = [r for r in absorb.materialize(
        cid, sid, {"weather_edits": [{"condition": "blizzard", "wind": "gale"}]})
        if r["kind"] == "weather"]
    assert len(rows) == 2   # the fixture only bites with more than one axis

    applied, failures = absorb.apply_edits(cid, rows, sid=sid)

    assert failures == [] and len(applied) == 2


def test_a_target_that_will_not_read_is_left_to_the_existing_skip(monkeypatch, tmp_path):
    """An unreadable target proves no contradiction. `apply_edits` has always
    tolerated a missing entity by skipping it, and that stays a skip rather
    than becoming a conflict the reviewer has to resolve."""
    cid = _campaign(monkeypatch, tmp_path)

    applied, failures = absorb.apply_edits(cid, [
        _lore_edit("Signed at dusk.", "Broken by morning.")])

    assert applied == [] and failures == []


def test_a_dossier_keeps_its_own_guard_and_its_own_wording(monkeypatch, tmp_path):
    """`dossier` is deliberately outside `conflicts` — its check lives with the
    existence check and I/O contract it shares a handler with (#235)."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Seraphine rides with the party.")

    applied, failures = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "Seraphine is wary.", "after": "Seraphine is loyal."}])

    assert applied == []
    assert failures == [{"id": f"dossier:{ch}", "kind": "conflict",
                         "reason": "this dossier changed since the scene was absorbed"}]


# --- every judged kind --------------------------------------------------------

def test_character_state_drift_is_caught(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    playstate.write_state(croot, ch, "Wary.")
    edit = {"id": f"character_state:{ch}", "kind": "character_state",
            "target": {"kind": "characters", "id": ch}, "field": "current_state",
            "before": "Wary.", "after": "Loyal now."}
    assert conflicts.conflict_row(cid, edit) is None

    playstate.write_state(croot, ch, "Bleeding out.")

    row = conflicts.conflict_row(cid, edit)
    assert row["stored"] == "Bleeding out."
    assert row["reason"] == "this character's state changed since the scene was absorbed"


def test_group_state_drift_is_caught(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    gid = entities.create_entity(croot, "groups", "The Harbour Watch")
    groupstate.write_state(croot, gid, groupstate.compose_body({"goals": "Hold the pier."}))
    edit = {"id": f"group_state:{gid}", "kind": "group_state",
            "target": {"kind": "groups", "id": gid}, "field": "group_state",
            "before": "Hold the pier.", "after": "Abandon the pier."}
    assert conflicts.conflict_row(cid, edit) is None

    groupstate.write_state(croot, gid, groupstate.compose_body({"goals": "Burn the pier."}))

    assert conflicts.conflict_row(cid, edit)["stored"] == "Burn the pier."


def test_authored_card_field_drift_is_caught(monkeypatch, tmp_path):
    from grimoire.store import appearances
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "Arrival")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    edit = {"id": f"authored:{ch}:personality", "kind": "authored",
            "target": {"kind": "characters", "id": ch}, "field": "personality",
            "before": "aloof", "after": "warmer"}
    assert conflicts.conflict_row(cid, edit) is None

    card = characters.read_card(croot, ch, "main")
    card["data"]["personality"] = "hand-edited since"
    characters.update_version(croot, ch, "main", card)

    row = conflicts.conflict_row(cid, edit)
    assert row["stored"] == "hand-edited since"
    assert row["reason"] == "this card field changed since the scene was absorbed"


def test_relationship_and_bond_drift_are_caught(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "warm")
    relationships.set_bond(cid, "characters:a", "characters:b", "allies")
    feeling = {"id": "feeling:a->b", "kind": "relationship",
               "target": {"kind": "relationships", "id": "a->b"}, "field": "feeling",
               "before": "trust 4, affection 3, tension 1 (warm)", "after": "…",
               "payload": {"from": "characters:a", "to": "characters:b"}}
    bond = {"id": "bond:a|b", "kind": "bond",
            "target": {"kind": "relationships", "id": "a|b"}, "field": "bond",
            "before": "allies", "after": "rivals",
            "payload": {"a": "characters:a", "b": "characters:b"}}
    assert conflicts.check_conflicts(cid, [feeling, bond]) == []

    relationships.set_feeling(cid, "characters:a", "characters:b", 0, 0, 5, "soured")
    relationships.set_bond(cid, "characters:a", "characters:b", "enemies")

    rows = conflicts.check_conflicts(cid, [feeling, bond])
    assert [r["id"] for r in rows] == ["feeling:a->b", "bond:a|b"]
    assert rows[0]["stored"] == "trust 0, affection 0, tension 5 (soured)"
    assert rows[1]["stored"] == "enemies"


def test_plot_drift_is_caught_and_shares_its_rendering_with_materialize(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Arrival")
    plot.set_movement(cid, "the-map", "The map", "open", "Found in the bilge.", "s1")
    staged = [e for e in absorb.materialize(
        cid, sid, {"plot_movements": [{"id": "the-map", "beat": "It is a forgery."}]})
        if e["kind"] == "plot"]
    assert conflicts.check_conflicts(cid, staged) == []

    plot.set_movement(cid, "the-map", "The map", "closed", "Burned by the watch.", "s2")

    row = conflicts.check_conflicts(cid, staged)[0]
    assert row["stored"] == "closed — Burned by the watch."
    assert row["reason"] == "this plot thread changed since the scene was absorbed"


def test_weather_drift_is_caught(monkeypatch, tmp_path):
    from grimoire.store import weather
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
    sid = scenes.create_scene(cid, "Arrival")
    scenes.set_location(cid, sid, lid)
    sid = scenes.set_datetime(cid, sid, "2026-06-14T09:00").get("id", sid)
    staged = [r for r in absorb.materialize(
        cid, sid, {"weather_edits": [{"condition": "blizzard"}]}) if r["kind"] == "weather"]
    assert conflicts.check_conflicts(cid, staged) == []

    absorb.apply_edits(cid, staged, sid=sid)   # a first save landed the override

    row = conflicts.check_conflicts(cid, staged)[0]
    assert row["stored"] == "blizzard"
    assert weather.resolve(cid, lid, "2026-06-14T09:00")["condition"] == "blizzard"


def test_a_new_record_row_is_never_judged(monkeypatch, tmp_path):
    """`new_*` rows have no target yet, so there is nothing that could have
    drifted — and a blank `before` must not read as one."""
    cid = _campaign(monkeypatch, tmp_path)
    row = {"id": "new_lore:the-tide-charter", "kind": "new_lore",
           "target": {"kind": "lore", "id": ""}, "field": "body",
           "before": "", "after": "Filed under salvage.",
           "payload": {"name": "The Tide Charter"}}
    assert conflicts.check_conflicts(cid, [row]) == []


# --- what materialize stages and what the check recomputes must agree ---------

def test_every_staged_before_equals_what_the_check_reads_back(monkeypatch, tmp_path):
    """The two halves are separate implementations of "what does the record say
    now", and a disagreement between them would flag an untouched store as
    conflicted. This is the test that holds them together."""
    from grimoire.store import appearances
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    playstate.write_state(croot, ch, "Wary.")
    gid = entities.create_entity(croot, "groups", "The Harbour Watch")
    groupstate.write_state(croot, gid, groupstate.compose_body({"goals": "Hold the pier."}))
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")
    plot.set_movement(cid, "the-map", "The map", "open", "Found in the bilge.", "s0")
    relationships.set_feeling(cid, f"characters:{ch}", "characters:mara", 1, 1, 1, "wary")
    relationships.set_bond(cid, f"characters:{ch}", "characters:mara", "allies")
    mara = _char(croot, "Mara")
    sid = scenes.create_scene(cid, "Arrival")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")

    staged = absorb.materialize(cid, sid, {
        "character_state_edits": [{"id": ch, "current_state": "Loyal now."}],
        "group_state_edits": [{"id": gid, "goals": "Abandon the pier."}],
        "lore_edits": [{"id": "the-pact", "append": "Broken by morning."}],
        "authored_edits": [{"id": ch, "field": "personality", "text": "warmer"}],
        "relationship_deltas": [{"from": f"characters:{ch}", "to": f"characters:{mara}",
                                 "trust": 3, "affection": 2, "tension": 0}],
        "bond_changes": [{"a": f"characters:{ch}", "b": f"characters:{mara}", "type": "rivals"}],
        "plot_movements": [{"id": "the-map", "beat": "It is a forgery."}],
    })
    kinds = {e["kind"] for e in staged}
    assert kinds == {"character_state", "group_state", "lore", "authored",
                     "relationship", "bond", "plot"}

    for e in staged:
        assert conflicts.current_value(cid, e) == e["before"], e["id"]
    assert conflicts.check_conflicts(cid, staged) == []


# --- the merge prefill --------------------------------------------------------

def test_merge_text_carries_only_what_the_proposal_added():
    merged = conflicts.merge_text(
        before="Signed at dusk.",
        after="Signed at dusk.\n\nBroken by morning.",
        stored="Signed at dusk.\n\nWitnessed by the harbour watch.")
    assert merged == ("Signed at dusk.\n\nWitnessed by the harbour watch.\n\n"
                      "Broken by morning.")


def test_merge_text_does_not_repeat_a_line_the_stored_text_already_has():
    merged = conflicts.merge_text(
        before="Signed at dusk.",
        after="Signed at dusk.\nBroken by morning.",
        stored="Signed at dusk.\nBroken by morning.\nRewritten by hand.")
    assert merged == "Signed at dusk.\nBroken by morning.\nRewritten by hand."


def test_merge_text_leaves_the_stored_text_alone_when_the_proposal_only_deleted():
    assert conflicts.merge_text("Signed at dusk.\nBroken by morning.", "Signed at dusk.",
                                "Rewritten by hand.") == "Rewritten by hand."


def test_a_merged_draft_is_offered_only_where_it_is_the_field_s_own_text(monkeypatch, tmp_path):
    """A plot row's `before` is "status — last beat", not the beat field, so
    prefilling the beat textarea with it would write the rendering back."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Moved on.")
    plot.set_movement(cid, "the-map", "The map", "closed", "Burned by the watch.", "s2")

    rows = conflicts.check_conflicts(cid, [
        _lore_edit("Signed at dusk.", "Signed at dusk.\n\nBroken by morning."),
        {"id": "plot:the-map", "kind": "plot", "target": {"kind": "plot", "id": "the-map"},
         "field": "beat", "before": "open — Found in the bilge.",
         "after": "It is a forgery.",
         "payload": {"id": "the-map", "title": "The map", "status": "open", "scene": "s3"}}])

    lore_row, plot_row = rows
    assert lore_row["mergeable"] is True
    assert lore_row["merged"] == "Moved on.\n\nBroken by morning."
    assert plot_row["mergeable"] is False
    assert plot_row["merged"] == plot_row["after"]   # nothing to merge into


def test_a_resolved_row_is_no_longer_reported(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Moved on.")
    edit = _lore_edit("Signed at dusk.", "Broken by morning.")

    assert len(conflicts.check_conflicts(cid, [edit])) == 1
    for answer in ("replace", "merge"):
        assert conflicts.check_conflicts(
            cid, [{**edit, "resolve": answer, "resolve_from": "Moved on."}]) == []


# --- answering a conflict is not standing permission --------------------------

def test_a_target_that_moves_again_after_the_answer_is_reported_again(monkeypatch, tmp_path):
    """`resolve` authorizes overwriting the value the reviewer was SHOWN. If the
    record moves again between the refusal and the retry, honouring the flag
    alone would overwrite something nobody ever saw — the lost update this whole
    module exists to stop, recreated one step later."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Witnessed by the watch.")
    answered = {**_lore_edit("Signed at dusk.", "Broken by morning."),
                "resolve": "replace", "resolve_from": "Witnessed by the watch."}
    assert conflicts.check_conflicts(cid, [answered]) == []   # still what they saw

    entities.update_entity(croot, "lore", "the-pact", body="Rewritten by hand.")

    row = conflicts.check_conflicts(cid, [answered])[0]
    assert row["stored"] == "Rewritten by hand."
    assert "changed again after you answered" in row["reason"]
    applied, failures = absorb.apply_edits(cid, [answered])
    assert applied == [] and [f["kind"] for f in failures] == ["conflict"]
    assert entities.read_entity(croot, "lore", "the-pact")["body"].strip() == "Rewritten by hand."


def test_answering_the_second_conflict_lets_the_save_through(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Rewritten by hand.")
    answered = {**_lore_edit("Signed at dusk.", "Broken by morning."),
                "resolve": "replace", "resolve_from": "Rewritten by hand."}

    applied, _ = absorb.apply_edits(cid, [answered])

    assert applied == ["lore:the-pact"]
    assert entities.read_entity(croot, "lore", "the-pact")["body"].strip() == "Broken by morning."


def test_a_resolution_with_no_snapshot_keeps_its_unconditional_meaning(monkeypatch, tmp_path):
    """Backward compatibility for a client that sends `resolve` alone: the flag
    still authorizes the write, it just cannot be held to a value it never
    recorded."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Rewritten by hand.")

    applied, _ = absorb.apply_edits(cid, [
        {**_lore_edit("Signed at dusk.", "Broken by morning."), "resolve": "replace"}])

    assert applied == ["lore:the-pact"]


def test_the_merged_draft_of_a_recheck_diffs_from_what_the_reviewer_saw(monkeypatch, tmp_path):
    """On a recheck the basis is `resolve_from`, so the draft carries only the
    reviewer's own edit forward — not the text they had already merged in."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Rewritten by hand.")

    row = conflicts.check_conflicts(cid, [
        {**_lore_edit("Signed at dusk.", "Witnessed by the watch.\nBroken by morning."),
         "resolve": "merge", "resolve_from": "Witnessed by the watch."}])[0]

    assert row["merged"] == "Rewritten by hand.\n\nBroken by morning."


# --- malformed client input stays best-effort ---------------------------------

def test_a_malformed_row_never_escapes_as_an_exception(monkeypatch, tmp_path):
    """`edits` arrives straight off a PUT body that is validated only as "a list
    of dicts". Every one of these raises rather than returning False/None if the
    read is not guarded, and one bad row must not 500 the whole save — the
    best-effort contract `apply_edits` has always had for a broken target."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Signed at dusk.")
    malformed = [
        {"id": "a", "kind": "lore", "target": "not-a-dict", "before": "x", "after": "y"},
        {"id": "b", "kind": "lore", "target": ["also", "not"], "before": "x", "after": "y"},
        {"id": "c", "kind": ["unhashable"], "target": {}, "before": "x", "after": "y"},
        {"id": "d", "kind": "relationship", "payload": "not-a-dict",
         "before": "x", "after": "y"},
        {"id": "e", "kind": "lore", "target": {"kind": "lore", "id": "the-pact"},
         "before": "x", "after": "y", "resolve": ["unhashable"]},
        "not a dict at all",
    ]

    # Only "e" is judged at all: its target reads, and its garbled `resolve`
    # authorizes nothing, so it is a plain conflict against a moved entry.
    assert [r["id"] for r in conflicts.check_conflicts(cid, malformed)] == ["e"]
    applied, failures = absorb.apply_edits(cid, malformed)
    assert applied == []
    assert [f["id"] for f in failures] == ["e"]   # the rest: skipped, best-effort
    assert entities.read_entity(croot, "lore", "the-pact")["body"].strip() == "Signed at dusk."


def test_two_rows_sharing_an_edit_id_are_judged_separately(monkeypatch, tmp_path):
    """`materialize` dedupes only plot threads, so two lore proposals naming one
    entry really can share an id. The verdicts are positional for that reason."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "The Pact", body="Moved on.")
    stale = _lore_edit("Signed at dusk.", "Broken by morning.")
    answered = {**stale, "resolve": "replace", "resolve_from": "Moved on."}

    assert conflicts.batch_verdicts(cid, [answered, stale]) == [
        None, conflicts.conflict_row(cid, stale)]
