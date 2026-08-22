from grimoire.store import characters, dossiers, worlds


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


def test_write_rejects_ids_that_escape_the_characters_dir(monkeypatch, tmp_path):
    """A dossier edit arrives in a client-supplied PUT body, so its target id is
    untrusted: an id with a separator must not write outside the campaign."""
    import pytest
    root = _root(monkeypatch, tmp_path)
    outside = tmp_path / "pwned.md"
    for bad in ("../../pwned", "..\\..\\pwned", "..", ".", ""):
        with pytest.raises(dossiers.BadDossierId):
            dossiers.write(root, bad, "owned")
    assert not outside.exists()


def test_read_rejects_ids_that_escape_the_characters_dir(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert dossiers.read(root, "../../anything") == ""


def test_build_prompt_includes_name_prior_and_transcript():
    msgs = dossiers.build_prompt("Aese", "was shy", "USER: hi\nAESE: *waves*")
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"]
    assert "was shy" in msgs[1]["content"] and "waves" in msgs[1]["content"]


def test_build_prompt_handles_empty_prior():
    msgs = dossiers.build_prompt("Aese", "", "transcript")
    assert "(none)" in msgs[1]["content"]


def test_a_dossier_does_not_expire_when_the_card_moves_on(monkeypatch, tmp_path):
    """The staleness decision (#57), as behaviour rather than a paragraph.

    A dossier carries no hash of what it was derived from, so nothing about it
    changes when the card does. The reasoning is in the module docstring; what
    a future refactor could break silently is this: a read after the card is
    rewritten still answers with the stored paragraph, not with "" or a flag.
    """
    root = _root(monkeypatch, tmp_path)
    characters.create_character(root, "Winifred", "main", characters.blank_card("Winifred"))
    dossiers.write(root, "winifred", "Winifred now trusts the owner.")
    card = characters.blank_card("Winifred")
    card["data"]["description"] = "rewritten from scratch"
    characters.update_version(root, "winifred",
                              characters.read_character(root, "winifred")["meta"]["default_version"],
                              card)
    assert dossiers.read(root, "winifred") == "Winifred now trusts the owner."


def test_a_refresh_that_says_the_same_thing_proposes_nothing(monkeypatch, tmp_path):
    """The other half of the same decision: the only thing that supersedes a
    dossier is a *different* paragraph the reviewer accepts. An unchanged
    refresh is not an edit, so it never reaches the review as one."""
    root = _root(monkeypatch, tmp_path)
    characters.create_character(root, "Winifred", "main", characters.blank_card("Winifred"))
    prior = "Winifred now trusts the owner."
    dossiers.write(root, "winifred", prior)
    assert dossiers.stage_edit("winifred", "Winifred", prior, prior) is None
    assert dossiers.stage_edit("winifred", "Winifred", prior, "  ") is None
