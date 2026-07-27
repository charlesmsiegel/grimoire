import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


def test_first_read_creates_defaults(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["theme"] == "codex"
    assert (tmp_path / "config.md").exists()


def test_context_scan_depth_default_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.read_config()["context_scan_depth"] == "8"
    s.write_config(context_scan_depth="5")
    assert s.read_config()["context_scan_depth"] == "5"


def test_write_merges_without_clearing(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(user_label="Kestrel")
    s.write_config(theme="manuscript")  # must not wipe the label
    cfg = s.read_config()
    assert cfg["user_label"] == "Kestrel"
    assert cfg["theme"] == "manuscript"


def test_recap_depth_default_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    assert s.read_config()["recap_depth"] == "5"
    s.write_config(recap_depth="3")
    assert s.read_config()["recap_depth"] == "3"


def test_label_defaults_and_write(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cfg = s.read_config()
    assert cfg["user_label"] == "You"
    assert cfg["assistant_label"] == "Grimoire"
    cfg = s.write_config(user_label="Kestrel", assistant_label="Narrator")
    assert cfg["user_label"] == "Kestrel"
    assert s.read_config()["assistant_label"] == "Narrator"


def test_response_and_length_keys_survive_read_config(monkeypatch, tmp_path):
    """The cascade's global scope reads through read_config(), which narrows its
    return to _CONFIG_KEYS — so a key missing from that tuple is silently
    dropped and the whole global scope goes dead with no error."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    store.write_config(response_preset="terse", length_reply_words="120",
                        length_blocks="2", length_paragraphs="1",
                        length_speakers="2", length_blocks_per_speaker="1")
    cfg = store.read_config()
    assert cfg["response_preset"] == "terse"
    assert cfg["length_reply_words"] == "120"
    assert cfg["length_blocks_per_speaker"] == "1"


def test_length_keys_default_to_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cfg = store.read_config()
    assert cfg["response_preset"] == ""
    assert cfg["length_reply_words"] == ""
