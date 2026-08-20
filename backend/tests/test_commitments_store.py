import json

import pytest

from grimoire.store import campaigns, commitments, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert commitments.read(cid) == {}


def test_set_movement_creates_and_appends_beats(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "Mara swore it aloud.", "s10")
    commitments.set_movement(cid, "the-debt", "", "", "", None, "She missed the first payment.", "s12")
    c = commitments.get(cid, "the-debt")
    assert c["title"] == "Repay Winifred"      # preserved when passed blank
    assert c["kind"] == "promise" and c["status"] == "open"
    assert c["due"] == "before the thaw"       # preserved when passed None
    assert [b["text"] for b in c["beats"]] == ["Mara swore it aloud.",
                                               "She missed the first payment."]
    assert [b["scene"] for b in c["beats"]] == ["s10", "s12"]
    assert c["last_scene"] == "s12"


def test_set_movement_due_is_three_valued(monkeypatch, tmp_path):
    """A deadline is the one field with a meaningful empty state, so it is the
    one field whose "the caller said nothing" is spelled differently from its
    "the caller said none". Without the split, a lifted deadline cannot be
    recorded and a stale date rides the ledger forever."""
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "before the thaw", "Sworn.", "s1")
    commitments.set_movement(cid, "the-debt", "", "", "", None, "Missed.", "s2")
    assert commitments.get(cid, "the-debt")["due"] == "before the thaw"   # None keeps
    commitments.set_movement(cid, "the-debt", "", "", "", "  by the third night  ",
                             "Renegotiated.", "s3")
    assert commitments.get(cid, "the-debt")["due"] == "by the third night"  # text sets
    commitments.set_movement(cid, "the-debt", "", "", "", "", "Whenever, she said.", "s4")
    assert commitments.get(cid, "the-debt")["due"] == ""                  # "" clears
    assert commitments.get(cid, "the-debt")["status"] == "open"    # still owed, just undated


def test_set_movement_ignores_unknown_kind_and_status(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "threat", "open",
                             "", "Winifred named a price.", "s1")
    commitments.set_movement(cid, "the-debt", "", "wager", "advanced", "", "", "s2")
    c = commitments.get(cid, "the-debt")
    assert c["kind"] == "threat"     # "wager" is not a KIND
    assert c["status"] == "open"     # "advanced" is plot's vocabulary, not this one


