from grimoire.store import (appearances, campaigns, characters, chronicle, entities,
                            plot, scenes, suggest, worlds)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def _char(root, name, birthdate=""):
    cid_ = characters.create_character(root, name, "main", characters.blank_card(name))[0]
    if birthdate:
        characters.set_birthdate(root, cid_, birthdate)
    return cid_


def test_build_snapshot_gathers_signals(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    absent = _char(wroot, "Doran")            # world char, appears then absent from recent chronicle
    present = _char(wroot, "Seraphine")
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", absent, "main", "npc")
    appearances.appear(cid, s1, "characters", present, "main", "npc")
    chronicle.absorb(cid, {"id": s1, "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [f"characters/{present}"], "location": "", "date": ""})
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", s1)

    snap = suggest.build_snapshot(cid)
    assert [t["title"] for t in snap["open_threads"]] == ["The map"]
    absent_names = [a["name"] for a in snap["absent_cast"]]
    assert "Doran" in absent_names and "Seraphine" not in absent_names   # present is not absent
    tokens = [c["token"] for c in snap["available_cast"]]
    assert f"characters:{absent}" in tokens and f"characters:{present}" in tokens


def test_build_snapshot_tolerates_empty_campaign(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)  # no scenes/chronicle/plot/calendar
    assert snap["open_threads"] == [] and snap["absent_cast"] == []
    assert snap["now"] == "" and snap["birthdays"] == []
