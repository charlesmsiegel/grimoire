"""The dated fact ledger (#114).

What separates this store from the two it sits beside is that a fact can stop
being true without being erased: `chronicle.json` replaces a whole scene record,
`state.md` is a snapshot rewritten on every absorb, and neither can say "the
thing scene 3 recorded stopped being true in scene 9, and here is what replaced
it". Everything below is about that sentence being expressible and durable.
"""

import json

import pytest

from grimoire.store import campaigns, facts, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


def _ledger_path(cid):
    return campaigns.campaign_root(cid) / "facts.json"


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert facts.read(cid) == {}
    assert facts.active(cid) == []
    assert facts.render_active(cid) == []


def test_record_writes_a_standing_fact(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    fid = facts.record(cid, "  The bridge at Saltmarch stands.  ", " the third night ", "s10")
    assert facts.get(cid, fid) == {
        "text": "The bridge at Saltmarch stands.", "date": "the third night",
        "scene": "s10", "status": "active", "superseded_by": "", "retired_scene": ""}
    assert facts.active(cid) == [{"id": fid, "text": "The bridge at Saltmarch stands.",
                                  "date": "the third night", "scene": "s10"}]


def test_record_refuses_a_blank_fact(monkeypatch, tmp_path):
    """The one caller passes text it has already checked; a blank reaching here
    would open an id holding nothing, which no later scene can supersede
    meaningfully and which the snapshot would show the model as an empty line."""
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        facts.record(cid, "   ", "", "s1")


def test_supersession_retires_the_old_fact_and_links_both_ways(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    old = facts.record(cid, "The ambassador trusts the party.", "the first night", "s3")
    new = facts.record(cid, "The ambassador believes the party sold him out.",
                       "the ninth night", "s9", supersedes=old)

    retired = facts.get(cid, old)
    assert retired["status"] == "retired"
    assert retired["superseded_by"] == new
    assert retired["retired_scene"] == "s9"
    # the fact itself is kept, not deleted: the ledger's whole point is that
    # "used to be true" stays answerable
    assert retired["text"] == "The ambassador trusts the party."
    assert retired["date"] == "the first night" and retired["scene"] == "s3"
    assert [f["id"] for f in facts.active(cid)] == [new]


def test_retire_ends_a_fact_with_nothing_in_its_place(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    fid = facts.record(cid, "The east gate is barred at dusk.", "", "s2")
    assert facts.retire(cid, fid, "s7") is True
    rec = facts.get(cid, fid)
    assert rec["status"] == "retired" and rec["superseded_by"] == "" and rec["retired_scene"] == "s7"
    assert facts.active(cid) == []


def test_retiring_twice_is_refused_rather_than_silently_restamped(monkeypatch, tmp_path):
    """False, not True: `apply` turns this into a reported failure, and a second
    retirement that quietly succeeded would move `retired_scene` onto a later
    scene and misdate when the fact actually ended."""
    cid = _campaign(monkeypatch, tmp_path)
    fid = facts.record(cid, "The east gate is barred at dusk.", "", "s2")
    facts.retire(cid, fid, "s7")
    assert facts.retire(cid, fid, "s9") is False
    assert facts.get(cid, fid)["retired_scene"] == "s7"
    assert facts.retire(cid, "never-existed", "s9") is False


def test_superseding_an_already_retired_fact_leaves_its_pointer_alone(monkeypatch, tmp_path):
    """The pointer says what really replaced it. A second supersession re-aiming
    it would lose the only record of that, and would take a live fact's place in
    the chain."""
    cid = _campaign(monkeypatch, tmp_path)
    old = facts.record(cid, "The ambassador trusts the party.", "", "s3")
    first = facts.record(cid, "The ambassador is wary of the party.", "", "s9", supersedes=old)
    second = facts.record(cid, "The ambassador has left the city.", "", "s11", supersedes=old)

    assert facts.get(cid, old)["superseded_by"] == first
    assert facts.get(cid, old)["retired_scene"] == "s9"
    # the second fact still lands — it is a true thing the scene established
    assert {f["id"] for f in facts.active(cid)} == {first, second}


def test_a_supersedes_naming_nothing_still_records_the_fact(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    fid = facts.record(cid, "The tide runs backwards here.", "", "s4", supersedes="f99")
    assert facts.active(cid) == [{"id": fid, "text": "The tide runs backwards here.",
                                 "date": "", "scene": "s4"}]


def test_a_fact_cannot_supersede_itself(monkeypatch, tmp_path):
    """The degenerate case a forged save body can send: honoured, it would retire
    the fact in the very write that recorded it."""
    cid = _campaign(monkeypatch, tmp_path)
    old = facts.record(cid, "The bridge stands.", "", "s1")
    again = facts.record(cid, "The bridge stands.", "", "s1", supersedes=old)
    assert again == old
    assert facts.get(cid, old)["status"] == "active"


def test_re_recording_a_scenes_fact_does_not_open_a_second_one(monkeypatch, tmp_path):
    """Absorbing a scene twice is supported and re-proposes every fact the first
    pass found. That is the `timeline.md` re-append this ledger exists to
    improve on, so the second pass has to land on the record the first made."""
    cid = _campaign(monkeypatch, tmp_path)
    first = facts.record(cid, "The bridge at Saltmarch stands.", "the third night", "s10")
    again = facts.record(cid, "  the BRIDGE at Saltmarch stands.  ", "the third night", "s10")
    assert again == first
    assert len(facts.read(cid)) == 1


def test_the_same_fact_from_a_different_scene_is_a_different_fact(monkeypatch, tmp_path):
    """The dedup is per scene on purpose: it exists to absorb a re-run, not to
    collapse two scenes that each established the same thing — those have
    different dates, and the ledger is dated."""
    cid = _campaign(monkeypatch, tmp_path)
    first = facts.record(cid, "The bridge stands.", "", "s1")
    second = facts.record(cid, "The bridge stands.", "", "s5")
    assert first != second


def test_re_recording_a_scenes_fact_does_not_resurrect_it_after_a_later_retirement(
        monkeypatch, tmp_path):
    """The dedup ignores `status`, and matching only ACTIVE records was a bug.
    Scene s1 records a fact, scene s4 ends it, and s1 is then re-absorbed with
    `force` — an active-only lookup cannot see the retired record, so it files
    the same sentence as a fresh standing fact and puts a truth s4 explicitly
    ended back on the ledger. The SCENE is what separates a re-extraction from a
    re-establishment; status has no work to do here."""
    cid = _campaign(monkeypatch, tmp_path)
    first = facts.record(cid, "The bridge stands.", "", "s1")
    facts.retire(cid, first, "s4")
    again = facts.record(cid, "The bridge stands.", "", "s1")
    assert again == first
    assert facts.get(cid, first)["status"] == "retired"    # s4's retirement stands
    assert facts.active(cid) == []


def test_a_later_scene_re_establishing_a_retired_fact_gets_its_own_record(
        monkeypatch, tmp_path):
    """The other half of that rule: a fact that stopped being true and then
    became true again is genuinely new, and it is the scene id — not the status
    — that says so. It is dated to when it became true the second time."""
    cid = _campaign(monkeypatch, tmp_path)
    first = facts.record(cid, "The bridge stands.", "the first night", "s1")
    facts.retire(cid, first, "s4")
    again = facts.record(cid, "The bridge stands.", "the ninth night", "s9")
    assert again != first
    assert facts.active(cid) == [{"id": again, "text": "The bridge stands.",
                                  "date": "the ninth night", "scene": "s9"}]


def test_a_supersession_landing_on_a_retired_dedup_hit_makes_a_chain(monkeypatch, tmp_path):
    """Not a special case: the predecessor is retired pointing at the record
    this returned, which is itself retired pointing at what replaced it."""
    cid = _campaign(monkeypatch, tmp_path)
    f1 = facts.record(cid, "The bridge stands.", "", "s1")
    f2 = facts.record(cid, "The bridge is rubble.", "", "s2", supersedes=f1)
    facts.retire(cid, f2, "s3")
    # s2 re-absorbed: its fact is retired, and the row still names f1
    again = facts.record(cid, "The bridge is rubble.", "", "s2", supersedes=f1)
    assert again == f2
    assert facts.get(cid, f1)["superseded_by"] == f2
    assert facts.get(cid, f2)["status"] == "retired"
    assert facts.active(cid) == []


def test_restates_is_case_and_whitespace_insensitive(monkeypatch, tmp_path):
    """One definition, because there are two callers: `materialize` drops a
    restatement the model wrote, and `apply` catches one the reviewer typed."""
    rec = {"text": "The ambassador trusts the party."}
    assert facts.restates(rec, "  the AMBASSADOR trusts the party.  ") is True
    assert facts.restates(rec, "The ambassador distrusts the party.") is False
    assert facts.restates({"text": ["not a string"]}, "") is True   # both read as ""
    assert facts.restates(None, "anything") is False


def test_ids_are_never_reused(monkeypatch, tmp_path):
    """`superseded_by` pointers from elsewhere in the ledger would otherwise
    silently re-aim at a different fact."""
    cid = _campaign(monkeypatch, tmp_path)
    ids = [facts.record(cid, f"Fact {n}.", "", "s1") for n in range(3)]
    assert ids == ["f1", "f2", "f3"]
    facts.retire(cid, "f2", "s2")
    assert facts.record(cid, "Another.", "", "s2") == "f4"


def test_ids_step_over_whatever_a_hand_edited_file_already_holds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _ledger_path(cid).write_text(json.dumps({
        "f1": {"text": "One.", "scene": "s1", "status": "active"},
        "f3": {"text": "Three.", "scene": "s1", "status": "active"}}), encoding="utf-8")
    assert facts.record(cid, "Four.", "", "s2") == "f4"


def test_active_orders_by_scene_then_by_counting_order(monkeypatch, tmp_path):
    """`f9` before `f10`: the ids are a counter, and a ledger read out of
    counting order misdates the very thing it is for."""
    cid = _campaign(monkeypatch, tmp_path)
    for n in range(11):
        facts.record(cid, f"Fact {n}.", "", "002--later" if n == 0 else "001--earlier")
    ids = [f["id"] for f in facts.active(cid)]
    assert ids == ["f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f1"]


def test_a_hand_written_status_that_is_not_retired_still_counts_as_standing(monkeypatch, tmp_path):
    """"Not retired" rather than "== active", like plot's "not closed" and
    commitments' "not resolved": a record written by hand or by an older version
    stays on the ledger instead of silently dropping out of it. Retirement is
    case-folded so a hand-edited "Retired" is not read as a live fact."""
    cid = _campaign(monkeypatch, tmp_path)
    _ledger_path(cid).write_text(json.dumps({
        "f1": {"text": "One.", "scene": "s1", "status": "standing"},
        "f2": {"text": "Two.", "scene": "s1"},
        "f3": {"text": "Three.", "scene": "s1", "status": "Retired"},
        "f4": {"text": "Four.", "scene": "s1", "status": ["nonsense"]}}), encoding="utf-8")
    assert [f["id"] for f in facts.active(cid)] == ["f1", "f2", "f4"]


def test_projected_fields_are_coerced_not_trusted(monkeypatch, tmp_path):
    """facts.json is hand-editable and read by a bare json.loads. An object-valued
    `text` reaching the ledger panel is a React child React refuses, which blanks
    the whole view rather than showing one odd row."""
    cid = _campaign(monkeypatch, tmp_path)
    _ledger_path(cid).write_text(json.dumps({
        "f1": {"text": {"oops": 1}, "date": ["nope"], "scene": 7, "status": "active"},
        "f2": "not even a record"}), encoding="utf-8")
    assert facts.active(cid) == [{"id": "f1", "text": "", "date": "", "scene": ""}]


def test_a_ledger_of_the_wrong_shape_is_refused_rather_than_overwritten(monkeypatch, tmp_path):
    """`[]` is valid JSON that `read` returns happily. Substituting {} would
    publish an empty ledger over whatever the file really held."""
    cid = _campaign(monkeypatch, tmp_path)
    _ledger_path(cid).write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        facts.record(cid, "A fact.", "", "s1")
    with pytest.raises(ValueError):
        facts.retire(cid, "f1", "s1")
    assert _ledger_path(cid).read_text(encoding="utf-8") == "[]"


def test_render_active_leads_with_the_id_so_the_model_can_cite_it(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    facts.record(cid, "The bridge stands.", "the third night", "s1")
    facts.record(cid, "The east gate is barred.", "", "s2")
    assert facts.render_active(cid) == ["f1: The bridge stands. (the third night)",
                                        "f2: The east gate is barred."]


def test_render_active_survives_a_garbled_ledger(monkeypatch, tmp_path):
    """A broken file costs the model one context block, not the whole turn —
    the policy `plot.render_open` and `commitments.render_open` already keep."""
    cid = _campaign(monkeypatch, tmp_path)
    _ledger_path(cid).write_text("{ not json", encoding="utf-8")
    assert facts.render_active(cid) == []


def test_repoint_follows_both_scenes_a_fact_carries(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    old = facts.record(cid, "The bridge stands.", "", "001--s")
    facts.record(cid, "The bridge is down.", "", "002--t", supersedes=old)

    facts.repoint_scenes(cid, {"001--s": "001--2026-07-04--s", "002--t": "002--2026-07-05--t"})

    assert facts.get(cid, old)["scene"] == "001--2026-07-04--s"
    assert facts.get(cid, old)["retired_scene"] == "002--2026-07-05--t"
    assert facts.get(cid, "f2")["scene"] == "002--2026-07-05--t"


def test_repoint_steps_over_a_ledger_it_cannot_read(monkeypatch, tmp_path):
    """This runs from `scene_refs.repoint` AFTER the scene file was renamed, so
    raising here would leave every store later in the sweep pointing at an id
    that no longer exists."""
    cid = _campaign(monkeypatch, tmp_path)
    for bad in ("{ not json", "[]", '{"f1": "not a record", "f2": {"scene": ["x"]}}'):
        _ledger_path(cid).write_text(bad, encoding="utf-8")
        facts.repoint_scenes(cid, {"001--s": "002--t"})   # must not raise
        assert _ledger_path(cid).read_text(encoding="utf-8") == bad


def test_repoint_writes_nothing_when_no_fact_names_a_renamed_scene(monkeypatch, tmp_path):
    """Asserted by making a write fail rather than by comparing timestamps: the
    file would be republished with identical bytes, so neither content nor a
    coarse mtime can tell a skipped write from a redundant one."""
    cid = _campaign(monkeypatch, tmp_path)
    facts.record(cid, "The bridge stands.", "", "001--s")
    monkeypatch.setattr(facts, "_write",
                        lambda *a, **k: pytest.fail("repointed nothing and wrote anyway"))
    facts.repoint_scenes(cid, {"009--other": "009--renamed"})
