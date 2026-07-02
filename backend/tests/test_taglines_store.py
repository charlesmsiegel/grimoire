from grimoire.store import taglines, characters, worlds


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
