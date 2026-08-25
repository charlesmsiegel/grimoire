"""The append-only relationship timeline (#63)."""

import json

from grimoire.store import (
    absorb,
    campaigns,
    journal,
    relationship_history,
    relationships,
    scene_refs,
    scenes,
    undo,
    worlds,
)

A, B = "characters:mara", "pcs:seraphine"
C = "characters:winifred"


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def _feeling(cid, frm, to, trust, affection, tension, note=""):
    """One staged feeling edit, with the standing it replaces read off the store
    the way `materialize` reads it — that `before` IS the driving evidence this
    timeline exists to keep, so a helper that invented it would be testing
    itself."""
    cur = relationships.get_feeling(cid, frm, to)
    payload = {"from": frm, "to": to, "trust": trust, "affection": affection,
               "tension": tension, "note": note}
    return {"id": f"feeling:{frm}->{to}", "kind": "relationship",
            "target": {"kind": "relationships", "id": f"{frm}->{to}"},
            "field": "feeling", "label": f"{frm} → {to}",
            "before": relationships._render_feeling(cur) if cur else "",
            "after": relationships._render_feeling(payload), "payload": payload}


def _bond(cid, a, b, type_):
    cur = relationships.get_bond(cid, a, b)
    return {"id": f"bond:{a}|{b}", "kind": "bond",
            "target": {"kind": "relationships", "id": f"{a}|{b}"},
            "field": "bond", "label": f"{a} & {b}",
            "before": cur["type"] if cur else "", "after": type_,
            "payload": {"a": a, "b": b, "type": type_}}


# ---- the store itself ------------------------------------------------------

def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert relationship_history.read(cid) == []


def test_append_stamps_ids_and_returns_rows(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    written = relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="Mara → Seraphine",
                                 before="", after="trust 2, affection 1, tension 3",
                                 scene="001--the-crypt")])
    assert [e["id"] for e in written] == ["rh1"]
    assert written[0]["ts"] and written[0]["source"] == "absorb"
    stored = relationship_history.read(cid)
    assert stored == written
    assert stored[0]["a"] == A and stored[0]["b"] == B


def test_append_of_nothing_writes_no_file(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert relationship_history.append(cid, []) == []
    assert not (campaigns.campaign_root(cid) / "relationship_history.json").exists()


def test_ids_never_repeat_across_appends(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    for n in range(3):
        relationship_history.append(cid, [
            relationship_history.row("feeling", A, B, label="", before="", after=str(n))])
    assert [e["id"] for e in relationship_history.read(cid)] == ["rh1", "rh2", "rh3"]


def test_seq_survives_a_hand_trimmed_file(monkeypatch, tmp_path):
    """A hand-edited `seq` must not hand out an id already in use — the one
    thing a reader citing a row depends on."""
    cid = _campaign(monkeypatch, tmp_path)
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after="one")])
    p = campaigns.campaign_root(cid) / "relationship_history.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["seq"] = 0
    p.write_text(json.dumps(doc), encoding="utf-8")
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after="two")])
    assert [e["id"] for e in relationship_history.read(cid)] == ["rh1", "rh2"]


def test_garbled_file_costs_the_timeline_not_the_page(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "relationship_history.json").write_text(
        "{not json", encoding="utf-8")
    assert relationship_history.read(cid) == []


def test_for_pair_matches_unordered_and_both_directions(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after="a→b"),
        relationship_history.row("feeling", B, A, label="", before="", after="b→a"),
        relationship_history.row("bond", A, B, label="", before="", after="allies"),
        relationship_history.row("feeling", A, C, label="", before="", after="a→c"),
    ])
    assert [e["after"] for e in relationship_history.for_pair(cid, B, A)] == [
        "a→b", "b→a", "allies"]
    assert [e["after"] for e in relationship_history.for_pair(cid, A, C)] == ["a→c"]
    assert relationship_history.for_pair(cid, B, C) == []


def test_retention_drops_the_oldest_and_never_the_present(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(relationship_history, "RETENTION", 3)
    for n in range(5):
        relationship_history.append(cid, [
            relationship_history.row("feeling", A, B, label="", before="", after=str(n))])
    assert [e["after"] for e in relationship_history.read(cid)] == ["2", "3", "4"]
    # One append larger than the cap keeps every row it just wrote: the caps
    # bound accumulation, not the present.
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after=f"burst{n}")
        for n in range(5)])
    assert [e["after"] for e in relationship_history.read(cid)] == [f"burst{n}" for n in range(5)]


