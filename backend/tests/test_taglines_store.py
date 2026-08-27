from grimoire.store import characters, taglines, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    card = characters.blank_card("Aese")
    card["data"]["description"] = "a snowleopardgirl"
    characters.create_character(root, "Aese", "main", card)
    return root


def test_read_missing_is_empty(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert taglines.read(root, "aese") == ""


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    taglines.write(root, "aese", "  A silent snowleopardgirl.  ")
    assert taglines.read(root, "aese") == "A silent snowleopardgirl."


def test_build_prompt_includes_card_fields():
    msgs = taglines.build_prompt({"name": "Aese", "description": "a snowleopardgirl",
                                  "personality": "shy", "scenario": ""})
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"] and "snowleopardgirl" in msgs[1]["content"]


def test_parse_output_takes_first_nonblank_line():
    assert taglines.parse_output("\n\nA silent snowleopardgirl.\nextra") == "A silent snowleopardgirl."


# --- The stat-based sweep the to-do list counts with -------------------------


def test_untagged_ids_matches_read_including_the_blank_write(monkeypatch, tmp_path):
    """`untagged_ids` never opens the file, so this is what makes it sound.

    `write` strips, so the only blank it can produce is a lone newline -- one
    byte -- and that is exactly the case a size test has to get right. A
    character written a real tagline has one; a character written "" has a file
    and still counts as untagged, the same answer `read` gives.
    """
    root = _world_with_char(monkeypatch, tmp_path)
    ids = characters.character_refs(root)
    assert ids

    def honest():
        return {c for c in ids if not taglines.read(root, c)}

    assert set(taglines.untagged_ids(root, ids)) == honest() == set(ids)

    taglines.write(root, ids[0], "   ")          # strips to blank -> 1-byte file
    assert taglines.tagline_path(root, ids[0]).exists()
    assert set(taglines.untagged_ids(root, ids)) == honest() == set(ids)

    taglines.write(root, ids[0], "Keeps the ledger, and the grudge.")
    assert set(taglines.untagged_ids(root, ids)) == honest() == set()