def test_set_movement_empty_beat_does_not_append(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open", "", "First.", "s1")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "fulfilled", "", "", "s2")
    c = commitments.get(cid, "the-debt")
    assert c["status"] == "fulfilled" and c["last_scene"] == "s2"
    assert len(c["beats"]) == 1


def test_title_defaults_to_the_id(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "", "promise", "open", "", "Beat.", "s1")
    assert commitments.get(cid, "the-debt")["title"] == "the-debt"


def test_open_commitments_excludes_resolved_and_sorts(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "b", "Bee", "threat", "open", "", "beat b", "s2")
    commitments.set_movement(cid, "a", "Ay", "promise", "open", "at dawn", "beat a", "s1")
    for status in commitments.RESOLVED:
        commitments.set_movement(cid, status, status.title(), "promise", status, "", "done", "s3")
    got = commitments.open_commitments(cid)
    assert [c["id"] for c in got] == ["a", "b"]  # every resolved status gone; sorted by last_scene
    assert got[0] == {"id": "a", "title": "Ay", "kind": "promise", "status": "open",
                      "due": "at dawn", "last_scene": "s1", "latest_beat": "beat a"}


def test_repoint_scenes_follows_renames(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open", "", "First.", "s1")
    commitments.repoint_scenes(cid, {"s1": "0001-the-oath"})
    c = commitments.get(cid, "the-debt")
    assert c["last_scene"] == "0001-the-oath"
    assert c["beats"][0]["scene"] == "0001-the-oath"


def test_render_open_forms(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "Mara swore it aloud.", "s1")
    assert commitments.render_open(cid, with_id=True) == [
        "the-debt: Repay Winifred (promise, open), due before the thaw — Mara swore it aloud."]
    assert commitments.render_open(cid, with_id=False) == [
        "Repay Winifred (promise, open), due before the thaw: Mara swore it aloud."]


def test_render_open_omits_absent_due_and_beat(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open", "", "", "s1")
    assert commitments.render_open(cid, with_id=False) == ["Repay Winifred (promise, open)"]


def test_render_open_tolerates_garbled(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text("{ not json", encoding="utf-8")
    assert commitments.render_open(cid, with_id=False) == []  # must not raise


@pytest.mark.parametrize("body", [
    "{ not json",                                  # unparseable: `read` itself raises
    "[]",                                          # the document is a list
    '{"x": []}',                                   # a record is a list
    '{"x": {"last_scene": [], "beats": []}}',      # an unhashable scene id
    '{"x": {"last_scene": "s1", "beats": "nope"}}',   # beats is not a list
    '{"x": {"last_scene": "s1", "beats": [null]}}',   # a beat is not a dict
])
def test_repoint_scenes_steps_over_malformed_records(monkeypatch, tmp_path, body):
    """`scene_refs.repoint` runs AFTER the scene file has been renamed, so raising
    here 500s the rename and leaves the stores it had not reached yet pointing at
    an id that no longer exists.

    Unparseable is in the list beside the wrong shapes on purpose: they need two
    different guards (`json.loads` raises on the first and returns happily on the
    second), and covering only the shapes left `{ not json` crashing the sweep."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text(body, encoding="utf-8")
    commitments.repoint_scenes(cid, {"s1": "0001-the-oath"})     # must not raise


def test_open_commitments_normalizes_the_status_it_filters_on(monkeypatch, tmp_path):
    """The predicate and the projection have to read the field the same way. A
    hand-edited `" fulfilled "` was *shown* as fulfilled and still counted as
    open, so it stayed on the ledger and in every absorb snapshot — where the
    model can move or resolve it a second time."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        '{"paid": {"title": "The debt", "kind": "promise", "status": " fulfilled ",'
        ' "due": "", "beats": [], "last_scene": "s1"},'
        ' "owed": {"title": "A promise", "kind": "promise", "status": " open ",'
        ' "due": "", "beats": [], "last_scene": "s1"}}', encoding="utf-8")
    assert [c["id"] for c in commitments.open_commitments(cid)] == ["owed"]


@pytest.mark.parametrize("record", ["[1]", '"a string"', "7", "true"])
def test_set_movement_replaces_a_record_that_is_not_a_mapping(monkeypatch, tmp_path, record):
    """A TRUTHY non-dict walked past the `or` and raised on `.get` — and, like
    the malformed beat list, silently: `materialize` skips a non-dict record and
    stages the movement as new, so the row is approved and `apply_edits` drops
    the write with a 200 and no failure reported. A record that is not a mapping
    holds nothing this module can read, so the movement wins."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        '{"x": %s}' % record, encoding="utf-8")
    commitments.set_movement(cid, "x", "The debt", "promise", "open", "at dawn",
                             "Sworn.", "s1")
    rec = commitments.get(cid, "x")
    assert rec["title"] == "The debt" and rec["kind"] == "promise"
    assert rec["due"] == "at dawn" and rec["last_scene"] == "s1"
    assert [b["text"] for b in rec["beats"]] == ["Sworn."]


@pytest.mark.parametrize("beats", ["{}", '"nope"', "7", "null"])
def test_set_movement_replaces_a_malformed_beat_list(monkeypatch, tmp_path, beats):
    """`setdefault` only covers a MISSING key, so a hand-edited `"beats": {}`
    reached `.append` and raised — and every reader upstream tolerates that
    record, so the row staged, the reviewer approved it, and `apply_edits`'
    per-edit `except` dropped the write with a 200 and no failure reported. The
    beat that was actually approved is the thing that must survive."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        '{"x": {"title": "The debt", "kind": "promise", "status": "open",'
        ' "due": "", "beats": %s, "last_scene": "s1"}}' % beats, encoding="utf-8")
    commitments.set_movement(cid, "x", "", "", "", None, "She missed a payment.", "s2")
    rec = commitments.get(cid, "x")
    assert [b["text"] for b in rec["beats"]] == ["She missed a payment."]
    assert rec["title"] == "The debt" and rec["last_scene"] == "s2"


def test_repoint_scenes_still_follows_the_readable_records(monkeypatch, tmp_path):
    """Stepping over the broken ones must not stop the sweep."""
    cid = _campaign(monkeypatch, tmp_path)
    commitments.set_movement(cid, "good", "The debt", "promise", "open", "", "Sworn.", "s1")
    data = json.loads((campaigns.campaign_root(cid) / "commitments.json").read_text("utf-8"))
    data["broken"] = []
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        json.dumps(data), encoding="utf-8")
    commitments.repoint_scenes(cid, {"s1": "0001-the-oath"})
    assert commitments.get(cid, "good")["last_scene"] == "0001-the-oath"


def test_open_commitments_case_folds_the_status_it_filters_on(monkeypatch, tmp_path):
    """The whitespace fix left capitalization bypassing the same check. Every
    status this module writes is already lower-case (`set_movement` accepts only
    a member of `STATUSES`), so folding can rescue a hand-edited record without
    reinterpreting anything the pipeline produced."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        '{"paid": {"title": "The debt", "kind": "promise", "status": "Fulfilled",'
        ' "due": "", "beats": [], "last_scene": "s1"},'
        ' "gone": {"title": "A threat", "kind": "threat", "status": " EXPIRED ",'
        ' "due": "", "beats": [], "last_scene": "s1"},'
        ' "owed": {"title": "A promise", "kind": "promise", "status": "Open",'
        ' "due": "", "beats": [], "last_scene": "s1"}}', encoding="utf-8")
    assert [c["id"] for c in commitments.open_commitments(cid)] == ["owed"]
