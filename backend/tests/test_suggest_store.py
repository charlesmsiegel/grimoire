import json

from grimoire.store import (appearances, calendars, campaigns, characters, chronicle, clock,
                            entities,
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


def test_build_prompt_includes_signals():
    snap = {"now": "2026-01-01", "friendly": "Jan 1",
            "notation": {"example": "", "months": []}, "holidays_today": ["New Year"],
            # A campaign-scheduled event (#101) beside the calendar's holiday:
            # the prompt line carries both, from two different sources.
            "events_today": ["The envoy arrives"],
            "upcoming": {"name": "Festival", "in_days": 5},
            "birthdays": [{"name": "Ann", "age": 30, "when": "today"}],
            "story_so_far": [{"one_line": "They met at the keep.", "location": "The Keep", "date": "2026-01-01"}],
            "open_threads": [{"id": "the-map", "title": "The map", "status": "open",
                              "latest_beat": "found it", "dormancy": 2}],
            "cast": [{"token": "characters:ann", "name": "Ann", "tagline": "a healer",
                      "status": "present", "role": "npc"},
                     {"token": "characters:doran", "name": "Doran", "tagline": "a sellsword",
                      "status": "unseen", "role": "npc"},
                     {"token": "characters:mira", "name": "Mira", "tagline": "an old ally",
                      "status": "appeared", "role": "npc"},
                     {"token": "pcs:kit", "name": "Kit", "tagline": "",
                      "status": "present", "role": "player"}],
            "available_locations": [{"id": "keep", "name": "The Keep"}]}
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
    snap = {"now": "", "friendly": "",
            "notation": {"example": "", "months": []}, "holidays_today": [], "events_today": [], "upcoming": None, "birthdays": [],
            "story_so_far": [], "open_threads": [], "cast": [], "available_locations": []}
    system = suggest.build_prompt(snap)[0]["content"]
    assert "Never assume a character is present" in system
    assert "gender" in system and "reviving" in system


def test_offscreen_instruction_keeps_presence_discipline():
    snap = {"now": "", "friendly": "",
            "notation": {"example": "", "months": []}, "holidays_today": [], "events_today": [], "upcoming": None, "birthdays": [],
            "story_so_far": [], "open_threads": [], "cast": [], "available_locations": []}
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
    snap = {"now": "2026-01-01", "friendly": "Jan 1",
            "notation": {"example": "", "months": []}, "holidays_today": [], "events_today": [], "upcoming": None,
            "birthdays": [], "open_threads": [], "story_so_far": [],
            "cast": [], "available_locations": []}
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
    snap = {"now": "", "friendly": "",
            "notation": {"example": "", "months": []}, "holidays_today": [], "events_today": [], "upcoming": None,
            "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
            "available_locations": []}
    msgs = suggest.build_prompt(snap, None, direction="something at sea")
    assert "something at sea" in msgs[1]["content"]
    assert "Direction" in msgs[0]["content"]      # the instruction addendum


def test_no_direction_omits_the_direction_block():
    snap = {"now": "", "friendly": "",
            "notation": {"example": "", "months": []}, "holidays_today": [], "events_today": [], "upcoming": None,
            "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
            "available_locations": []}
    msgs = suggest.build_prompt(snap, None, direction="")
    assert "Direction" not in msgs[0]["content"]
    assert "Direction" not in msgs[1]["content"]


def test_direction_is_truncated_to_the_limit():
    snap = {"now": "", "friendly": "",
            "notation": {"example": "", "months": []}, "holidays_today": [], "events_today": [], "upcoming": None,
            "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
            "available_locations": []}
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
