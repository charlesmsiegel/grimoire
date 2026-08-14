"""Scenario-card import (#217): extraction, the review payload, and the write.

The three halves are tested apart because they fail apart. `parse_output` is
about what a model can send; `proposal` is about what the reviewer is shown, and
is pure — nothing on disk moves; `apply` is the only thing here that writes, and
what it writes has to line up with what the proposal promised.
"""

import json

import pytest

from grimoire.store import (assets, characters, entities, greetings, lorebook, scenario,
                            worlds)

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
