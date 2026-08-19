"""Scenario-card import (#217): extraction, the review payload, and the write.

The three halves are tested apart because they fail apart. `parse_output` is
about what a model can send; `proposal` is about what the reviewer is shown, and
is pure — nothing on disk moves; `apply` is the only thing here that writes, and
what it writes has to line up with what the proposal promised.
"""

import json

import pytest
from grimoire.store import assets, characters, entities, greetings, lorebook, scenario, worlds

CARD = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "Saltmarch",
        "description": "A drowned town where Mara keeps the tide-gate and Winifred runs the market.",
        "personality": "Wary, tidal, slow to trust outsiders.",
        "scenario": "The gate has not opened in nine days.",
        "first_mes": "Mara is waiting at the tide-gate when you arrive.",
        "alternate_greetings": [
            "Winifred looks up from the market ledger. Mara is nowhere in sight.",
            "The square is empty.",
        ],
        "character_book": {"entries": [
            {"keys": ["gate", "tide-gate"], "name": "The Tide-Gate",
             "content": "Iron and barnacle, three men wide.", "enabled": True},
            {"keys": ["market"], "name": "The Market", "content": "Six stalls and a scale.",
             "enabled": True},
        ]},
    },
}

EXTRACTED = {
    "characters": [
        {"name": "Winifred", "description": "Keeps the market's books.", "personality": "Dry."},
        {"name": "Mara", "description": "Tends the tide-gate.", "personality": "Watchful."},
    ],
    "entries": [
        {"name": "The Tide-Gate", "keys": [], "body": "", "category": "locations"},
        {"name": "The Drowned Guild", "keys": ["guild"], "body": "They hold the gate's key.",
         "category": "groups"},
    ],
}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return wid, worlds.world_root(wid)


# ---------------------------------------------------------------- extraction
def test_the_prompt_carries_the_cards_prose_its_world_info_and_its_openers():
    system, user = scenario.build_prompt(CARD)
    assert system["role"] == "system" and user["role"] == "user"
    text = user["content"]
    assert "Saltmarch" in text
    assert "A drowned town where Mara keeps the tide-gate" in text
    assert "The gate has not opened in nine days." in text
    assert "The Tide-Gate" in text and "tide-gate" in text        # entry name + its keys
    assert "Mara is waiting at the tide-gate" in text             # first_mes
    assert "Winifred looks up from the market ledger" in text     # an alternate greeting


def test_an_absent_field_contributes_no_heading():
    bare = {"data": {"name": "Saltmarch", "description": "A drowned town."}}
    text = scenario.build_prompt(bare)[1]["content"]
    assert "Description:" in text
    assert "Creator notes:" not in text
    assert "Existing entries:" not in text
    assert "Scene openers:" not in text


def test_the_openers_reach_the_prompt_without_their_art():
    """A card that embeds its art sends megabytes of base64 per opener, and the
    bytes say nothing about the cast. The reference goes; the prose stays."""
    blob = "data:image/png;base64," + "A" * 4000
    card = {"data": {"name": "Saltmarch",
                     "first_mes": f"![]({blob})\n\nMara waits at the gate."}}
    text = scenario.build_prompt(card)[1]["content"]
    assert "Mara waits at the gate." in text
    assert "base64" not in text and "AAAA" not in text


def test_the_prompt_bounds_what_one_entry_or_opener_can_cost():
    """The cards this exists for are the big ones — a whole setting, dozens of
    world-info entries, a dozen illustrated openers. Bodies are the dominant
    term and the model needs none of them whole: an entry it re-files by name,
    and an opener it reads for who is in it. So each is clipped, visibly."""
    card = {"data": {
        "name": "Saltmarch",
        "first_mes": "Mara waits. " + "x" * 20_000,
        "character_book": {"entries": [
            {"keys": [], "name": "Long", "content": "The gate. " + "y" * 20_000,
             "enabled": True}]},
    }}
    text = scenario.build_prompt(card)[1]["content"]
    assert len(text) < 5_000
    assert "Mara waits." in text and "The gate." in text     # the head of each survives
    assert "…" in text                                        # ...and says it was clipped
    # The clip is the PROMPT's, never the import's: what gets written is the
    # card's own text, whole.
    assert len(scenario.proposal(card, {"characters": [], "entries": []})
               ["greetings"][0]["body"]) > 20_000
    assert len(scenario.proposal(card, {"characters": [], "entries": []})
               ["entries"][0]["body"]) > 20_000