def test_byte_cap_binds_on_a_long_note(monkeypatch, tmp_path):
    """The row cap alone bounds nothing: a feeling's `note` is model-authored
    and uncapped upstream."""
    cid = _campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(relationship_history, "MAX_BYTES", 4000)
    for n in range(6):
        relationship_history.append(cid, [
            relationship_history.row("feeling", A, B, label="", before="",
                                     after=f"{n}:" + "x" * 1000)])
    kept = relationship_history.read(cid)
    assert len(kept) < 6 and kept[-1]["after"].startswith("5:")


def test_repoint_follows_a_renamed_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after="x",
                                 scene="001--s"),
        relationship_history.row("bond", A, B, label="", before="", after="allies",
                                 scene="other")])
    scene_refs.repoint(cid, {"001--s": "001--2026-07-04--s"})
    kept = relationship_history.read(cid)
    assert kept[0]["scene"] == "001--2026-07-04--s"
    assert kept[1]["scene"] == "other"   # untouched


def test_repoint_creates_no_file_for_a_campaign_with_no_history(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    scene_refs.repoint(cid, {"a": "b"})
    assert not (campaigns.campaign_root(cid) / "relationship_history.json").exists()


# ---- what absorb writes ----------------------------------------------------

def test_absorb_records_one_entry_per_applied_delta(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "The crypt")
    applied, failures = absorb.apply_edits(
        cid, [_feeling(cid, A, B, 2, 1, 3, "wary"), _bond(cid, A, B, "allies")], sid=sid)
    assert len(applied) == 2 and failures == []
    rows = relationship_history.read(cid)
    assert [(r["kind"], r["a"], r["b"], r["scene"], r["source"]) for r in rows] == [
        ("feeling", A, B, sid, "absorb"), ("bond", A, B, sid, "absorb")]
    assert rows[0]["before"] == "" and rows[0]["after"].startswith("trust 2")
    assert rows[1]["after"] == "allies"


def test_a_second_scene_appends_rather_than_overwriting(monkeypatch, tmp_path):
    """The whole point: `relationships.json` keeps only the latest standing, and
    a rolling log would have thrown the first delta away."""
    cid = _campaign(monkeypatch, tmp_path)
    first = scenes.create_scene(cid, "The crypt")
    second = scenes.create_scene(cid, "The pier")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=first)
    absorb.apply_edits(cid, [_feeling(cid, A, B, 4, 3, 1, "warmer")], sid=second)
    rows = relationship_history.for_pair(cid, A, B)
    assert [r["scene"] for r in rows] == [first, second]
    assert rows[1]["before"] == "trust 2, affection 1, tension 3 (wary)"
    assert rows[1]["after"] == "trust 4, affection 3, tension 1 (warmer)"
    # And the current-value store still holds only the latest.
    assert relationships.get_feeling(cid, A, B)["trust"] == 4


def test_a_non_relationship_edit_records_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [
        {"id": "plot:the-map", "kind": "plot",
         "target": {"kind": "plot", "id": "the-map"}, "field": "beat",
         "after": "It is a forgery.",
         "payload": {"id": "the-map", "title": "The map", "status": "advanced",
                     "scene": sid}}], sid=sid)
    assert relationship_history.read(cid) == []


def test_a_resumed_commit_does_not_append_twice(monkeypatch, tmp_path):
    """The timeline is append-only, so replaying it would DUPLICATE the arc.
    It rides the same guarded block the change journal does."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    progress: dict = {}
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3)], sid=sid, progress=progress)
    assert len(relationship_history.read(cid)) == 1
    applied, failures = absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3)], sid=sid,
                                           progress=progress)
    assert len(relationship_history.read(cid)) == 1
    assert applied == ["feeling:characters:mara->pcs:seraphine"]
    # The same answer the change journal gives a resume that cannot tell whether
    # the log block ran: reported, rather than replayed into a duplicate row.
    assert [(f["id"], f["reason"]) for f in failures] == [("changes", absorb.UNCONFIRMED)]


# ---- what an undo writes ---------------------------------------------------

def test_undoing_a_feeling_appends_the_reversal(monkeypatch, tmp_path):
    """Appended, never deleted: a reversal happened, and an arc that quietly
    lost its last row is one nobody can reconcile against relationships.json."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=sid)
    absorb.apply_edits(cid, [_feeling(cid, A, B, 4, 3, 1, "warmer")], sid=sid)
    latest = journal.read(cid)[-1]
    undo.undo(cid, latest["id"])
    rows = relationship_history.for_pair(cid, A, B)
    assert len(rows) == 3
    assert rows[2]["source"] == "undo"
    assert rows[2]["before"] == "trust 4, affection 3, tension 1 (warmer)"
    assert rows[2]["after"] == "trust 2, affection 1, tension 3 (wary)"
    assert relationships.get_feeling(cid, A, B)["trust"] == 2


