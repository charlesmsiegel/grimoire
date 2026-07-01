from grimoire.store import playstate, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.world_root(worlds.create_world("W"))


def test_read_missing_is_none(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert playstate.read_state(root, "seraphine") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "Wounded left arm; travels with the party.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Wounded left arm; travels with the party."
    assert st["updated"]


def test_write_replaces_snapshot(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "v1")
    playstate.write_state(root, "seraphine", "v2")
    assert playstate.read_state(root, "seraphine")["current_state"] == "v2"
