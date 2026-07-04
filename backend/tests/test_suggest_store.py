from grimoire.store import (appearances, campaigns, characters, chronicle, entities,
                            plot, scenes, suggest, taglines, worlds)


def _world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("W")


def _campaign(monkeypatch, tmp_path):
    # campaign over an empty world (seed the world BEFORE create_campaign elsewhere)
    return campaigns.create_campaign("Run", _world(monkeypatch, tmp_path))


def _char(root, name, birthdate=""):
    cid_ = characters.create_character(root, name, "main", characters.blank_card(name))[0]
    if birthdate:
        characters.set_birthdate(root, cid_, birthdate)
    return cid_


def test_build_snapshot_gathers_signals(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    absent = _char(wroot, "Doran")            # appears then absent from recent chronicle
    present = _char(wroot, "Seraphine")
    taglines.write(wroot, absent, "a quiet sellsword")   # seeded before the fork
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", absent, "main", "npc")
    appearances.appear(cid, s1, "characters", present, "main", "npc")
    chronicle.absorb(cid, {"id": s1, "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [f"characters/{present}"], "location": "", "date": ""})
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", s1)

    snap = suggest.build_snapshot(cid)
    assert [t["title"] for t in snap["open_threads"]] == ["The map"]
    absent_by_name = {a["name"]: a["tagline"] for a in snap["absent_cast"]}
    assert absent_by_name.get("Doran") == "a quiet sellsword"   # tagline travels with the copy
    assert "Seraphine" not in absent_by_name                    # present is not absent
    tokens = [c["token"] for c in snap["available_cast"]]
    assert f"characters:{absent}" in tokens and f"characters:{present}" in tokens


def test_build_snapshot_tolerates_empty_campaign(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)  # no scenes/chronicle/plot/calendar
    assert snap["open_threads"] == [] and snap["absent_cast"] == []
    assert snap["now"] == "" and snap["birthdays"] == []


def test_build_prompt_includes_signals():
    snap = {"now": "2026-01-01", "friendly": "Jan 1", "holidays_today": ["New Year"],
            "upcoming": {"name": "Festival", "in_days": 5}, "birthdays": [{"name": "Ann", "age": 30, "when": "today"}],
            "open_threads": [{"id": "the-map", "title": "The map", "status": "open", "latest_beat": "found it"}],
            "absent_cast": [{"name": "Doran", "tagline": "a sellsword"}],
            "available_cast": [{"token": "characters:ann", "name": "Ann"}],
            "available_locations": [{"id": "keep", "name": "The Keep"}]}
    msgs = suggest.build_prompt(snap)
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "The map" in user and "Doran" in user and "Ann" in user and "The Keep" in user
    assert "New Year" in user and "today" in user


def test_parse_output_validates_ids(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    ann = characters.create_character(wroot, "Ann", "main", characters.blank_card("Ann"))[0]
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "The Keep")
    text = ('{"suggestions": ['
            f'{{"title": "T", "premise": "P", "cast": ["characters:{ann}", "characters:ghost"], "location": "the-keep"}},'
            '{"title": "", "premise": "no title", "cast": [], "location": ""},'
            '{"title": "Bad loc", "premise": "P2", "cast": [], "location": "nowhere"}]}')
    out = suggest.parse_output(text, cid)
    assert [s["title"] for s in out] == ["T", "Bad loc"]          # title-less dropped
    assert out[0]["cast"] == [f"characters:{ann}"]                # ghost dropped
    assert out[0]["location"] == "the-keep" and out[1]["location"] == ""  # unknown loc -> ""


def test_parse_output_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert suggest.parse_output("not json", cid) == []


def test_parse_output_accepts_bare_array(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    # a common LLM deviation: a top-level array instead of {"suggestions": [...]}
    out = suggest.parse_output('[{"title": "T", "premise": "P", "cast": [], "location": ""}]', cid)
    assert [s["title"] for s in out] == ["T"]


def test_build_snapshot_tolerates_garbled_chronicle(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{ not json", encoding="utf-8")
    snap = suggest.build_snapshot(cid)  # must not raise
    assert snap["now"] == ""


def test_build_snapshot_dedupes_available_cast(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    hero = _char(wroot, "Hero")
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", hero, "main", "player")  # campaign char AND roster player
    tokens = [c["token"] for c in suggest.build_snapshot(cid)["available_cast"]]
    assert tokens.count(f"characters:{hero}") == 1  # listed once, not duplicated
