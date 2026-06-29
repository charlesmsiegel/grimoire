from grimoire.store import briefs, characters, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    card = characters.blank_card("Aese")
    card["data"]["description"] = "a snowleopardgirl"
    characters.create_character(root, "Aese", "main", card)
    return root


def test_read_missing_is_none(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert briefs.read_brief(root, "aese") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    briefs.write_brief(root, "aese", "A silent snowleopardgirl.", "She keeps house.\nShe is shy.", "h0")
    b = briefs.read_brief(root, "aese")
    assert b == {"tagline": "A silent snowleopardgirl.", "base": "h0",
                 "body": "She keeps house.\nShe is shy."}


def test_missing_brief_is_stale(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert briefs.is_stale(root, "aese") is True


def test_base_match_is_fresh(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    h = briefs.default_card_hash(root, "aese")
    briefs.write_brief(root, "aese", "t", "body", h)
    assert briefs.is_stale(root, "aese") is False


def test_base_mismatch_is_stale(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    briefs.write_brief(root, "aese", "t", "body", "stale-hash")
    assert briefs.is_stale(root, "aese") is True


def test_default_card_hash_unknown_char_is_none(monkeypatch, tmp_path):
    root = _world_with_char(monkeypatch, tmp_path)
    assert briefs.default_card_hash(root, "nobody") is None


def test_build_prompt_includes_card_fields(monkeypatch, tmp_path):
    msgs = briefs.build_prompt({"name": "Aese", "description": "a snowleopardgirl",
                                "personality": "shy", "scenario": ""})
    assert msgs[0]["role"] == "system"
    assert "Aese" in msgs[1]["content"] and "snowleopardgirl" in msgs[1]["content"]
    assert "shy" in msgs[1]["content"]


def test_parse_output_splits_tagline_and_body():
    tagline, body = briefs.parse_output("A silent snowleopardgirl.\n\nShe keeps house. She is shy.")
    assert tagline == "A silent snowleopardgirl."
    assert body == "She keeps house. She is shy."


def test_parse_output_skips_leading_blank_lines():
    tagline, body = briefs.parse_output("\n\nTagline here.\nParagraph here.")
    assert tagline == "Tagline here."
    assert body == "Paragraph here."
