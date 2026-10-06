import json
import textwrap

import pytest

from grimoire.store import (
    appearances,
    calendars,
    campaigns,
    characters,
    chronicle,
    clock,
    commitments,
    entities,
    events,
    overlay,
    pcs,
    playing,
    plot,
    scenes,
    suggest,
    taglines,
    worlds,
)
from grimoire.store.continuity import doc as continuity_doc
from grimoire.store.continuity import drivers as continuity_drivers
from grimoire.store.continuity import pressure


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


def _campaign_with_player_character(monkeypatch, tmp_path):
    # a `characters`-kind actor seated with role="player" -- what CastPanel's
    # role selector allows, and the exact case the offscreen filter must catch.
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    mara = _char(wroot, "Mara")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "One")
    appearances.appear(cid, sid, "characters", mara, "main", "player")
    return cid


def _snap(**over) -> dict:
    """A hand-built suggestion snapshot: today's keys, plus the driver keys
    `build_snapshot` adds (empty), so the templates render it under
    StrictUndefined exactly as they render a real one."""
    base = {"now": "", "friendly": "", "notation": {"example": "", "months": []},
            "holidays_today": [], "events_today": [], "upcoming": None, "birthdays": [],
            "story_so_far": [], "open_threads": [], "cast": [], "available_locations": [],
            "commitments": [], "timeline": [], "driver_index": [], "anchors": [],
            "links": [], "fixed": None, "near_days": 7, "sooner_ref": ""}
    return {**base, **over}


def test_build_snapshot_classifies_cast_and_annotates_threads(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    absent = _char(wroot, "Doran")        # appears in s1 but not in s1's chronicle cast
    present = _char(wroot, "Seraphine")   # in the most recent scene's cast
    unseen = _char(wroot, "Mira")         # never on screen
    taglines.write(wroot, absent, "a quiet sellsword")   # seeded before the fork
    taglines.write(wroot, unseen, "a wandering oracle")
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", absent, "main", "npc")
    appearances.appear(cid, s1, "characters", present, "main", "npc")
    chronicle.absorb(cid, {"id": s1, "one_line": "They gathered at dusk.", "summary": "y",
                           "keywords": [], "cast": [f"characters/{present}"],
                           "location": "The Hall", "date": "2026-01-02"})
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", s1)

    snap = suggest.build_snapshot(cid)
    by_name = {c["name"]: c for c in snap["cast"]}
    assert by_name["Seraphine"]["status"] == "present"
    assert by_name["Doran"]["status"] == "appeared" and by_name["Doran"]["tagline"] == "a quiet sellsword"
    assert by_name["Mira"]["status"] == "unseen" and by_name["Mira"]["tagline"] == "a wandering oracle"
    assert [t["title"] for t in snap["open_threads"]] == ["The map"]
    assert snap["open_threads"][0]["dormancy"] == 0            # advanced in the most recent scene
    assert snap["story_so_far"][0]["one_line"] == "They gathered at dusk."
    assert snap["story_so_far"][0]["location"] == "The Hall"


def test_build_snapshot_tolerates_empty_campaign(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)  # no scenes/chronicle/plot/calendar
    assert snap["open_threads"] == [] and snap["cast"] == []
    assert snap["story_so_far"] == []
    assert snap["now"] == "" and snap["birthdays"] == []


def test_campaign_only_character_with_partial_birthdate_reaches_suggestions(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid, _ = overlay.create_character(cid, "Seraphine")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "--05-09")
    clock.advance(cid, to="2026-05-08")

    snap = suggest.build_snapshot(cid)
    assert snap["birthdays"] == [{"name": "Seraphine", "age": None, "when": "in 1 days"}]
    assert "Seraphine" in suggest.build_prompt(snap)[1]["content"]


def test_month_only_birthdate_suggests_without_inventing_day_or_age(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid, _ = overlay.create_character(cid, "Mara")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "--05")
    clock.advance(cid, to="2026-05-08")

    snap = suggest.build_snapshot(cid)
    assert snap["birthdays"] == [{"name": "Mara", "age": None, "when": "this month"}]
    prompt = suggest.build_prompt(snap)[1]["content"]
    assert "Mara" in prompt and "this month" in prompt
    assert "age None" not in prompt


def test_year_and_month_without_day_does_not_claim_an_exact_birthday(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid, _ = overlay.create_character(cid, "Mara")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "1985-05")
    clock.advance(cid, to="2026-05-08")

    assert suggest.build_snapshot(cid)["birthdays"] == [
        {"name": "Mara", "age": None, "when": "this month"}]


def test_full_birthdate_suggestion_uses_age_on_the_upcoming_day(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid, _ = overlay.create_character(cid, "Mara")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "1985-05-09")
    clock.advance(cid, to="2026-05-08")

    assert suggest.build_snapshot(cid)["birthdays"] == [
        {"name": "Mara", "age": 41, "when": "in 1 days"}]


def test_build_snapshot_dormancy_counts_scenes_since_last_advance(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    hero = _char(wroot, "Hero")
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    s2 = scenes.create_scene(cid, "Two")
    appearances.appear(cid, s1, "characters", hero, "main", "npc")
    chronicle.absorb(cid, {"id": s1, "one_line": "a", "summary": "", "keywords": [],
                           "cast": [], "location": "", "date": "2026-01-01"})
    chronicle.absorb(cid, {"id": s2, "one_line": "b", "summary": "", "keywords": [],
                           "cast": [], "location": "", "date": "2026-01-02"})
    plot.set_movement(cid, "hot", "Hot thread", "advanced", "just moved", s2)   # advanced in the most recent scene
    plot.set_movement(cid, "cool", "Cool thread", "open", "went quiet", s1)     # last advanced one scene back
    plot.set_movement(cid, "orphan", "Orphan thread", "open", "lost", "ghost-scene")  # last_scene not in chronicle
    threads = {t["id"]: t for t in suggest.build_snapshot(cid)["open_threads"]}
    assert threads["hot"]["dormancy"] == 0     # advanced in the most recent scene
    assert threads["cool"]["dormancy"] == 1    # one scene (s2) has passed since s1
    assert threads["orphan"]["dormancy"] == 2  # unknown last_scene -> maximally cold (len scene_ids)


def test_snapshot_threads_are_canonical(monkeypatch, tmp_path):
    """A merged-away thread is not a second suggestion signal: the snapshot
    lists the canonical alone, still annotated with its dormancy."""
    from grimoire.store.continuity import doc
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "winifreds-chart", "Winifred's chart", "open", "Winifred inks it.", "001--gate")
    plot.set_movement(cid, "maras-map", "Mara's map", "open", "Mara finds it.", "002--causeway")
    doc.put_alias(cid, "thread:maras-map",
                  {"to": "thread:winifreds-chart", "created": "", "source": "manual", "note": ""})
    snap = suggest.build_snapshot(cid)
    assert [t["id"] for t in snap["open_threads"]] == ["winifreds-chart"]
    assert "dormancy" in snap["open_threads"][0]


def test_a_continuity_side_failure_still_lists_the_physical_threads(monkeypatch, tmp_path):
    """Whatever makes the effective projection raise costs the merge (spec
    3.9), never the threads: the snapshot falls back to the physical ledger,
    as the briefing and the advance digest do."""
    from grimoire.store.continuity import effective

    def _boom(*_a, **_k):
        raise RuntimeError("an unanticipated continuity.json shape")

    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "maras-map", "Mara's map", "open", "Mara finds it.", "001--gate")
    monkeypatch.setattr(effective, "threads", _boom)
    snap = suggest.build_snapshot(cid)
    assert [t["id"] for t in snap["open_threads"]] == ["maras-map"]
    assert "dormancy" in snap["open_threads"][0]