def test_undoing_a_bond_appends_the_reversal(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [_bond(cid, A, B, "allies")], sid=sid)
    undo.undo(cid, journal.read(cid)[-1]["id"])
    rows = relationship_history.for_pair(cid, A, B)
    assert [(r["source"], r["after"]) for r in rows] == [("absorb", "allies"), ("undo", "")]
    assert relationships.get_bond(cid, A, B) is None


def test_undoing_something_else_leaves_the_timeline_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [
        {"id": "plot:the-map", "kind": "plot",
         "target": {"kind": "plot", "id": "the-map"}, "field": "beat",
         "after": "It is a forgery.",
         "payload": {"id": "the-map", "title": "The map", "status": "advanced",
                     "scene": sid}}], sid=sid)
    undo.undo(cid, journal.read(cid)[-1]["id"])
    assert relationship_history.read(cid) == []


# ---- end to end, through the real staging path -----------------------------

def _char(root, name):
    from grimoire.store import characters
    return characters.create_character(root, name, "main", characters.blank_card(name))[0]


def test_the_arc_of_two_scenes_reads_as_a_timeline(monkeypatch, tmp_path):
    """Materialize and apply twice, as an absorb does. The second scene's
    `before` is the first scene's `after` because `materialize` read it off
    `relationships.json` — which is what makes the row evidence rather than a
    restatement of the write."""
    from grimoire.store import appearances
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    mara, winifred = _char(croot, "Mara"), _char(croot, "Winifred")
    atok, btok = f"characters:{mara}", f"characters:{winifred}"
    first = scenes.create_scene(cid, "The crypt")
    second = scenes.create_scene(cid, "The pier")
    for sid in (first, second):
        appearances.appear(cid, sid, "characters", mara, "main", "npc")
        appearances.appear(cid, sid, "characters", winifred, "main", "npc")

    def absorb_scene(sid, trust, affection, tension, note, bond):
        parsed = {"relationship_deltas": [{"from": atok, "to": btok, "trust": trust,
                                           "affection": affection, "tension": tension,
                                           "note": note}],
                  "bond_changes": [{"a": atok, "b": btok, "type": bond}]}
        return absorb.apply_edits(cid, absorb.materialize(cid, sid, parsed), sid=sid)

    absorb_scene(first, 1, 1, 4, "wary", "wary allies")
    absorb_scene(second, 4, 3, 1, "warm", "sworn")

    rows = relationship_history.for_pair(cid, btok, atok)
    assert [(r["kind"], r["scene"]) for r in rows] == [
        ("feeling", first), ("bond", first), ("feeling", second), ("bond", second)]
    assert rows[0]["before"] == "" and rows[0]["after"] == "trust 1, affection 1, tension 4 (wary)"
    assert rows[2]["before"] == "trust 1, affection 1, tension 4 (wary)"
    assert rows[2]["after"] == "trust 4, affection 3, tension 1 (warm)"
    assert (rows[1]["before"], rows[1]["after"]) == ("", "wary allies")
    assert (rows[3]["before"], rows[3]["after"]) == ("wary allies", "sworn")
    assert rows[2]["label"] == "Mara → Winifred" and rows[3]["label"] == "Mara & Winifred"
    # The current-value store kept only the far end of that arc.
    assert relationships.get_feeling(cid, atok, btok)["trust"] == 4
    assert relationships.get_bond(cid, atok, btok)["type"] == "sworn"