def test_a_proposal_never_offers_a_cast_name_its_openers_cannot_match():
    """The two halves of a proposal reference each other by name, so the names
    have to be spelled one way. A cast row saying " Mara " while its opener says
    "Mara" is a picker whose value is not in its own option list."""
    prop = scenario.proposal(CARD, {"characters": [
        {"name": "  Mara  ", "description": "d", "personality": "p"},
        {"name": "   ", "description": "", "personality": ""},
    ], "entries": []})
    assert [c["name"] for c in prop["characters"]] == ["Mara"]   # blank row dropped
    assert prop["greetings"][0]["character"] == "Mara"
    for g in prop["greetings"]:
        assert g["character"] in {"", *[c["name"] for c in prop["characters"]]}


def test_a_cast_member_proposed_twice_appears_once():
    """A proposal wired on names cannot represent two people who share one:
    `apply` would resolve both rows to a single character and report the second
    as one it found already there."""
    prop = scenario.proposal(CARD, {"characters": [
        {"name": "Mara", "description": "Tends the tide-gate.", "personality": "Watchful."},
        {"name": " mara ", "description": "A second guess.", "personality": ""},
    ], "entries": []})
    assert [c["name"] for c in prop["characters"]] == ["Mara"]
    assert prop["characters"][0]["description"] == "Tends the tide-gate."   # the fuller one


def test_strip_images_drops_every_reference_shape_localize_knows():
    body = ('![a](https://example.com/one.png) prose '
            '<img src="https://example.com/two.png"> more '
            'https://example.com/three.png')
    out = scenario.strip_images(body)
    assert "example.com" not in out
    assert "prose" in out and "more" in out


def test_parse_output_keeps_only_the_two_sections_and_their_fields():
    got = scenario.parse_output(json.dumps({
        "characters": [{"name": "Mara", "description": "d", "personality": "p", "sneaky": 1}],
        "entries": [{"name": "The Market", "keys": ["market"], "body": "b", "category": "locations"}],
        "invented_section": [{"name": "no"}],
    }))
    assert set(got) == {"characters", "entries"}
    assert got["characters"] == [{"name": "Mara", "description": "d", "personality": "p"}]
    assert got["entries"] == [{"name": "The Market", "keys": ["market"], "body": "b",
                               "category": "locations"}]


def test_parse_output_survives_prose_a_fence_and_junk_rows():
    text = ('Sure! Here you go:\n```json\n' + json.dumps({
        "characters": [{"name": "  Mara  "}, {"name": "   "}, "not a dict", {"description": "d"}],
        "entries": None,
    }) + '\n```\nHope that helps.')
    got = scenario.parse_output(text)
    assert got["characters"] == [{"name": "Mara", "description": "", "personality": ""}]
    assert got["entries"] == []


def test_parse_output_takes_comma_joined_keys_and_refuses_an_unknown_category():
    got = scenario.parse_output(json.dumps({"entries": [
        {"name": "A", "keys": "one, two ,", "body": "b", "category": "locations"},
        {"name": "B", "keys": [], "body": "b", "category": "bogus"},
    ]}))
    assert got["entries"][0]["keys"] == ["one", "two"]
    assert got["entries"][1]["category"] == "lore"     # unknown kinds fall back, never raise


def test_a_reply_that_is_not_json_at_all_yields_empty_sections():
    assert scenario.parse_output("I cannot do that.") == {"characters": [], "entries": []}


# ------------------------------------------------------------- the proposal
def test_the_proposal_writes_nothing(world):
    _wid, root = world
    scenario.proposal(CARD, EXTRACTED)
    assert characters.list_characters(root) == []
    assert entities.list_entities(root, "lore") == []
    assert greetings.list_greetings(root) == []


