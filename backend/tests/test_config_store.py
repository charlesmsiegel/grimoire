import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_first_read_creates_defaults(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["openrouter_key"] == ""
    assert cfg["theme"] == "occult"
    assert cfg["model"]  # some non-empty default
    assert (tmp_path / "config.md").exists()


def test_context_scan_depth_default_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.read_config()["context_scan_depth"] == "8"
    s.write_config(context_scan_depth="5")
    assert s.read_config()["context_scan_depth"] == "5"


def test_write_merges_without_clearing(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(openrouter_key="sk-or-secret")
    s.write_config(model="anthropic/claude-x")  # must not wipe the key
    cfg = s.read_config()
    assert cfg["openrouter_key"] == "sk-or-secret"
    assert cfg["model"] == "anthropic/claude-x"
    assert cfg["theme"] == "occult"