def test_a_rename_keeps_the_timeline_pointing_at_the_scene(monkeypatch, tmp_path):
    """A scene id is a filename stem, so a rename that this store did not follow
    would leave every row citing a scene that is not there."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "The crypt")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=sid)
    renamed = scenes.rename_scene(cid, sid, "The ossuary")
    assert renamed != sid
    assert [r["scene"] for r in relationship_history.read(cid)] == [renamed]


def test_a_cut_scene_leaves_its_deltas_and_their_reversals(monkeypatch, tmp_path):
    """`cascade.revert_scene` puts every write-back back through `undo.undo`, so
    a cut appends reversals rather than erasing rows. That is the opposite of
    what `changes.forget_scene` does, and deliberately: this log is append-only,
    and the rows before the cut are still true about what happened."""
    from grimoire.store import cascade
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "The crypt")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=sid)
    absorb.apply_edits(cid, [_bond(cid, A, B, "allies")], sid=sid)

    report = cascade.revert_scene(cid, sid)
    assert report["records"] == 2 and report["refused"] == []
    rows = relationship_history.for_pair(cid, A, B)
    assert [(r["kind"], r["source"], r["after"]) for r in rows] == [
        ("feeling", "absorb", "trust 2, affection 1, tension 3 (wary)"),
        ("bond", "absorb", "allies"),
        ("bond", "undo", ""),
        ("feeling", "undo", "")]
    assert relationships.get_feeling(cid, A, B) is None


def test_for_pair_skips_a_garbled_row_rather_than_raising(monkeypatch, tmp_path):
    """An unhashable token where a string belongs would turn one hand-edited row
    into a 500 for the whole request."""
    cid = _campaign(monkeypatch, tmp_path)
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after="real")])
    p = campaigns.campaign_root(cid) / "relationship_history.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["entries"].append({"id": "rh2", "kind": "feeling", "a": {"oops": 1}, "b": B,
                           "before": "", "after": "garbled", "scene": "", "source": "absorb"})
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert [e["after"] for e in relationship_history.for_pair(cid, A, B)] == ["real"]


# ---- the row describes the RECORD, not the edit that travelled with it -----

def test_a_forged_after_cannot_make_the_timeline_claim_a_standing(monkeypatch, tmp_path):
    """The write goes from the payload and the row is read back off the record,
    so an `after` string that disagrees with the payload cannot leave an
    append-only row permanently claiming a standing nothing holds."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    forged = _feeling(cid, A, B, 4, 3, 1, "warm")
    forged["after"] = "trust 0, affection 0, tension 5 (nothing like the payload)"
    absorb.apply_edits(cid, [forged], sid=sid)

    row = relationship_history.read(cid)[-1]
    assert row["after"] == "trust 4, affection 3, tension 1 (warm)"
    assert row["after"] == relationships._render_feeling(
        relationships.get_feeling(cid, A, B))


def test_a_stale_before_never_reaches_the_timeline_at_all(monkeypatch, tmp_path):
    """The other end of the row is checked before the write rather than after
    it: `relationship` and `bond` are both in `conflicts._REASONS`, so a row
    whose staged `before` is not the stored standing is refused and no timeline
    row is written — which is why `before` can be the journal's."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 1, 1, 4, "wary")], sid=sid)
    stale = _feeling(cid, A, B, 4, 3, 1, "warm")
    stale["before"] = "trust 5, affection 5, tension 0 (never stored)"
    applied, failures = absorb.apply_edits(cid, [stale], sid=sid)

    assert applied == [] and [f["kind"] for f in failures] == ["conflict"]
    rows = relationship_history.for_pair(cid, A, B)
    assert [r["after"] for r in rows] == ["trust 1, affection 1, tension 4 (wary)"]
    assert relationships.get_feeling(cid, A, B)["trust"] == 1


def test_a_forged_bond_type_is_recorded_as_written(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    forged = _bond(cid, A, B, "allies")
    forged["after"] = "sworn enemies"
    absorb.apply_edits(cid, [forged], sid=sid)
    assert relationship_history.read(cid)[-1]["after"] == "allies"
    assert relationships.get_bond(cid, A, B)["type"] == "allies"


def test_invalid_utf8_costs_the_timeline_rather_than_the_next_append(monkeypatch, tmp_path):
    """A file a sync client mangled raises out of `read_text` before json sees a
    character. Uncaught it would sink every later append from inside the absorb
    block that has already moved `relationships.json`."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "relationship_history.json").write_bytes(
        b'{"seq": 1, "entries": [{"id": "rh1", "after": "\xff\xfe not utf-8"}]}')
    assert relationship_history.read(cid) == []
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3)], sid=sid)
    assert [r["a"] for r in relationship_history.read(cid)] == [A]


