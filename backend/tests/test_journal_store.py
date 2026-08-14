"""The append-only change journal (#31): storage, ids, retention, repointing.

`changes.json` is a rolling upsert, so the second scene to touch a record erases
the first scene's delta. This file covers the history that replaces it -- and in
particular the two properties an undo target depends on: an id is never reused,
and an entry only ever gains its `undone` stamp.
"""

import json

import pytest

from grimoire.store import campaigns, journal, worlds


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


def _row(label="Saltmarch — locations", **over):
    return {"scene": "s1", "source": "absorb", "kind": "lore",
            "ref": {"kind": "locations", "id": "saltmarch"},
            "field": "body", "label": label, "before": "old", "after": "new",
            "undo": None, "why": "", **over}


def test_empty_campaign_has_no_history(cid):
    assert journal.read(cid) == []
    assert journal.get(cid, "j1") is None


def test_append_stamps_ids_and_a_timestamp(cid):
    written = journal.append(cid, [_row(), _row(label="The Pact — lore")])
    assert [e["id"] for e in written] == ["j1", "j2"]
    assert all(e["ts"] for e in written)
    assert [e["label"] for e in journal.read(cid)] == ["Saltmarch — locations",
                                                      "The Pact — lore"]


def test_append_empty_is_a_noop(cid):
    journal.append(cid, [])
    assert not (campaigns.campaign_root(cid) / "journal.json").exists()


def test_ids_keep_rising_across_appends(cid):
    journal.append(cid, [_row()])
    journal.append(cid, [_row()])
    assert [e["id"] for e in journal.read(cid)] == ["j1", "j2"]


def test_get_finds_by_id(cid):
    journal.append(cid, [_row(), _row(label="Second")])
    assert journal.get(cid, "j2")["label"] == "Second"


def test_retention_drops_the_oldest_and_never_reuses_an_id(cid, monkeypatch):
    monkeypatch.setattr(journal, "RETENTION", 3)
    for i in range(5):
        journal.append(cid, [_row(label=f"edit {i}")])
    entries = journal.read(cid)
    assert [e["id"] for e in entries] == ["j3", "j4", "j5"]
    # The high-water mark survived the trim, so the next append cannot hand out
    # an id an undo target might still be quoting.
    assert journal.append(cid, [_row()])[0]["id"] == "j6"


def test_a_hand_trimmed_seq_cannot_reissue_an_id(cid):
    journal.append(cid, [_row(), _row()])
    p = campaigns.campaign_root(cid) / "journal.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["seq"] = 0                       # a hand edit, or an older writer
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert journal.append(cid, [_row()])[0]["id"] == "j3"


def test_mark_undone_stamps_once(cid):
    journal.append(cid, [_row()])
    assert journal.mark_undone(cid, "j1", "j2") is True
    assert journal.get(cid, "j1")["undone"]["by"] == "j2"
    assert journal.mark_undone(cid, "j1", "j3") is False


def test_mark_undone_of_an_unknown_entry_is_false(cid):
    assert journal.mark_undone(cid, "j9", "j1") is False


def test_repoint_scenes_follows_a_rename(cid):
    journal.append(cid, [_row(), _row(scene="s2")])
    journal.repoint_scenes(cid, {"s1": "0003-the-blockade"})
    assert [e["scene"] for e in journal.read(cid)] == ["0003-the-blockade", "s2"]


def test_repoint_scenes_writes_nothing_when_nothing_matches(cid):
    journal.append(cid, [_row()])
    p = campaigns.campaign_root(cid) / "journal.json"
    before = p.read_text(encoding="utf-8")
    journal.repoint_scenes(cid, {"other": "renamed"})
    assert p.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("body", ["{not json", '"a string"', "[1, 2]", "null"])
def test_read_tolerates_garbage(cid, body):
    (campaigns.campaign_root(cid) / "journal.json").write_text(body, encoding="utf-8")
    assert journal.read(cid) == []


def test_non_dict_entries_are_stepped_over(cid):
    (campaigns.campaign_root(cid) / "journal.json").write_text(
        json.dumps({"seq": 4, "entries": [1, {"id": "j4", "label": "kept"}]}),
        encoding="utf-8")
    assert [e["label"] for e in journal.read(cid)] == ["kept"]
    assert journal.append(cid, [_row()])[0]["id"] == "j5"


def test_the_byte_cap_is_what_actually_binds(cid, monkeypatch):
    """A row cap bounds nothing on its own: rows carry record text, up to four
    copies of it, so 500 of them is however many megabytes the campaign's bodies
    happen to be."""
    monkeypatch.setattr(journal, "MAX_BYTES", 4000)
    for i in range(20):
        journal.append(cid, [_row(before="x" * 500, after="y" * 500)])
    entries = journal.read(cid)
    assert len(entries) < 20                       # the row cap never fired
    assert len(json.dumps(entries)) <= 4000
    assert entries[-1]["id"] == "j20"              # the newest survived


def test_a_row_larger_than_the_cap_is_still_recorded(cid, monkeypatch):
    """The cap bounds accumulation, not the present: trimming an oversized row
    away would leave the write that just happened with no history at all."""
    monkeypatch.setattr(journal, "MAX_BYTES", 100)
    journal.append(cid, [_row(before="x" * 5000)])
    assert [e["id"] for e in journal.read(cid)] == ["j1"]


def test_both_caps_apply(cid, monkeypatch):
    monkeypatch.setattr(journal, "RETENTION", 3)
    monkeypatch.setattr(journal, "MAX_BYTES", 10_000_000)
    for _ in range(6):
        journal.append(cid, [_row()])
    assert [e["id"] for e in journal.read(cid)] == ["j4", "j5", "j6"]