def test_a_proposed_entry_refiles_a_card_entry_without_retyping_its_body():
    prop = scenario.proposal(CARD, EXTRACTED)
    by_name = {e["name"]: e for e in prop["entries"]}
    assert by_name["The Tide-Gate"]["category"] == "locations"          # re-filed
    assert by_name["The Tide-Gate"]["body"] == "Iron and barnacle, three men wide."
    assert by_name["The Tide-Gate"]["keys"] == ["gate", "tide-gate"]    # the card's, kept
    assert by_name["The Market"]["category"] == "lore"                  # untouched by the model
    assert by_name["The Drowned Guild"]["category"] == "groups"         # genuinely new


def test_a_card_entrys_own_text_is_never_replaced_by_the_models():
    """The prompt shows entry bodies CLIPPED, so a model can read one as
    truncated and offer to complete it. Letting that land would swap the card's
    exact text for a guess — and re-filing an entry was never supposed to cost
    its body in the first place. A match takes the category and nothing else."""
    prop = scenario.proposal(CARD, {"characters": [], "entries": [
        {"name": "The Tide-Gate", "keys": ["invented"], "category": "locations",
         "body": "Iron and barnacle, three men wide, and beyond it the drowned nave …"},
    ]})
    gate = next(e for e in prop["entries"] if e["name"] == "The Tide-Gate")
    assert gate["category"] == "locations"                               # re-filed
    assert gate["body"] == "Iron and barnacle, three men wide."          # the card's, whole
    assert gate["keys"] == ["gate", "tide-gate"]                         # the card's keys


def test_a_proposed_entry_with_no_body_and_no_match_is_dropped():
    prop = scenario.proposal(CARD, {"characters": [], "entries": [
        {"name": "Nowhere", "keys": [], "body": "", "category": "locations"}]})
    assert "Nowhere" not in {e["name"] for e in prop["entries"]}


def test_each_opener_is_attributed_to_the_cast_member_it_opens_on():
    prop = scenario.proposal(CARD, EXTRACTED)
    openers = {g["name"]: g for g in prop["greetings"]}
    assert openers["Saltmarch"]["character"] == "Mara"
    assert openers["Saltmarch"]["present"] == ["Mara"]
    # Winifred is named first in this one even though Mara was proposed first.
    assert openers["Saltmarch (alt 1)"]["character"] == "Winifred"
    assert openers["Saltmarch (alt 1)"]["present"] == ["Winifred", "Mara"]


def test_an_opener_naming_nobody_carries_no_cast_rather_than_a_blank_one():
    prop = scenario.proposal(CARD, EXTRACTED)
    empty = next(g for g in prop["greetings"] if g["name"] == "Saltmarch (alt 2)")
    assert empty["character"] == ""
    assert empty["present"] == []


def test_a_card_alone_still_proposes_its_world_info_and_its_openers():
    """What a world with no LLM connection can import: everything but the cast."""
    prop = scenario.proposal(CARD, {"characters": [], "entries": []})
    assert prop["characters"] == []
    assert [e["name"] for e in prop["entries"]] == ["The Tide-Gate", "The Market"]
    assert len(prop["greetings"]) == 3
    assert all(g["character"] == "" for g in prop["greetings"])


# ------------------------------------------------------------------ the write
def test_apply_creates_the_cast_the_entries_and_the_openers(world):
    wid, root = world
    out = scenario.apply(root, wid, scenario.proposal(CARD, EXTRACTED), art=False)

    made = {c["name"]: c for c in out["characters"]}
    assert set(made) == {"Winifred", "Mara"}
    assert all(c["created"] for c in made.values())
    card = characters.read_card(root, made["Mara"]["id"], made["Mara"]["version"])
    assert card["data"]["description"] == "Tends the tide-gate."
    assert card["data"]["personality"] == "Watchful."

    assert {(e["kind"], e["id"]) for e in out["entries"]} == {
        ("locations", "the-tide-gate"), ("lore", "the-market"), ("groups", "the-drowned-guild")}

    assert len(out["greetings"]) == 3
    opener = greetings.read_greeting(root, out["greetings"][0]["id"])
    assert opener["meta"]["character"] == made["Mara"]["id"]
    assert opener["meta"]["present"] == [made["Mara"]["id"]]
    assert opener["body"].strip() == "Mara is waiting at the tide-gate when you arrive."
    second = greetings.read_greeting(root, out["greetings"][1]["id"])
    assert second["meta"]["present"] == [made["Winifred"]["id"], made["Mara"]["id"]]


