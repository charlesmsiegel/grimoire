from grimoire.store import campaigns, relationships, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert relationships.read(cid) == {"feelings": {}, "bonds": {}}


def test_feeling_directed_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "grateful")
    assert relationships.get_feeling(cid, "characters:a", "characters:b") == {
        "trust": 4, "affection": 3, "tension": 1, "note": "grateful"}
    assert relationships.get_feeling(cid, "characters:b", "characters:a") is None  # asymmetric


def test_bond_key_is_canonical(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_bond(cid, "characters:b", "characters:a", "allies", since_scene="s1")
    assert relationships.get_bond(cid, "characters:a", "characters:b")["type"] == "allies"
    relationships.set_bond(cid, "characters:a", "characters:b", "rivals")  # reorder, no since
    data = relationships.read(cid)
    assert list(data["bonds"]) == ["characters:a|characters:b"]  # single canonical key
    assert data["bonds"]["characters:a|characters:b"] == {"type": "rivals", "since_scene": "s1"}


def test_render_present_lists_feelings_and_bonds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "warm")
    relationships.set_bond(cid, "characters:a", "characters:b", "allies")
    lines = relationships.render_present(cid, ["characters:a", "characters:b"], lambda t: t.split(":")[1].title())
    assert "A → B: trust 4, affection 3, tension 1 (warm)" in lines
    assert "A & B: allies" in lines
