import pytest
from grimoire.store import entities, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_create_list_read(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Drowned Realm")
    assert wid == "drowned-realm"
    entities.create_entity(worlds.world_root(wid), "locations", "Drowned Library")
    listed = worlds.list_worlds()
    assert len(listed) == 1
    assert listed[0]["name"] == "Drowned Realm"
    assert listed[0]["counts"]["locations"] == 1
    w = worlds.read_world(wid)
    assert w["meta"]["id"] == wid
    assert w["counts"]["locations"] == 1


def test_rename_keeps_id(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Old")
    worlds.rename_world(wid, "New Name")
    assert worlds.read_world(wid)["meta"]["name"] == "New Name"  # id unchanged


def test_missing_world_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        worlds.read_world("nope")
    with pytest.raises(worlds.WorldNotFound):
        worlds.rename_world("nope", "x")
    with pytest.raises(worlds.WorldNotFound):
        worlds.delete_world("nope")


def test_delete_removes_world(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Doomed")
    worlds.delete_world(wid)
    assert worlds.list_worlds() == []


def test_world_counts_include_greetings(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import worlds
    wid = worlds.create_world("Saltmarch")
    root = worlds.world_root(wid)
    (root / "greetings").mkdir()
    (root / "greetings" / "gala.md").write_text("---\nname: Gala\n---\n", encoding="utf-8")
    assert worlds.read_world(wid)["counts"]["greetings"] == 1
    assert worlds.list_worlds()[0]["counts"]["greetings"] == 1