def test_a_proposal_says_which_of_its_cast_the_world_already_has():
    """Reuse-by-name is the right write and the wrong surprise: a world with its
    own Mara silently absorbs this card's openers into her. The reviewer is told
    before they commit, so renaming the row is still an option."""
    prop = scenario.proposal(CARD, EXTRACTED, existing=["  MARA  "])
    by_name = {c["name"]: c for c in prop["characters"]}
    assert by_name["Mara"]["exists"] is True        # matched the way `apply` matches
    assert by_name["Winifred"]["exists"] is False
    # Absent `existing`, nothing is claimed either way rather than claimed false
    # on no evidence — a caller that cannot look is not a world with no cast.
    assert all(c["exists"] is False for c in scenario.proposal(CARD, EXTRACTED)["characters"])


def test_apply_reuses_a_character_the_world_already_has(world):
    wid, root = world
    existing, vid = characters.create_character(root, "Mara", "default",
                                                characters.blank_card("Mara"))
    out = scenario.apply(root, wid, scenario.proposal(CARD, EXTRACTED), art=False)

    mara = next(c for c in out["characters"] if c["name"] == "Mara")
    assert (mara["id"], mara["version"], mara["created"]) == (existing, vid, False)
    assert len(characters.list_characters(root)) == 2          # Mara + Winifred, not three
    # ...and the reused character's card is left alone: the proposal's text is a
    # guess about someone the world already describes.
    assert characters.read_card(root, existing, vid)["data"]["description"] == ""


def test_a_second_import_of_the_same_card_adds_no_duplicate_records(world):
    wid, root = world
    prop = scenario.proposal(CARD, EXTRACTED)
    scenario.apply(root, wid, prop, art=False)
    scenario.apply(root, wid, prop, art=False)
    assert len(characters.list_characters(root)) == 2
    assert len(entities.list_entities(root, "locations")) == 1
    assert len(entities.list_entities(root, "lore")) == 1
    # Greetings are the exception, and deliberately: two openers with the same
    # text are a thing a world can legitimately want, so nothing dedups them.
    assert len(greetings.list_greetings(root)) == 6


def test_an_unknown_category_is_refused_before_anything_is_written(world):
    """A bad category is a whole-proposal failure, not a half-import: the check
    runs ahead of the cast, so a rejected entry costs no characters."""
    wid, root = world
    prop = scenario.proposal(CARD, EXTRACTED)
    prop["entries"][0]["category"] = "bogus"
    with pytest.raises(lorebook.LorebookError):
        scenario.apply(root, wid, prop, art=False)
    assert characters.list_characters(root) == []
    assert greetings.list_greetings(root) == []


def test_an_opener_with_no_body_is_not_written(world):
    wid, root = world
    out = scenario.apply(root, wid, {"characters": [], "entries": [], "greetings": [
        {"name": "Blank", "body": "   ", "character": "", "present": []}]}, art=False)
    assert out["greetings"] == []
    assert greetings.list_greetings(root) == []


def test_apply_resolves_the_cast_a_reviewer_retyped(world):
    """Names, not ids, are what a proposal speaks — so editing one in review
    has to move the opener onto the character that name belongs to."""
    wid, root = world
    prop = scenario.proposal(CARD, EXTRACTED)
    prop["greetings"][0]["character"] = "Winifred"
    prop["greetings"][0]["present"] = ["Winifred"]
    out = scenario.apply(root, wid, prop, art=False)
    winifred = next(c for c in out["characters"] if c["name"] == "Winifred")["id"]
    assert greetings.read_greeting(root, out["greetings"][0]["id"])["meta"]["character"] == winifred


