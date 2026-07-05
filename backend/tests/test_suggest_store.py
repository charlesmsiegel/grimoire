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


# ---- greeting ranking (folded into the suggestions call) ----
def _campaign_with_greetings(monkeypatch, tmp_path, n):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    ch = _char(wroot, "Ann")
    from grimoire.store import greetings as gr
    gids = [gr.create_greeting(wroot, f"Opening {i}", ch, "main", f"Body of opening {i}. " * 30)
            for i in range(n)]
    return campaigns.create_campaign("Run", wid), gids


def test_greeting_candidates_only_when_more_than_two(monkeypatch, tmp_path):
    cid, gids = _campaign_with_greetings(monkeypatch, tmp_path, 3)
    cands = suggest.greeting_candidates(cid)
    assert [c["id"] for c in cands] == gids
    assert all(c["name"].startswith("Opening") for c in cands)
    assert all(0 < len(c["excerpt"]) <= 300 for c in cands)


def test_greeting_candidates_empty_at_two_or_fewer(monkeypatch, tmp_path):
    cid, _gids = _campaign_with_greetings(monkeypatch, tmp_path, 2)
    assert suggest.greeting_candidates(cid) == []


def test_build_prompt_lists_greeting_candidates(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snapshot = suggest.build_snapshot(cid)
    cands = [{"id": "g1", "name": "Reckoning", "excerpt": "A debt comes due."}]
    messages = suggest.build_prompt(snapshot, greeting_candidates=cands)
    assert "greeting_picks" in messages[0]["content"]
    assert "g1 = Reckoning" in messages[1]["content"]
    assert "A debt comes due." in messages[1]["content"]
    # without candidates the prompt is unchanged (no phantom instruction)
    plain = suggest.build_prompt(snapshot)
    assert "greeting_picks" not in plain[0]["content"]


def test_parse_greeting_picks_validates_dedupes_and_keeps_order(monkeypatch, tmp_path):
    text = '{"suggestions": [], "greeting_picks": ["g2", "ghost", "g1", "g2", 7]}'
    assert suggest.parse_greeting_picks(text, {"g1", "g2", "g3"}) == ["g2", "g1"]
    assert suggest.parse_greeting_picks("no json here", {"g1"}) == []
    assert suggest.parse_greeting_picks('{"greeting_picks": "g1"}', {"g1"}) == []


# ---- suggested dates (per-suggestion "date" + top-level "next_date") ----
def test_build_prompt_requests_dates_only_with_a_current_date():
    snap = {"now": "2026-01-01", "friendly": "Jan 1", "holidays_today": [], "upcoming": None,
            "birthdays": [], "open_threads": [], "absent_cast": [],
            "available_cast": [], "available_locations": []}
    assert "next_date" in suggest.build_prompt(snap)[0]["content"]
    snap["now"] = ""
    assert "next_date" not in suggest.build_prompt(snap)[0]["content"]


def test_parse_output_keeps_valid_dates_and_drops_bad_ones(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    text = ('{"suggestions": ['
            '{"title": "A", "premise": "P", "cast": [], "location": "", "date": "2026-07-10"},'
            '{"title": "B", "premise": "P", "cast": [], "location": "", "date": "2026-13-40"},'
            '{"title": "C", "premise": "P", "cast": [], "location": ""}]}')
    out = suggest.parse_output(text, cid)
    assert [s["date"] for s in out] == ["2026-07-10", "", ""]


def test_parse_next_date_validates_and_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert suggest.parse_next_date('{"suggestions": [], "next_date": "2026-07-08"}', cid) == "2026-07-08"
    assert suggest.parse_next_date('{"suggestions": [], "next_date": "soonish"}', cid) == ""
    assert suggest.parse_next_date('{"suggestions": []}', cid) == ""
    assert suggest.parse_next_date("not json", cid) == ""
