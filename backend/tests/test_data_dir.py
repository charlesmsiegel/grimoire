import grimoire.store as store
from grimoire.store import paths


def isolate(monkeypatch, tmp_path):
    """Point the bootstrap pointer at a temp file and clear the env override."""
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    pointer = tmp_path / "pointer" / ".grimoire.json"
    monkeypatch.setattr(paths, "_pointer_path", lambda: pointer)
    monkeypatch.setattr(paths, "DEFAULT_HOME", tmp_path / "default")
    return pointer


def test_default_when_unset(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    info = store.data_dir_info()
    assert info["is_default"] is True
    assert info["source"] == "default"
    assert info["data_dir"] == str(tmp_path / "default")


def test_set_data_dir_persists_and_resolves(monkeypatch, tmp_path):
    pointer = isolate(monkeypatch, tmp_path)
    target = tmp_path / "dropbox" / "grimoire"
    store.set_data_dir(str(target))

    assert store.home() == target
    assert (target / "worlds").is_dir()
    assert (target / "campaigns").is_dir()
    assert pointer.exists()

    info = store.data_dir_info()
    assert info["source"] == "custom"
    assert info["is_default"] is False
    assert info["data_dir"] == str(target)


def test_reset_to_default(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "elsewhere"))
    assert store.data_dir_info()["source"] == "custom"

    store.set_data_dir(None)
    assert store.data_dir_info()["source"] == "default"
    assert store.home() == tmp_path / "default"


def test_env_var_wins_over_pointer(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "pointed"))
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "envdir"))

    assert store.home() == tmp_path / "envdir"
    info = store.data_dir_info()
    assert info["source"] == "env"


def test_rejects_file_target(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    afile = tmp_path / "afile"
    afile.write_text("not a dir", encoding="utf-8")
    try:
        store.set_data_dir(str(afile))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a non-directory target")


def test_config_follows_data_dir(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "campaign-a"))
    store.write_config(active_connection_id="claude")

    store.set_data_dir(str(tmp_path / "campaign-b"))
    # A fresh store at the new location gets defaults, not campaign-a's value.
    assert store.read_config()["active_connection_id"] != "claude"
    assert (tmp_path / "campaign-b" / "config.md").exists()