def test_a_blank_lead_is_nobody_even_in_a_world_holding_a_blank_named_character(world):
    """`POST /worlds/{wid}/characters` does not require a name, so a world can
    genuinely hold one called "" — and every cast-less opener would resolve to
    them if a blank lead were looked up like any other."""
    wid, root = world
    characters.create_character(root, "", "default", characters.blank_card(""))
    out = scenario.apply(root, wid, {"characters": [], "entries": [], "greetings": [
        {"name": "Opener", "body": "Nobody in particular.", "character": "", "present": []}]},
        art=False)
    meta = greetings.read_greeting(root, out["greetings"][0]["id"])["meta"]
    assert meta["character"] == ""
    assert meta["present"] == []


def test_a_named_cast_member_that_was_never_created_is_dropped_from_present(world):
    wid, root = world
    out = scenario.apply(root, wid, {"characters": [], "entries": [], "greetings": [
        {"name": "Opener", "body": "text", "character": "Ghost", "present": ["Ghost"]}]},
        art=False)
    meta = greetings.read_greeting(root, out["greetings"][0]["id"])["meta"]
    assert meta["character"] == ""
    assert meta["present"] == []


def test_apply_localizes_each_openers_art_into_that_greetings_own_asset_store(world):
    wid, root = world
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    prop = {"characters": [], "entries": [], "greetings": [
        {"name": "Opener", "body": "![](https://example.com/a.png)\n\nMara waits.",
         "character": "", "present": []}]}
    out = scenario.apply(root, wid, prop, fetch=lambda _url: (png, "png"))

    assert out["art"]["total"] == 1 and out["art"]["localized"] == 1
    gid = out["greetings"][0]["id"]
    stored = assets.list_images(root, gid, "default", base="greetings")
    assert len(stored) == 1
    assert f"/api/worlds/{wid}/greetings/{gid}/images/" in greetings.read_greeting(root, gid)["body"]


def test_art_off_leaves_the_opener_pointing_at_the_remote_url(world):
    wid, root = world
    prop = {"characters": [], "entries": [], "greetings": [
        {"name": "Opener", "body": "![](https://example.com/a.png)", "character": "", "present": []}]}
    out = scenario.apply(root, wid, prop, art=False)
    assert out["art"] == {"total": 0, "localized": 0, "skipped": 0, "failed": 0, "capped": False}
    assert "https://example.com/a.png" in greetings.read_greeting(
        root, out["greetings"][0]["id"])["body"]


# --------------------------------------------------------------- the sweeps
# Two invariants that every individual test above tests one instance of. They
# are swept rather than exampled because both are properties of the WHOLE
# pipeline: each round of review found another way to reach a dangling name or
# a raise, and an example test only ever pins the way that was found.
def test_no_opener_apply_writes_ever_names_a_character_that_does_not_exist(world):
    """`present`/`character` hold ids once written, so a name that resolved to
    nothing must leave the field empty rather than dangling — and a lead must
    always be in its own scene."""
    wid, _root = world
    bodies = ["Mara waits.", "", "   ", "![](x)", "{{char}} waits.", "Mara and Winifred."]
    casts = [[], [{"name": "Mara", "description": "d", "personality": "p"}],
             [{"name": "Mara"}, {"name": "Winifred"}]]
    # The proposals `proposal()` builds are internally consistent by
    # construction, so a sweep over only those never reaches the resolver's
    # failure branch at all — it takes a proposal whose openers name somebody
    # the cast list does not, which is what a reviewer produces every time they
    # untick a character an opener opens on. Both shapes are swept.
    drops = [None, "drop the cast", "name a stranger"]
    checked, unresolvable = 0, 0
    for i, body in enumerate(bodies):
        for j, cast in enumerate(casts):
            for k, drop in enumerate(drops):
                sub = worlds.world_root(worlds.create_world(f"Sweep {i}-{j}-{k}"))
                prop = scenario.proposal({"data": {"name": "S", "first_mes": body}},
                                         {"characters": cast, "entries": []})
                if drop == "drop the cast":
                    prop["characters"] = []
                elif drop == "name a stranger":
                    for g in prop["greetings"]:
                        g["character"] = g["character"] or "Nobody At All"
                        g["present"] = [*g["present"], "Nobody At All"]
                names = {c["name"] for c in prop["characters"]}
                unresolvable += sum(1 for g in prop["greetings"] if g["body"].strip()
                                    and not {g["character"], *g["present"]} <= {"", *names})
                out = scenario.apply(sub, wid, prop, art=False)
                ids = {c["id"] for c in characters.list_characters(sub)}
                for made in out["greetings"]:
                    meta = greetings.read_greeting(sub, made["id"])["meta"]
                    assert not meta["character"] or meta["character"] in ids
                    assert all(p in ids for p in meta["present"])
                    assert not meta["character"] or meta["character"] in meta["present"]
                    checked += 1
    assert checked, "the sweep wrote no greetings -- it is proving nothing"
    # ...and it really did drive the branch that drops an unresolvable name,
    # rather than sweeping only proposals that could not have one.
    assert unresolvable, "the sweep never offered a name no character answers to"


