import logging

import pytest

import grimoire.store as store
from grimoire.store import failsoft, paths


@pytest.fixture(autouse=True)
def _forget_corruption_warnings():
    """`failsoft` dedupes on module state; tests must not inherit each other's."""
    failsoft._warned.clear()


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


# ---- a corrupt pointer still falls back, but says so ----
#
# Dropping the configured data_dir sends home() back to ~/.grimoire, so a user
# who pointed grimoire at a synced folder opens it to an empty library. The
# fallback is right -- refusing to start over a bad dotfile is worse -- but
# "all your worlds are gone" reported as nothing at all is not.

def test_corrupt_pointer_still_falls_back_to_default(monkeypatch, tmp_path, caplog):
    pointer = isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "synced"))
    pointer.write_text("{ half a", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert store.home() == tmp_path / "default"


def test_corrupt_pointer_warns_with_path_and_consequence(monkeypatch, tmp_path, caplog):
    pointer = isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "synced"))
    pointer.write_text("{ half a", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        store.home()
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert str(pointer) in msg
    assert "data_dir" in msg


def test_pointer_of_the_wrong_json_type_warns(monkeypatch, tmp_path, caplog):
    pointer = isolate(monkeypatch, tmp_path)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('["data_dir", "/srv/store"]', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert store.home() == tmp_path / "default"
    assert len(caplog.records) == 1


def test_an_absent_or_intact_pointer_is_silent(monkeypatch, tmp_path, caplog):
    isolate(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING):
        store.home()                                   # never written
        store.set_data_dir(str(tmp_path / "synced"))   # written, then read back
        store.home()
        store.data_dir_info()
    assert caplog.records == []


def test_repeated_resolution_over_a_corrupt_pointer_warns_once(monkeypatch, tmp_path, caplog):
    """home() re-reads the pointer on every call -- hundreds of times per
    request -- so the warning has to be deduped to stay readable."""
    pointer = isolate(monkeypatch, tmp_path)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{ half a", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            store.home()
    assert len(caplog.records) == 1


def test_set_data_dir_over_a_corrupt_pointer_repairs_it_and_goes_quiet(monkeypatch, tmp_path, caplog):
    """The Configuration page is the user's way out: pointing the store
    somewhere rewrites the file whole, and the warning stops."""
    pointer = isolate(monkeypatch, tmp_path)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{ half a", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        store.set_data_dir(str(tmp_path / "synced"))   # warns once, on the read
        caplog.clear()
        assert store.home() == tmp_path / "synced"
    assert caplog.records == []
