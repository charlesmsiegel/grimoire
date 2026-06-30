from grimoire.store import characters, pcs, worlds


def test_pc_persona_roundtrips_birthdate(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    pid, vid = pcs.create_pc(root, "Elara", [], persona={
        "name": "Elara", "pronouns": "she/her", "summary": "scholar",
        "description": "A wanderer.", "birthdate": "1990-06-29"})
    assert pcs.read_persona(root, pid, vid)["birthdate"] == "1990-06-29"


def test_pc_persona_birthdate_defaults_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    pid, vid = pcs.create_pc(root, "Mara", [], persona=pcs.blank_persona("Mara"))
    assert pcs.read_persona(root, pid, vid)["birthdate"] == ""


def test_character_meta_birthdate_set_and_read(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    root = worlds.world_root(wid)
    cid, _ = characters.create_character(root, "Seraphine", "default", characters.blank_card("Seraphine"))
    assert characters.read_character(root, cid)["meta"]["birthdate"] == ""
    characters.set_birthdate(root, cid, "1985-03-14")
    assert characters.read_character(root, cid)["meta"]["birthdate"] == "1985-03-14"
