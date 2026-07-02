from grimoire.store import dossiers, characters, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    root = worlds.world_root(worlds.create_world("W"))
    characters.create_character(root, "Aese", "main", characters.blank_card("Aese"))
    return root


def test_read_missing_is_empty(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert dossiers.read(root, "aese") == ""


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    dossiers.write(root, "aese", "  Aese now trusts the owner.  ")
    assert dossiers.read(root, "aese") == "Aese now trusts the owner."


def test_build_prompt_includes_name_prior_and_transcript():
    msgs = dossiers.build_prompt("Aese", "was shy", "USER: hi\nAESE: *waves*")
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"]
    assert "was shy" in msgs[1]["content"] and "waves" in msgs[1]["content"]


def test_build_prompt_handles_empty_prior():
    msgs = dossiers.build_prompt("Aese", "", "transcript")
    assert "(none)" in msgs[1]["content"]