def test_build_prompt_includes_signals():
    snap = _snap(
        now="2026-01-01", friendly="Jan 1", holidays_today=["New Year"],
        # A campaign-scheduled event (#101) beside the calendar's holiday:
        # the prompt line carries both, from two different sources.
        events_today=["The envoy arrives"],
        upcoming={"name": "Festival", "in_days": 5},
        birthdays=[{"name": "Ann", "age": 30, "when": "today"}],
        story_so_far=[{"one_line": "They met at the keep.", "location": "The Keep", "date": "2026-01-01"}],
        open_threads=[{"id": "the-map", "title": "The map", "status": "open",
                       "latest_beat": "found it", "dormancy": 2}],
        cast=[{"token": "characters:ann", "name": "Ann", "tagline": "a healer",
               "status": "present", "role": "npc"},
              {"token": "characters:doran", "name": "Doran", "tagline": "a sellsword",
               "status": "unseen", "role": "npc"},
              {"token": "characters:mira", "name": "Mira", "tagline": "an old ally",
               "status": "appeared", "role": "npc"},
              {"token": "pcs:kit", "name": "Kit", "tagline": "",
               "status": "present", "role": "player"}],
        available_locations=[{"id": "keep", "name": "The Keep"}])
    user = suggest.build_prompt(snap)[1]["content"]
    assert "The map" in user and "cold — 2 scenes" in user
    assert "Ann" in user and "a healer" in user
    assert "Doran" in user and "Not yet appeared" in user
    assert "The Keep" in user and "New Year" in user and "today" in user
    # Both halves of the date line: the calendar's holiday and the campaign's
    # own scheduled event (#101), which reach it from different files.
    assert "Scheduled today: The envoy arrives." in user
    assert "They met at the keep." in user
    assert "Appeared earlier, now offstage:" in user and "Mira" in user
    assert "Kit (the player character)" in user


def test_standard_instruction_enforces_presence_and_gender():
    snap = _snap()
    system = suggest.build_prompt(snap)[0]["content"]
    assert "Never assume a character is present" in system
    assert "gender" in system and "reviving" in system


def test_offscreen_instruction_keeps_presence_discipline():
    snap = _snap()
    system = suggest.build_prompt(snap, offscreen=True)[0]["content"]
    assert "OFFSCREEN" in system
    assert "Never include the player character" in system
    assert "Do not assume a character is present" in system


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


def test_build_snapshot_dedupes_cast(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    hero = _char(wroot, "Hero")
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", hero, "main", "player")  # campaign char AND roster player
    cast = suggest.build_snapshot(cid)["cast"]
    tokens = [c["token"] for c in cast]
    assert tokens.count(f"characters:{hero}") == 1                    # listed once, not duplicated
    assert next(c for c in cast if c["token"] == f"characters:{hero}")["role"] == "player"


def test_offscreen_rejects_a_player_seated_as_a_character(monkeypatch, tmp_path):
    """CastPanel's role selector lets a `characters` actor be a player. An
    offscreen scene is defined by the player's absence, whatever kind seats
    them."""
    cid = _campaign_with_player_character(monkeypatch, tmp_path)   # seats characters:mara as role=player
    reply = '{"suggestions": [{"title": "T", "premise": "P", "cast": ["characters:mara"], "location": ""}]}'
    assert suggest.parse_output(reply, cid, offscreen=True)[0]["cast"] == []
    snap = suggest.build_snapshot(cid, offscreen=True)
    assert "characters:mara" not in {c["token"] for c in snap["cast"]}


def test_pc_scene_still_accepts_that_same_player(monkeypatch, tmp_path):
    """The offscreen clause must stay guarded: without the guard it would
    reject players from ordinary PC scenes too."""
    cid = _campaign_with_player_character(monkeypatch, tmp_path)
    reply = '{"suggestions": [{"title": "T", "premise": "P", "cast": ["characters:mara"], "location": ""}]}'
    assert suggest.parse_output(reply, cid, offscreen=False)[0]["cast"] == ["characters:mara"]
    snap = suggest.build_snapshot(cid, offscreen=False)
    assert "characters:mara" in {c["token"] for c in snap["cast"]}


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
    snap = _snap(now="2026-01-01", friendly="Jan 1")
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


# ---- scene intent (#317) ----
def _campaign_with_location_and_character(monkeypatch, tmp_path):
    # a world-level location/character, inherited into the campaign the same
    # way `overlay.list_entities`/`overlay.list_characters` inherit anything
    # not overridden campaign-side.
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "locations", "Saltmarch")
    _char(wroot, "Mara")
    return campaigns.create_campaign("Run", wid)


INTENT_REPLY = ('{"title": "The morning after", "date": "2026-03-04", '
                '"location": "saltmarch", "cast": ["characters:mara"]}')