def test_a_redo_appends_a_row_that_reads_forwards(monkeypatch, tmp_path):
    """Undoing an undo is a redo. `source` does not claim which of the two a
    reversal was — the direction is the parity of a chain retention can truncate
    — so each row is read from its own two standings, and those are right for
    every step."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=sid)
    undone = undo.undo(cid, journal.read(cid)[-1]["id"])
    undo.undo(cid, undone["id"])   # the redo: it puts the absorbed standing back

    standing = "trust 2, affection 1, tension 3 (wary)"
    assert [(r["source"], r["before"], r["after"])
            for r in relationship_history.for_pair(cid, A, B)] == [
        ("absorb", "", standing), ("undo", standing, ""), ("undo", "", standing)]
    assert relationships.get_feeling(cid, A, B)["trust"] == 2


def test_two_deltas_on_one_pair_in_one_absorb_chain(monkeypatch, tmp_path):
    """Nothing dedupes `relationship_deltas` by pair, and the conflict gate is
    one pass over the whole batch BEFORE the first write — so both rows are
    staged against the untouched record and both pass. The second row's
    `before` has to be what the FIRST one left, or the arc has a break in it."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    first = _feeling(cid, A, B, 1, 1, 4, "wary")
    second = _feeling(cid, A, B, 4, 3, 1, "warm")
    second["before"] = first["before"]     # what materialize stages: both read the
    second["id"] = "feeling:second"        # same record, before either has landed
    applied, failures = absorb.apply_edits(cid, [first, second], sid=sid)

    assert len(applied) == 2 and failures == []
    rows = relationship_history.for_pair(cid, A, B)
    assert [(r["before"], r["after"]) for r in rows] == [
        ("", "trust 1, affection 1, tension 4 (wary)"),
        ("trust 1, affection 1, tension 4 (wary)", "trust 4, affection 3, tension 1 (warm)")]


def test_a_reversal_reads_the_records_not_the_journals_text(monkeypatch, tmp_path):
    """An undo builds its row from the record the compare-and-swap verified and
    the one it restored — never from the journal's display strings, which carry
    whatever `after` a client-supplied edit supplied."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    forged = _feeling(cid, A, B, 2, 1, 3, "wary")
    forged["after"] = "trust 0, affection 0, tension 5 (nothing like the payload)"
    absorb.apply_edits(cid, [forged], sid=sid)
    undo.undo(cid, journal.read(cid)[-1]["id"])

    landed, reversal = relationship_history.for_pair(cid, A, B)
    assert landed["after"] == "trust 2, affection 1, tension 3 (wary)"
    assert reversal["before"] == landed["after"]   # the arc joins up
    assert reversal["after"] == ""                 # there was no prior standing
    assert relationships.get_feeling(cid, A, B) is None


def test_a_deleted_scenes_rows_stay_but_stop_resolving(monkeypatch, tmp_path):
    """Scene ids are recycled, so a retained row must not be joinable: the next
    scene to take the number would lend it a title it never had, and that
    scene's next rename would drag the row along."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "The crypt")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=sid)
    scenes.delete_scene(cid, sid)

    rows = relationship_history.read(cid)
    assert len(rows) == 1                       # kept: the delta still happened
    assert rows[0]["scene"] == sid and rows[0]["scene_gone"] is True
    # And a rename of whatever holds that id now leaves the row alone.
    scene_refs.repoint(cid, {sid: "001--2026-07-04--the-crypt"})
    assert relationship_history.read(cid)[0]["scene"] == sid


def test_forget_scene_is_idempotent_and_leaves_other_scenes_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationship_history.append(cid, [
        relationship_history.row("feeling", A, B, label="", before="", after="x",
                                 scene="001--s"),
        relationship_history.row("bond", A, B, label="", before="", after="allies",
                                 scene="002--t")])
    assert relationship_history.forget_scene(cid, "001--s") == 1
    assert relationship_history.forget_scene(cid, "001--s") == 0   # already marked
    kept = relationship_history.read(cid)
    assert kept[0]["scene_gone"] is True and "scene_gone" not in kept[1]


def test_a_reversal_after_the_scene_was_deleted_is_marked_gone(monkeypatch, tmp_path):
    """`forget_scene` can only mark what exists when it runs. A journal entry a
    deleted scene left behind stays undoable, so its reversal lands afterwards
    citing an id `scenes.lifecycle` may already have handed on."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "The crypt")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3, "wary")], sid=sid)
    jid = journal.read(cid)[-1]["id"]
    scenes.delete_scene(cid, sid)
    undo.undo(cid, jid)

    landed, reversal = relationship_history.read(cid)
    assert landed["scene_gone"] is True        # marked at delete time
    assert reversal["source"] == "undo" and reversal["scene"] == sid
    assert reversal["scene_gone"] is True      # and this one from the file's absence


def test_a_reversal_inside_a_live_scene_carries_no_gone_flag(monkeypatch, tmp_path):
    """Absent rather than False: a row that carries the key at all is one
    somebody established is unresolvable."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [_feeling(cid, A, B, 2, 1, 3)], sid=sid)
    undo.undo(cid, journal.read(cid)[-1]["id"])
    assert all("scene_gone" not in r for r in relationship_history.read(cid))