def test_no_reply_a_model_can_send_produces_a_malformed_proposal():
    """`parse_output` is the only thing between a provider and the reviewer, and
    a raise here is a 500 after the tokens were already spent."""
    card = {"data": {"name": "S", "first_mes": "Mara waits.", "character_book": {"entries": [
        {"keys": ["k"], "name": "E", "content": "b", "enabled": True}]}}}
    replies = [
        "", "{}", "null", "[]", "not json at all", '{"characters": "Mara"}',
        '{"characters": [null, 1, true, [], {}]}',
        '{"entries": [{"name": 5}, {"name": "A", "keys": {"x": 1}}, {"name": "E", "category": 7}]}',
        '{"characters": [{"name": "A", "description": {"nested": 1}}]}',
        '{"characters": [{"name": "E"}], "entries": [{"name": "E", "body": "x"}]}',
        '{"characters": [{"name": "' + "z" * 5000 + '"}]}',
        '{"entries": [' + ",".join('{"name": "n%d", "body": "b"}' % i for i in range(500)) + "]}",
    ]
    # Each reply twice: once through `parse_output` (the route's path), and once
    # straight into `proposal` (every other caller's). The second is not
    # redundant — `parse_output` trims and drops, so a sweep that only ever went
    # through it would leave `proposal`'s own normalization untested, and that
    # normalization is the only thing keeping the cast list and the openers
    # spelling a name the same way.
    dirty = [{"characters": [{"name": "  Mara  "}, {"name": "mara"}, {"name": "   "},
                             {"name": "Winifred"}],
              "entries": [{"name": " E ", "keys": [], "body": "", "category": "locations"}]}]
    for extracted in [scenario.parse_output(r) for r in replies] + dirty:
        prop = scenario.proposal(card, extracted, existing=["E"])
        assert set(prop) == {"characters", "entries", "greetings"}
        cast = {c["name"] for c in prop["characters"]}
        for c in prop["characters"]:
            assert c["name"] and c["name"].strip() == c["name"]
        for e in prop["entries"]:
            assert isinstance(e["body"], str) and isinstance(e["keys"], list)
            assert e["category"] in entities.ENTITY_KINDS
        for g in prop["greetings"]:
            # The join key holds in both directions: an opener can only name
            # somebody the cast list also offers.
            assert g["character"] in {"", *cast}
            assert set(g["present"]) <= cast


def test_a_failed_download_costs_the_opener_its_image_not_its_text(world):
    wid, root = world
    def boom(_url):
        raise OSError("no network")

    prop = {"characters": [], "entries": [], "greetings": [
        {"name": "Opener", "body": "![](https://example.com/a.png)\n\nMara waits.",
         "character": "", "present": []}]}
    out = scenario.apply(root, wid, prop, fetch=boom)
    assert out["art"]["failed"] == 1 and out["art"]["localized"] == 0
    assert "Mara waits." in greetings.read_greeting(root, out["greetings"][0]["id"])["body"]