def test_parse_intent_validates_every_field(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)  # locations/saltmarch, characters/mara
    got = suggest.parse_intent(INTENT_REPLY, cid)
    assert got["title"] == "The morning after"
    assert got["location"] == "saltmarch"
    assert got["cast"] == ["characters:mara"]
    assert got["date"]        # normalized, non-empty


def test_parse_intent_drops_what_the_campaign_does_not_have(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    reply = ('{"title": "T", "date": "the fourth of Never", "location": "atlantis", '
             '"cast": ["characters:nobody", "garbage"]}')
    got = suggest.parse_intent(reply, cid)
    assert got == {"title": "T", "date": "", "location": "", "cast": []}


def test_parse_intent_takes_the_first_object_of_a_bare_array(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    assert suggest.parse_intent(f"[{INTENT_REPLY}]", cid)["title"] == "The morning after"


def test_parse_intent_survives_garbage(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    assert suggest.parse_intent("I'm afraid I can't do that.", cid) == {
        "title": "", "date": "", "location": "", "cast": []}


def test_parse_intent_honors_offscreen(monkeypatch, tmp_path):
    cid = _campaign_with_player_character(monkeypatch, tmp_path)
    reply = '{"title": "T", "date": "", "location": "", "cast": ["characters:mara"]}'
    assert suggest.parse_intent(reply, cid, offscreen=True)["cast"] == []


def test_parse_intent_treats_a_non_string_field_as_missing(monkeypatch, tmp_path):
    # `str(None)` == "None", `str(42)` == "42", `str({...})` == "{...}" -- each
    # a non-empty string that would otherwise read as real model output (e.g.
    # the title "None") instead of falling back to blank/BLANK_TITLE and
    # keeping the empty-intent warning live.
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    for bad in (None, 42, {"nested": "object"}):
        reply = json.dumps({"title": bad, "date": bad, "location": bad, "cast": []})
        got = suggest.parse_intent(reply, cid)
        assert got == {"title": "", "date": "", "location": "", "cast": []}, bad


# ---- direction (#316) ----
def test_direction_reaches_the_prompt():
    snap = _snap()
    msgs = suggest.build_prompt(snap, None, direction="something at sea")
    assert "something at sea" in msgs[1]["content"]
    assert "Direction" in msgs[0]["content"]      # the instruction addendum


def test_no_direction_omits_the_direction_block():
    snap = _snap()
    msgs = suggest.build_prompt(snap, None, direction="")
    assert "Direction" not in msgs[0]["content"]
    assert "Direction" not in msgs[1]["content"]


def test_direction_is_truncated_to_the_limit():
    snap = _snap()
    msgs = suggest.build_prompt(snap, None, direction="x" * 900)
    assert ("x" * suggest.DIRECTION_LIMIT) in msgs[1]["content"]
    assert ("x" * (suggest.DIRECTION_LIMIT + 1)) not in msgs[1]["content"]


# ---- calendar notation: the prompt must show a form the parser accepts ----
def _hebrew_campaign(monkeypatch, tmp_path, now="5786-Kislev-25"):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "hebrew", "region": "", "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)
    clock.advance(cid, to=now, reason="setup")
    return cid


def test_snapshot_carries_the_calendars_own_notation(monkeypatch, tmp_path):
    """Not the friendly form: `example` is what `date_normalizer` accepts."""
    snap = suggest.build_snapshot(_hebrew_campaign(monkeypatch, tmp_path))
    assert snap["friendly"] == "25 Kislev 5786"
    assert snap["notation"]["example"] == "5786-Kislev-25"
    assert snap["notation"]["months"][:3] == ["Tishrei", "Cheshvan", "Kislev"]


def test_snapshot_notation_follows_whatever_calendar_is_configured(monkeypatch, tmp_path):
    """Provider contract only — no calendar is named in the builder."""
    cid = _campaign(monkeypatch, tmp_path)     # gregorian by default
    clock.advance(cid, to="2026-06-29", reason="setup")
    notation = suggest.build_snapshot(cid)["notation"]
    assert notation["example"] == "2026-06-29"
    assert notation["months"][:2] == ["01", "02"]


def test_snapshot_notation_is_blank_without_a_current_date(monkeypatch, tmp_path):
    snap = suggest.build_snapshot(_campaign(monkeypatch, tmp_path))
    assert snap["notation"] == {"example": "", "months": []}


def test_prompt_shows_the_native_form_beside_the_friendly_one(monkeypatch, tmp_path):
    snap = suggest.build_snapshot(_hebrew_campaign(monkeypatch, tmp_path))
    prompt = "\n".join(m["content"] for m in suggest.build_prompt(snap))
    assert "25 Kislev 5786" in prompt        # still readable
    assert "5786-Kislev-25" in prompt        # and now writable
    assert "Adar" in prompt                  # this year's month keys are listed


def test_parse_output_accepts_a_date_written_the_way_the_prompt_displays_it(
        monkeypatch, tmp_path):
    """The Hebrew-calendar miss: a model echoing `friendly` used to lose its date."""
    cid = _hebrew_campaign(monkeypatch, tmp_path)
    text = ('{"suggestions": [{"title": "A", "premise": "P", "cast": [], '
            '"location": "", "date": "2 Tevet 5786"}], "next_date": "2 Tevet 5786"}')
    assert suggest.parse_output(text, cid)[0]["date"] == "5786-Tevet-02"
    assert suggest.parse_next_date(text, cid) == "5786-Tevet-02"


def test_parse_output_still_drops_a_date_no_calendar_could_render(monkeypatch, tmp_path):
    cid = _hebrew_campaign(monkeypatch, tmp_path)
    text = ('{"suggestions": [{"title": "A", "premise": "P", "cast": [], '
            '"location": "", "date": "sometime next winter"}]}')
    assert suggest.parse_output(text, cid)[0]["date"] == ""


def test_parse_intent_accepts_the_friendly_form_too(monkeypatch, tmp_path):
    cid = _hebrew_campaign(monkeypatch, tmp_path)
    assert suggest.parse_intent('{"date": "2 Tevet 5786"}', cid)["date"] == "5786-Tevet-02"


def test_stored_records_are_held_to_the_strict_notation(monkeypatch, tmp_path):
    """Tolerance is for MODEL TEXT, not for the ledger.

    `ref_validator` re-checks records this campaign already wrote, on every
    read of them. Two reasons it stays strict. Correctness: a stored date is
    canonical by construction, so one that no longer parses means the campaign
    changed calendars under it -- and a Gregorian date re-read through a Hebrew
    string matcher is not a date this campaign meant. Cost: the ledger is
    unbounded, and a fuzzy miss walks the whole window, so a calendar switch
    would otherwise buy a full scan per idea on every read."""
    cid = _hebrew_campaign(monkeypatch, tmp_path)
    assert suggest.valid_refs(cid, [], "", "5786-Tevet-02")["date"] == "5786-Tevet-02"
    assert suggest.valid_refs(cid, [], "", "2 Tevet 5786")["date"] == ""
    # ...while the model-output parsers stay tolerant
    assert suggest.parse_intent('{"date": "2 Tevet 5786"}', cid)["date"] == "5786-Tevet-02"


# A user-authored calendar, which is what "any calendar will work" has to mean:
# `_notation` is built from the CalendarProvider contract alone, so a plugin
# gets the format lesson by implementing nothing extra. Deliberately wide --
# more months than the notation hint will list.
_WIDE_PROVIDER_SRC = '''
from grimoire.store.calendars.base import CalendarError, CalendarProvider, register

MONTHS = [f"M{n:02d}" for n in range(1, 41)]      # 40 months of 10 days

class _WideProvider(CalendarProvider):
    def __init__(self, config):
        self.custom_holidays = []

    def parse(self, native):
        try:
            y, m, d = str(native).split("-")
            return int(y) * 400 + MONTHS.index(m) * 10 + int(d) - 1
        except (ValueError, IndexError) as e:
            raise CalendarError(f"bad wide date: {native!r}") from e

    def format(self, fixed):
        y, rest = divmod(fixed, 400)
        m, d = divmod(rest, 10)
        return f"{y}-{MONTHS[m]}-{d + 1:02d}"

    def describe(self, fixed):
        y, rest = divmod(fixed, 400)
        m, d = divmod(rest, 10)
        return {"year": y, "month": m + 1, "month_name": MONTHS[m], "day": d + 1,
                "weekday_name": "Oneday", "weekday_index": 0,
                "friendly": f"{d + 1} {MONTHS[m]} {y}"}

    def holidays(self, start_fixed, end_fixed):
        return []

    def months(self, year):
        return [{"key": k, "name": k, "days": 10} for k in MONTHS]

register("wide-test-calendar", _WideProvider, "Wide Test Calendar")
'''


def _wide_campaign(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plugins = tmp_path / "calendars"
    plugins.mkdir(exist_ok=True)
    (plugins / "wide_test.py").write_text(_WIDE_PROVIDER_SRC, encoding="utf-8")
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "wide-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)
    clock.advance(cid, to="5-M03-07", reason="setup")
    return cid


def test_a_plugin_calendar_gets_the_notation_hint_too(monkeypatch, tmp_path):
    snap = suggest.build_snapshot(_wide_campaign(monkeypatch, tmp_path))
    assert snap["notation"]["example"] == "5-M03-07"
    assert "5-M03-07" in "\n".join(m["content"] for m in suggest.build_prompt(snap))


def test_an_unlistably_wide_month_set_is_dropped_rather_than_trimmed(monkeypatch, tmp_path):
    """A trimmed list reads as a complete one, and would teach the model that
    the months it was not shown do not exist. The example alone is honest."""
    snap = suggest.build_snapshot(_wide_campaign(monkeypatch, tmp_path))
    assert snap["notation"]["months"] == []
    prompt = "\n".join(m["content"] for m in suggest.build_prompt(snap))
    assert "M01" not in prompt and "months, in order" not in prompt


def test_a_plugin_calendars_own_friendly_form_resolves_too(monkeypatch, tmp_path):
    """The tolerant parser is contract-only as well: no calendar is named in it."""
    cid = _wide_campaign(monkeypatch, tmp_path)
    text = ('{"suggestions": [{"title": "A", "premise": "P", "cast": [], '
            '"location": "", "date": "9 M03 5"}]}')
    assert suggest.parse_output(text, cid)[0]["date"] == "5-M03-09"


_NO_MONTHS_SRC = _WIDE_PROVIDER_SRC.replace(
    'register("wide-test-calendar", _WideProvider, "Wide Test Calendar")',
    '''
class _NoMonthsProvider(_WideProvider):
    def months(self, year):
        raise CalendarError("this calendar will not enumerate its months")

register("wide-test-calendar", _WideProvider, "Wide Test Calendar")
register("no-months-test-calendar", _NoMonthsProvider, "No Months Test Calendar")
''')


def test_a_broken_months_costs_the_month_list_and_not_the_example(monkeypatch, tmp_path):
    """The two halves of the hint are independent. The example is the half that
    actually teaches the notation, so a provider that will not enumerate its
    months must not take it down with them."""
    cid = _campaign(monkeypatch, tmp_path)
    plugins = tmp_path / "calendars"
    plugins.mkdir(exist_ok=True)
    (plugins / "no_months_test.py").write_text(_NO_MONTHS_SRC, encoding="utf-8")
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "no-months-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)
    clock.advance(cid, to="5-M03-07", reason="setup")

    assert suggest.build_snapshot(cid)["notation"] == {"example": "5-M03-07", "months": []}


def test_greeting_candidates_do_not_rank_past_bounded_recommendations(monkeypatch):
    rows = [
        {"id": "next-a", "name": "Next A", "available": True, "pcless": False,
         "recommendation": "successor"},
        {"id": "next-b", "name": "Next B", "available": True, "pcless": False,
         "recommendation": "phase_optional"},
        {"id": "later-a", "name": "Later A", "available": True, "pcless": False,
         "recommendation": None},
        {"id": "later-b", "name": "Later B", "available": True, "pcless": False,
         "recommendation": None},
    ]
    monkeypatch.setattr(playing, "available_greetings", lambda *args, **kwargs: rows)
    monkeypatch.setattr(overlay, "read_greeting", lambda *args: {"body": "Opening."})

    assert suggest.greeting_candidates("run", after="scene") == []


# ---- the driver capture (continuity capstone, Slice E) ----------------------

#: The intent prompt's snapshot keys: what `build_snapshot(drivers=False)`
#: returns, exactly.
TODAY_KEYS = {"now", "friendly", "notation", "holidays_today", "events_today", "upcoming",
              "birthdays", "story_so_far", "open_threads", "cast", "available_locations"}

MAP = "thread:mara-s-map"
OATH = "commitment:mara-s-oath"

#: A plugin whose constructor raises something that is not a CalendarError
#: (copied from Slice B's `test_continuity_pressure`). Every abstract method is
#: defined, so the RuntimeError -- not an abstract-class TypeError -- is what
#: reaches the caller.
_BROKEN_PROVIDER_SRC = textwrap.dedent(
    """
    from grimoire.store.calendars.base import CalendarProvider, register

    class _BrokenProvider(CalendarProvider):
        def __init__(self, config):
            raise RuntimeError("this calendar plugin is broken")

        def parse(self, native):
            return 0

        def format(self, fixed):
            return ""

        def describe(self, fixed):
            return {}

        def holidays(self, start_fixed, end_fixed):
            return []

        def months(self, year):
            return []

    register("broken-test-calendar", _BrokenProvider, "Broken Test Calendar")
    """
)


def _rule(name, month, day):
    return {"name": name, "month": month, "day": day}


def _pressure_campaign(monkeypatch, tmp_path, *, calendar="gregorian", now="2026-05-10",
                       holidays=(), secondary=None):
    """A campaign whose calendar observes exactly `holidays` (`region=""`
    switches the Gregorian holiday library off), its clock at `now`."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, calendar=calendar)
    root = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {**cfg["primary"], "region": "", "custom_holidays": list(holidays)}
    if secondary is not None:
        cfg["secondary"] = secondary
    calendars.write_calendar(root, cfg)
    if now is not None:
        clock.advance(cid, to=now)
    return cid


def _map(cid, scene="001--gate"):
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                      "The map turned up in Saltmarch.", scene)


def _oath(cid, due="2026-05-14", scene="001--gate"):
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open", due,
                             "Mara swore it at the gate.", scene)


def _recorder(monkeypatch, module, name):
    """Replace `module.name` with a delegating call counter."""
    real, calls = getattr(module, name), []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)
    monkeypatch.setattr(module, name, record)
    return calls


def test_snapshot_has_ids_commitments_timeline_and_index(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    eid = events.create(cid, "The coronation", "2026-05-13")

    snap = suggest.build_snapshot(cid)
    assert [t["ref"] for t in snap["open_threads"]] == [MAP]
    oath = snap["commitments"][0]
    assert oath["ref"] == OATH
    assert {"id", "kind", "due", "latest_beat", "dormancy"} <= set(oath)
    assert (oath["id"], oath["kind"], oath["due"]) == ("mara-s-oath", "promise", "2026-05-14")
    assert oath["pressure"]["state"] == "due_soon"
    assert oath["pressure"]["in_days"] == 4
    timeline = {(i["kind"], i["ref"]) for i in snap["timeline"]}
    assert ("event", f"event:{eid}") in timeline
    assert ("deadline", OATH) in timeline
    assert {MAP, OATH, f"event:{eid}"} <= {d["ref"] for d in snap["driver_index"]}


@pytest.mark.parametrize(("warn", "near"), [(30, 30), (3, suggest.NEAR_MIN_DAYS)])
def test_near_days_is_the_wider_of_warn_days_and_the_floor(monkeypatch, tmp_path, warn, near):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["warn_days"] = warn
    calendars.write_calendar(croot, cfg)
    snap = suggest.build_snapshot(cid)
    assert snap["near_days"] == near
    gregorian = calendars.get_provider({"provider": "gregorian"})
    assert snap["fixed"] == calendars.fixed_of(gregorian, "2026-05-10")


def test_a_garbled_calendar_json_still_gets_the_near_floor(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "calendar.json").write_text("{oops", encoding="utf-8")
    assert suggest.build_snapshot(cid)["near_days"] == suggest.NEAR_MIN_DAYS == 7


def test_unchanged_keys_are_unchanged(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 10)])
    sid = scenes.create_scene(cid, "One")
    chronicle.absorb(cid, {"id": sid, "one_line": "Mara reached Saltmarch.", "summary": "",
                           "keywords": [], "cast": [], "location": "Saltmarch",
                           "date": "2026-05-09"})
    overlay.create_entity(cid, "locations", "Saltmarch")
    aid, _ = overlay.create_character(cid, "Mara")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "1985-05-12")
    events.create(cid, "The coronation", "2026-05-10")
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "The map turned up.", sid)
    _oath(cid)

    a, b = suggest.build_snapshot(cid), suggest.build_snapshot(cid, drivers=False)
    unchanged = ("now", "friendly", "notation", "holidays_today", "events_today", "birthdays",
                 "story_so_far", "cast", "available_locations")
    for k in unchanged:
        assert b[k], k   # a comparison between blanks would prove nothing
    assert {k: a[k] for k in unchanged} == {k: b[k] for k in unchanged}
    assert len(a["open_threads"]) == len(b["open_threads"]) == 1
    for row_a, row_b in zip(a["open_threads"], b["open_threads"], strict=True):
        assert {k: row_a[k] for k in row_b} == row_b


def test_intent_snapshot_does_no_driver_work(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    events.create(cid, "The coronation", "2026-05-13")
    # Recorders that delegate, not raisers: the new reads sit inside broad
    # catches, which would swallow an AssertionError and pass.
    builds = _recorder(monkeypatch, pressure, "build")
    snaps = _recorder(monkeypatch, continuity_drivers, "snapshot")
    assert set(suggest.build_snapshot(cid, drivers=False)) == TODAY_KEYS
    assert (len(builds), len(snaps)) == (0, 0)


def test_timeline_and_index_share_one_pressure_read(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    events.create(cid, "The coronation", "2026-05-13")
    builds = _recorder(monkeypatch, pressure, "build")
    snaps = _recorder(monkeypatch, continuity_drivers, "snapshot")
    snap = suggest.build_snapshot(cid)
    assert len(builds) == 1
    assert len(snaps) == 1
    assert snap["timeline"] and snap["driver_index"]


_HEBREW = {"provider": "hebrew", "region": "", "custom_holidays": [], "anchor": None}


@pytest.mark.parametrize(("now", "holidays", "secondary", "event"), [
    ("2026-05-10", [], None, "2026-05-13"),
    ("2026-05-10", [_rule("Saltmarch Eve", "05", 12)], None, None),
    ("2026-05-10", [_rule("Saltmarch Eve", "05", 12)], None, "2026-05-12"),
    ("2026-09-01", [], _HEBREW, None),
], ids=["event", "holiday", "event-and-holiday", "secondary-holiday"])
def test_timeline_contains_what_sooner_picks(monkeypatch, tmp_path, now, holidays,
                                             secondary, event):
    cid = _pressure_campaign(monkeypatch, tmp_path, now=now, holidays=holidays,
                             secondary=secondary)
    if event:
        events.create(cid, "The coronation", event)
    pick = suggest.build_snapshot(cid, drivers=False)["upcoming"]
    assert pick is not None
    snap = suggest.build_snapshot(cid)
    [match, *_] = [i for i in snap["timeline"]
                   if i["label"] == pick["name"] and i["in_days"] == pick["in_days"]]
    assert match["ref"] == snap["sooner_ref"]
    if secondary is not None:
        # the pick is the secondary calendar's: no primary observance exists
        assert match["kind"] == "holiday"


def test_no_pick_means_no_sooner_ref(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    assert suggest.build_snapshot(cid, drivers=False)["upcoming"] is None
    assert suggest.build_snapshot(cid)["sooner_ref"] == ""


def test_links_among_drivers(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    plot.set_movement(cid, "winifred-s-chart", "Winifred's chart", "closed",
                      "The chart was burned.", "001--gate")
    continuity_doc.put_link(cid, "l-1", {"a": MAP, "b": OATH, "relation": "pays_off",
                                         "created": "", "scene": "", "note": ""})
    continuity_doc.put_link(cid, "l-2", {"a": "thread:winifred-s-chart", "b": OATH,
                                         "relation": "pays_off", "created": "", "scene": "",
                                         "note": ""})
    snap = suggest.build_snapshot(cid)
    assert "thread:winifred-s-chart" not in {d["ref"] for d in snap["driver_index"]}
    assert snap["links"] == [{"id": "l-1", "a": MAP, "b": OATH, "relation": "pays_off"}]


def test_commitment_dormancy_counts_scenes(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    sids = [scenes.create_scene(cid, name) for name in ("One", "Two", "Three")]
    for sid in sids:
        chronicle.absorb(cid, {"id": sid, "one_line": "", "summary": "", "keywords": [],
                               "cast": [], "location": "", "date": "2026-05-09"})
    _oath(cid, scene=sids[0])
    eid = events.create(cid, "The coronation", "2026-05-13")
    snap = suggest.build_snapshot(cid)
    assert snap["commitments"][0]["dormancy"] == 2
    index = {d["ref"]: d for d in snap["driver_index"]}
    assert index[OATH]["dormancy"] == 2
    assert index[f"event:{eid}"]["dormancy"] is None


def test_snapshot_survives_a_raising_plugin(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    eid = events.create(cid, "The coronation", "2026-05-13")
    plugins = tmp_path / "calendars"
    plugins.mkdir(exist_ok=True)
    (plugins / "broken_test.py").write_text(_BROKEN_PROVIDER_SRC, encoding="utf-8")
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "broken-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)

    legacy = suggest.build_snapshot(cid, drivers=False)
    snap = suggest.build_snapshot(cid)
    for s in (legacy, snap):
        assert s["notation"] == {"example": "", "months": []}
        assert (s["friendly"], s["holidays_today"], s["events_today"], s["birthdays"]) == (
            "", [], [], [])
    assert snap["timeline"] and all(i["fixed"] is None for i in snap["timeline"])
    assert [i["ref"] for i in snap["timeline"]] == [f"event:{eid}"]
    assert snap["anchors"] == []
    assert snap["fixed"] is None
    assert {MAP, OATH} <= {d["ref"] for d in snap["driver_index"]}


def test_offscreen_keeps_a_pc_birthday_driver_but_never_its_cast_token(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    root = campaigns.campaign_root(cid)
    wid, vid = pcs.create_pc(root, "Winifred", [], persona={
        **pcs.blank_persona("Winifred"), "birthdate": "--05-12"})
    sid = scenes.create_scene(cid, "One")
    appearances.appear(cid, sid, "pcs", wid, vid, "player")

    snap = suggest.build_snapshot(cid, offscreen=True)
    births = [d for d in snap["driver_index"] if d["ref"].startswith(f"birthday:pcs:{wid}:")]
    assert len(births) == 1
    assert births[0]["dormancy"] is None
    assert f"pcs:{wid}" not in {c["token"] for c in snap["cast"]}


# ---- Task 3: the prompt renders the driver capture ----
DRIVER_KEYS = {"commitments", "timeline", "driver_index", "anchors", "links", "fixed",
               "near_days", "sooner_ref"}

#: Global Constraints' drivers-addendum sentence needles (§15.1). Pinned here
#: only; a reword edits the plan's list and the template together.
DRIVERS_ADDENDUM_NEEDLES = (
    'Each suggestion also includes key "drivers"',
    '"time_anchor"',
    "High pressure means a driver whose state is",
    "Not every suggestion should serve the same drivers",
    "At least one suggestion should address a high-pressure driver, if any exists",
    "Near-future temporal anchors are worth using",
    "A quiet character or relationship scene is welcome when nothing urgent dominates",
)


def _store_campaign(monkeypatch, tmp_path):
    """Mara's map, Mara's oath (due 2026-05-14) and the coronation on
    2026-05-13, the clock at 2026-05-10."""
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    eid = events.create(cid, "The coronation", "2026-05-13")
    return cid, f"event:{eid}"


def test_prompt_renders_refs_commitments_timeline_and_index(monkeypatch, tmp_path):
    cid, _event = _store_campaign(monkeypatch, tmp_path)
    user = suggest.build_prompt(suggest.build_snapshot(cid))[1]["content"]
    assert "- thread:mara-s-map = Mara's map (" in user
    assert "commitment:mara-s-oath: Mara's oath (promise, open), due 2026-05-14" in user
    assert "Timeline (computed from the calendar" in user
    assert "event:the-coronation = The coronation" in user
    assert "Story drivers (cite these refs" in user
    assert "Upcoming:" not in user


def test_intent_prompt_still_has_its_upcoming_line(monkeypatch, tmp_path):
    cid, _event = _store_campaign(monkeypatch, tmp_path)
    user = suggest.build_intent_prompt(cid, "x")[1]["content"]
    assert "Upcoming: The coronation in 3 days." in user
    assert "Story drivers" not in user and "thread:mara-s-map" not in user


def test_intent_prompt_does_no_driver_work(monkeypatch, tmp_path):
    cid, _event = _store_campaign(monkeypatch, tmp_path)
    # Recorders that delegate, not raisers: the new reads sit inside broad
    # catches, which would swallow an AssertionError and pass.
    builds = _recorder(monkeypatch, pressure, "build")
    snaps = _recorder(monkeypatch, continuity_drivers, "snapshot")
    assert suggest.build_intent_prompt(cid, "x")
    assert (len(builds), len(snaps)) == (0, 0)


def test_snapshot_drops_only_upcoming(monkeypatch, tmp_path):
    cid, _event = _store_campaign(monkeypatch, tmp_path)
    legacy = set(suggest.build_snapshot(cid, drivers=False))
    assert "upcoming" in legacy
    assert set(suggest.build_snapshot(cid)) == (legacy - {"upcoming"}) | DRIVER_KEYS


def test_drivers_addendum_names_every_action_and_high_pressure(monkeypatch, tmp_path):
    cid, _event = _store_campaign(monkeypatch, tmp_path)
    system = suggest.build_prompt(suggest.build_snapshot(cid))[0]["content"]
    for action in continuity_drivers.DRIVER_ACTIONS:
        assert f'"{action}"' in system, action
    for needle in DRIVERS_ADDENDUM_NEEDLES:
        assert needle in system, needle
    assert 'High pressure means a driver whose state is "overdue", "today" or "due_soon"' \
        in system


def test_controls_addendum_needles(monkeypatch, tmp_path):
    cid, event = _store_campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)

    def system(controls):
        return suggest.build_prompt(snap, controls=controls)[0]["content"]

    plain = system(None)
    for needle in ("Focus drivers:", "Avoid drivers:", "Must-include drivers:",
                   "between the current date and", "later than the current date",
                   "Anchor every suggestion to"):
        assert needle not in plain, needle

    near = system(suggest.Controls(time_mode="near"))
    assert "between the current date and" in near and "7 days" in near
    assert "later than the current date" in system(suggest.Controls(time_mode="move"))
    anchored = system(suggest.Controls(time_mode="anchor", anchor=event, relation="before"))
    assert "Anchor every suggestion to" in anchored
    assert f"{event} (The coronation" in anchored and '"before"' in anchored

    steered = system(suggest.Controls(focus=(MAP,), avoid=(event,), must=(OATH,)))
    assert f"Focus drivers: {MAP} = Mara's map" in steered
    assert "Spread the focus drivers across the suggestions" in steered
    assert f"Avoid drivers: {event} = The coronation" in steered
    assert f"Must-include drivers: {OATH} = Mara's oath" in steered
    assert "Every suggestion must serve each must-include driver" in steered


@pytest.mark.parametrize("mode", ["near", "move"])
def test_near_and_move_ask_for_no_date_in_an_undated_campaign(monkeypatch, tmp_path, mode):
    """§26: with no current date the prompt never asks for a "date" (the date
    addendum, which defines that key, renders only under `s.now`), so near and
    move render no sentence rather than one measured from a date that does
    not exist."""
    cid = _pressure_campaign(monkeypatch, tmp_path, now=None)
    _map(cid)
    _oath(cid)
    snap = suggest.build_snapshot(cid)
    assert snap["now"] == ""
    system = suggest.build_prompt(snap, controls=suggest.Controls(time_mode=mode))[0]["content"]
    assert '"date"' not in system
    assert system == suggest.build_prompt(snap)[0]["content"]


def test_an_empty_campaign_keeps_todays_system_message(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    system = suggest.build_prompt(suggest.build_snapshot(cid))[0]["content"]
    assert system == suggest.build_prompt(_snap())[0]["content"]
    assert 'key "drivers"' not in system


# ---- Task 4: parsing against the captured snapshot ----
def _reply(*suggestions, next_date=None) -> str:
    body: dict = {"suggestions": [{"title": f"T{i}", "premise": "P", "cast": [],
                                   "location": "", **s} for i, s in enumerate(suggestions)]}
    if next_date is not None:
        body["next_date"] = next_date
    return json.dumps(body)


def _break_calendar(cid, tmp_path):
    """Point the campaign's primary calendar at a plugin whose constructor
    raises RuntimeError -- not CalendarError, which today's guards absorb."""
    plugins = tmp_path / "calendars"
    plugins.mkdir(exist_ok=True)
    (plugins / "broken_test.py").write_text(_BROKEN_PROVIDER_SRC, encoding="utf-8")
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "broken-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)


def test_parse_output_resolves_labels_and_dates(monkeypatch, tmp_path):
    cid, event = _store_campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)
    controls = suggest.Controls(time_mode="anchor", anchor=event, relation="before")
    text = _reply({"date": "2026-05-12", "drivers": [{"ref": MAP, "action": "advance"}]})
    [row] = suggest.parse_output(text, cid, snapshot=snap, controls=controls)
    assert row["time_anchor"] == {"ref": "event:the-coronation", "kind": "event",
                                  "relation": "before", "label": "The coronation",
                                  "friendly": "13 May 2026", "in_days": 3}
    assert row["drivers"] == [
        {"ref": MAP, "kind": "thread", "action": "advance", "label": "Mara's map"},
        {"ref": event, "kind": "event", "action": "anchor", "label": "The coronation"}]
    assert (row["date"], row["date_friendly"], row["in_days"], row["date_rejected"]) == (
        "2026-05-12", "12 May 2026", 2, False)
    assert (row["unmet_must"], row["avoided"]) == ([], [])
    assert set(row) == {"title", "premise", "cast", "location", "date", "date_friendly",
                        "in_days", "date_rejected", "date_rejected_by", "drivers",
                        "time_anchor", "unmet_must", "avoided"}


def test_plugin_calendar_dates_derive_in_native_notation(monkeypatch, tmp_path):
    # the wide test calendar's months have ten days, so the brief's "5-M03-12"
    # would be read as 5-M04-02; the tenth is the same check inside the month
    cid = _wide_campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The coronation", "5-M03-10")
    snap = suggest.build_snapshot(cid)
    assert f"event:{eid}" in {a["ref"] for a in snap["anchors"]}

    def parsed(relation: str, date: str) -> dict:
        controls = suggest.Controls(time_mode="anchor", anchor=f"event:{eid}",
                                    relation=relation)
        return suggest.parse_output(_reply({"date": date}), cid, snapshot=snap,
                                    controls=controls)[0]

    on = parsed("on", "")
    assert (on["date"], on["date_rejected"], on["in_days"]) == ("5-M03-10", False, 3)
    assert on["date_friendly"] == "10 M03 5"
    before = parsed("before", "9 M03 5")
    assert (before["date"], before["date_rejected"]) == ("5-M03-09", False)


def test_parse_output_without_a_snapshot_keeps_todays_fields(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    text = _reply({"date": "2026-05-12", "drivers": [{"ref": MAP, "action": "advance"}],
                   "time_anchor": {"ref": "event:the-coronation", "relation": "on"}},
                  {"date": "2026-04-01"})
    rows = suggest.parse_output(text, cid)
    assert [r["date"] for r in rows] == ["2026-05-12", "2026-04-01"]
    for row in rows:
        assert row["drivers"] == [] and row["time_anchor"] is None
        assert row["date_rejected"] is False
        assert (row["unmet_must"], row["avoided"]) == ([], [])
    assert (rows[0]["date_friendly"], rows[0]["in_days"]) == ("12 May 2026", 2)
    assert rows[1]["in_days"] == -39


def test_next_date_is_checked_under_near_only(monkeypatch, tmp_path):
    cid, event = _store_campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)
    text = _reply(next_date="2026-05-30")    # 20 days out; near is 7
    assert suggest.parse_next_date(text, cid, snapshot=snap,
                                   controls=suggest.Controls(time_mode="near")) == ""
    anchored = suggest.Controls(time_mode="anchor", anchor=event, relation="before")
    assert suggest.parse_next_date(text, cid, snapshot=snap, controls=anchored) == "2026-05-30"
    assert suggest.parse_next_date(text, cid, snapshot=snap) == "2026-05-30"
    assert suggest.parse_next_date(text, cid) == "2026-05-30"
    past = _reply(next_date="2026-05-10")
    assert suggest.parse_next_date(past, cid, snapshot=snap,
                                   controls=suggest.Controls(time_mode="move")) == ""


def test_month_only_on_against_a_real_campaign(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    aid, _ = overlay.create_character(cid, "Winifred")
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "--06")
    snap = suggest.build_snapshot(cid)
    [ref] = [a["ref"] for a in snap["anchors"] if a["ref"].startswith(f"birthday:characters:{aid}:")]
    assert suggest._month_of(ref) == (2026, "06")
    controls = suggest.Controls(time_mode="anchor", anchor=ref, relation="on")
    rows = suggest.parse_output(_reply({"date": "2026-06-20"}, {"date": "2026-07-01"}), cid,
                                snapshot=snap, controls=controls)
    assert [(r["date"], r["date_rejected"]) for r in rows] == [
        ("2026-06-20", False), ("", True)]
    assert rows[0]["time_anchor"]["relation"] == "on"


#: A plugin that constructs fine but whose `parse` is written the ordinary way
#: -- split on "-" and int() the parts -- so model text it cannot read raises
#: ValueError rather than CalendarError.
_NAIVE_PROVIDER_SRC = _WIDE_PROVIDER_SRC.replace(
    """        try:
            y, m, d = str(native).split("-")
            return int(y) * 400 + MONTHS.index(m) * 10 + int(d) - 1
        except (ValueError, IndexError) as e:
            raise CalendarError(f"bad wide date: {native!r}") from e
""",
    """        y, m, d = str(native).split("-")
        return int(y) * 400 + MONTHS.index(m) * 10 + int(d) - 1
""").replace('"wide-test-calendar", _WideProvider, "Wide Test Calendar"',
             '"naive-test-calendar", _WideProvider, "Naive Test Calendar"')


def test_a_plugin_parse_raising_on_model_text_is_no_date(monkeypatch, tmp_path):
    assert "CalendarError(" not in _NAIVE_PROVIDER_SRC.split("def parse", 1)[1].split("def ", 1)[0]
    cid = _campaign(monkeypatch, tmp_path)
    plugins = tmp_path / "calendars"
    plugins.mkdir(exist_ok=True)
    (plugins / "naive_test.py").write_text(_NAIVE_PROVIDER_SRC, encoding="utf-8")
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    cfg["primary"] = {"provider": "naive-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(croot, cfg)
    clock.advance(cid, to="5-M03-07", reason="setup")
    snap = suggest.build_snapshot(cid)
    text = _reply({"date": "next week"}, {"date": "5-M03-09"}, next_date="next week")
    for kwargs in ({}, {"snapshot": snap}):
        rows = suggest.parse_output(text, cid, **kwargs)
        assert [(r["date"], r["date_rejected"]) for r in rows] == [
            ("", False), ("5-M03-09", False)], kwargs
        assert suggest.parse_next_date(text, cid, **kwargs) == "", kwargs


def test_parse_survives_a_raising_plugin(monkeypatch, tmp_path):
    cid = _pressure_campaign(monkeypatch, tmp_path)
    _map(cid)
    _oath(cid)
    events.create(cid, "The coronation", "2026-05-13")
    _break_calendar(cid, tmp_path)
    snap = suggest.build_snapshot(cid)
    text = _reply({"date": "2026-05-12", "drivers": [{"ref": MAP, "action": "advance"}]},
                  {"date": "2026-05-20"}, next_date="2026-05-14")
    for kwargs in ({}, {"snapshot": snap, "controls": suggest.Controls(focus=(MAP,))}):
        rows = suggest.parse_output(text, cid, **kwargs)
        assert [r["title"] for r in rows] == ["T0", "T1"], kwargs
        assert all(r["date"] == "" and r["date_rejected"] is False for r in rows), kwargs
        assert suggest.parse_next_date(text, cid, **kwargs) == ""
    intent = suggest.parse_intent('{"title": "A", "date": "2026-05-12"}', cid)
    assert (intent["title"], intent["date"]) == ("A", "")
    assert suggest.ref_validator(cid)([], "", "2026-05-12")["date"] == ""
