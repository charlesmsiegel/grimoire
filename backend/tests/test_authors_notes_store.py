"""Author's notes, the store half (play controls V) -- `store/authors_notes.py`.

What is worth holding here is what a reader cannot see from the panel: that a
bad file never raises (it sits on the path that composes a turn), that a scene
note follows the scene's identity rather than its sid, and that the notes go
where the scene goes -- dropped with it, copied by a fork, cut with the scenes
a retrospective fork takes off.
"""

import pytest

from grimoire.store import authors_notes, campaigns, fork, scenes, worlds

NOTE = {"text": "Keep the storm audible in every scene.", "depth": 4, "every": 1}


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _file(cid):
    return campaigns.campaign_root(cid) / "authors_notes.json"


def test_read_is_lenient(cid):
    assert authors_notes.read(cid) == {"campaign": None, "scenes": {}, "characters": {}}
    _file(cid).write_text("{not json")
    assert authors_notes.read(cid)["campaign"] is None
    _file(cid).write_text("[]")
    assert authors_notes.read(cid)["scenes"] == {}
    _file(cid).write_text('{"campaign": "nope", "scenes": [], "characters": {"x": 3}}')
    assert authors_notes.read(cid) == {"campaign": None, "scenes": {}, "characters": {}}


def test_saving_over_a_garbled_file_replaces_it(cid):
    _file(cid).write_text("{not json")
    authors_notes.set_campaign(cid, NOTE)
    assert authors_notes.read(cid)["campaign"] == NOTE


def test_normalize_defaults_and_clamps():
    assert authors_notes.normalize({"text": "x"}) == {"text": "x", "depth": 4, "every": 1}
    assert authors_notes.normalize({"text": "  "}) is None
    assert authors_notes.normalize({"text": 3}) is None
    assert authors_notes.normalize("x") is None
    assert authors_notes.normalize({"text": "x" * 2500})["text"] == "x" * 2000
    assert authors_notes.normalize({"text": "x", "depth": 0})["depth"] == 0
    assert authors_notes.normalize({"text": "x", "depth": 99, "every": 0}) == {
        "text": "x", "depth": 50, "every": 1}
    assert authors_notes.normalize({"text": "x", "depth": "deep", "every": True}) == {
        "text": "x", "depth": 4, "every": 1}


def test_set_and_clear_each_level(cid):
    authors_notes.set_campaign(cid, NOTE)
    authors_notes.set_character(cid, "characters:mara", NOTE)
    authors_notes.set_scene(cid, "a" * 32, NOTE)
    data = authors_notes.read(cid)
    assert data["campaign"] == NOTE and data["characters"]["characters:mara"] == NOTE
    assert data["scenes"]["a" * 32] == NOTE
    authors_notes.set_campaign(cid, {"text": ""})
    authors_notes.set_character(cid, "characters:mara", None)
    authors_notes.set_scene(cid, "a" * 32, {"text": " "})
    assert authors_notes.read(cid) == {"campaign": None, "scenes": {}, "characters": {}}


def test_applies_cadence():
    n3 = {**NOTE, "every": 3}
    assert [t for t in range(10) if authors_notes.applies(n3, t)] == [3, 6, 9]
    assert authors_notes.applies(NOTE, 0) and not authors_notes.applies(n3, 4)


def test_scene_note_follows_rename(cid):
    sid = scenes.create_scene(cid, "Mara")
    ident = scenes.ensure_identity(cid, sid)
    authors_notes.set_scene(cid, ident, NOTE)
    new = scenes.rename_scene(cid, sid, "Winifred")
    assert new != sid
    assert authors_notes.read(cid)["scenes"][scenes.scene_identity(cid, new)] == NOTE


def test_delete_scene_drops_note(cid):
    sid = scenes.create_scene(cid, "Mara")
    ident = scenes.ensure_identity(cid, sid)
    authors_notes.set_scene(cid, ident, NOTE)
    authors_notes.set_campaign(cid, NOTE)
    scenes.delete_scene(cid, sid)
    data = authors_notes.read(cid)
    assert ident not in data["scenes"]
    assert data["campaign"] == NOTE
    # A recycled sid gets a fresh identity, so it cannot adopt the dead note.
    again = scenes.create_scene(cid, "Mara")
    assert scenes.ensure_identity(cid, again) not in authors_notes.read(cid)["scenes"]


def test_delete_scene_survives_a_failing_drop(cid, monkeypatch):
    sid = scenes.create_scene(cid, "Mara")
    scenes.ensure_identity(cid, sid)

    def boom(*_a, **_k):
        raise OSError("disk")
    monkeypatch.setattr(authors_notes, "drop", boom)
    scenes.delete_scene(cid, sid)
    assert sid not in {s["id"] for s in scenes.list_scenes(cid)}


def test_copy_scene(cid):
    authors_notes.set_scene(cid, "a" * 32, NOTE)
    authors_notes.copy_scene(cid, "a" * 32, "b" * 32)
    scenes_ = authors_notes.read(cid)["scenes"]
    assert scenes_["a" * 32] == NOTE and scenes_["b" * 32] == NOTE
    authors_notes.copy_scene(cid, "c" * 32, "d" * 32)
    assert "d" * 32 not in authors_notes.read(cid)["scenes"]


def test_fork_copies_notes_and_cut_scenes_lose_theirs(cid):
    first = scenes.create_scene(cid, "Mara")
    scenes.append_message(cid, first, "user", "Mara post")
    second = scenes.create_scene(cid, "Winifred")
    scenes.append_message(cid, second, "user", "Winifred post")
    i1, i2 = scenes.ensure_identity(cid, first), scenes.ensure_identity(cid, second)
    authors_notes.set_scene(cid, i1, NOTE)
    authors_notes.set_scene(cid, i2, {**NOTE, "text": "Second."})
    authors_notes.set_campaign(cid, NOTE)
    child = fork.fork_campaign(cid, "Saltmarch Redux", from_scene=first)["id"]
    data = authors_notes.read(child)
    assert data["campaign"] == NOTE
    assert data["scenes"].get(i1) == NOTE
    assert i2 not in data["scenes"]
    # The source keeps both.
    assert set(authors_notes.read(cid)["scenes"]) == {i1, i2}
