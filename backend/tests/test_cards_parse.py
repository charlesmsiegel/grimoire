"""Sprint 037: the pure card parser, record mapping, roles and link suggestions (synthetic cards only)."""

from __future__ import annotations

import pickle
import time
import unicodedata

import pytest

from grimoire.cards import (
    CCV3, LOREBOOK_V3, ROLES, SCENARIO_DOC, CardError, CharacterCard, LorebookCard, Loss, ScenarioCard,
    character_payload, detect, lorebook_payload, mention_texts, parse_card, parse_lorebook_document, pieces,
    scenario_payload, section_ok, slugify, suggest_links,
)
from grimoire.core.identity import SourceRef

from card_fixtures import LOREBOOK, SCENARIO, V3, card


def test_a_v3_card_keeps_every_text_in_its_role():
    parsed = parse_card(V3)
    assert isinstance(parsed, CharacterCard) and parsed.format == CCV3 and parsed.name == "Mara Vell"
    assert [(g.greeting_id, g.group_only) for g in parsed.greetings] == [
        ("first", False), ("alt-1", False), ("group-1", True)]
    assert parsed.system_prompt and parsed.post_history_instructions and parsed.example_messages
    assert parsed.authorship.creator == "grimoire tests" and parsed.tags == ("synthetic", "harbor")
    assert parsed.extensions == {"x-test": {"kept": True}}
    entry = parsed.lorebook.entries[0]
    assert entry.keys == ("lighthouse",) and entry.settings["insertion_order"] == 10
    assert entry.extensions == {"x-depth": 4} and not parsed.lorebook.entries[1].enabled
    roles = {(piece.field, piece.role) for piece in pieces(parsed)}
    assert {("data.description", "authored_description"), ("data.personality", "authored_style"),
            ("data.scenario", "scenario_instruction"), ("data.mes_example", "authored_voice"),
            ("data.system_prompt", "private_instruction"), ("greeting:first", "opening_greeting"),
            ("data.character_book.entries[0]", "lorebook_content"), ("data.creator_notes", "creator_note")} <= roles
    assert "accepted_event" not in ROLES and all(piece.role in ROLES for piece in pieces(parsed))


def test_unknown_fields_are_kept_and_wrong_types_are_reported_not_coerced():
    messy = card(tags=["ok", 3], nickname=7, x_unknown={"deep": [1, 2]})
    messy["x_top"] = "kept"
    parsed = parse_card(messy)
    assert parsed.tags == ("ok",) and parsed.nickname is None
    assert Loss("data.tags[1]", "wrong_type") in parsed.losses and Loss("data.nickname", "wrong_type") in parsed.losses
    assert parsed.extras["data"] == {"x_unknown": {"deep": [1, 2]}}
    assert parsed.extras["card"]["x_top"] == "kept"
    for loss in parsed.losses:  # a loss names a field, never its text
        assert "Mara" not in loss.path


@pytest.mark.parametrize("document, detail", [
    ({"spec": "chara_card_v2", "data": {"name": "x"}}, "chara_card_v2"),
    ({"name": "x", "first_mes": "hi"}, "chara_card_v1"),
    ({"entries": {}}, "sillytavern_world_info"),
    ({"hello": "world"}, "unknown"),
])
def test_other_formats_are_unsupported_and_named(document, detail):
    with pytest.raises(CardError) as caught:
        parse_card(document)
    assert (caught.value.reason, caught.value.detail) == ("unsupported_format", detail)


@pytest.mark.parametrize("document", [
    card(name="   "), card(name=None),
    {"format": SCENARIO_DOC, "title": "t"},  # neither premise nor opening
    {"format": SCENARIO_DOC, "premise": "p"},  # no title
    {"spec": "chara_card_v3", "spec_version": "3.0", "data": []},
])
def test_a_card_without_identity_or_meaning_is_invalid(document):
    with pytest.raises(CardError) as caught:
        parse_card(document)
    assert caught.value.reason == "invalid_card"


def test_a_v3_spec_with_another_major_version_is_unsupported():
    with pytest.raises(CardError) as caught:
        parse_card({**card(), "spec_version": "4.0"})
    assert caught.value.reason == "unsupported_format"


def test_card_errors_pickle_and_never_echo_card_text():
    with pytest.raises(CardError) as caught:
        parse_card(card(name=["SENTINEL-NAME"]))
    assert "SENTINEL" not in str(caught.value)
    again = pickle.loads(pickle.dumps(caught.value))
    assert (again.reason, again.detail) == (caught.value.reason, caught.value.detail)


def test_detect_names_each_format():
    assert detect(V3) == CCV3 and detect(LOREBOOK) == LOREBOOK_V3 and detect(SCENARIO) == SCENARIO_DOC


def test_a_scenario_document_is_a_scenario_never_a_character():
    parsed = parse_card(SCENARIO)
    assert isinstance(parsed, ScenarioCard) and parsed.title == "Storm over Port Vell"
    assert [opening.greeting_id for opening in parsed.openings] == ["opening-1", "opening-2"]
    assert [(cast.ref, cast.name) for cast in parsed.cast] == [
        ("world:harbor/character/innkeeper", None), ("world:harbor/character/nobody", None), (None, "Captain Osk")]
    payload = scenario_payload(parsed)
    assert "traits" not in payload and payload["roles"]["premise"] == "scenario_premise"
    assert payload["roles"]["openings"] == "scenario_opening"
    assert payload["cast"] == [{"ref": "world:harbor/character/innkeeper"}, {"ref": "world:harbor/character/nobody"},
                               {"name": "Captain Osk"}]


def test_a_v3_card_can_be_read_as_a_scenario():
    parsed = parse_card(V3, as_scenario=True)
    assert isinstance(parsed, ScenarioCard) and parsed.format == CCV3 and parsed.title == "Mara Vell"
    assert parsed.premise == V3["data"]["scenario"] and parsed.openings[0].text == V3["data"]["first_mes"]
    assert parsed.texts["setting"] == V3["data"]["description"]
    with pytest.raises(CardError):
        parse_card(SCENARIO, as_scenario=True)


def test_lorebook_documents():
    parsed = parse_card(LOREBOOK)
    assert isinstance(parsed, LorebookCard) and parsed.lorebook.name == "Port Vell gazetteer"
    assert parse_lorebook_document(LOREBOOK) == parsed
    with pytest.raises(CardError) as caught:
        parse_lorebook_document(V3)
    assert caught.value.reason == "unsupported_format"
    payload = lorebook_payload(parsed.lorebook)
    assert payload["entries"] == [{"keys": ["market"], "content": "The fish market opens at dawn.",
                                   "settings": {"insertion_order": 0, "use_regex": False}}]


def test_the_character_payload_is_an_agent_with_its_card_texts_apart():
    payload, losses = character_payload(parse_card(V3), lorebook=SourceRef("card:mara", "lorebook", "lore", "r1"))
    assert payload["nature"] == "individual" and [t["facet"] for t in payload["traits"]] == ["agent", "communicative"]
    assert payload["description"] == V3["data"]["description"]
    assert set(payload["sections"]) == {"personality"}  # instructions and examples are never character facts
    meta = payload["card"]
    assert meta["texts"] == {"scenario": V3["data"]["scenario"], "example-messages": V3["data"]["mes_example"]}
    assert meta["roles"]["personality"] == "authored_style"
    assert meta["roles"]["texts.scenario"] == "scenario_instruction"
    assert meta["roles"]["texts.example-messages"] == "authored_voice"
    assert [g["id"] for g in meta["greetings"]] == ["first", "alt-1", "group-1"]
    assert meta["instructions"] == {"system_prompt": "Write in the third person.",
                                    "post_history_instructions": "Stay in character."}
    assert meta["lorebook"] == "card:mara/lorebook/lore@r1" and losses == ()


def test_text_that_cannot_be_a_section_is_kept_unsectioned():
    payload, losses = character_payload(parse_card(card(personality="x" * 9000)))
    assert "personality" not in payload.get("sections", {})
    assert payload["card"]["unsectioned"]["personality"] == "x" * 9000
    assert losses == (Loss("character.sections.personality", "not_section"),)
    assert section_ok("personality", "fine") and not section_ok("personality", "x" * 9000)


def test_slugify():
    assert slugify("Mära the Bold!") == "mara-the-bold" and slugify("   ") == "card"
    assert slugify("../../etc/passwd") == "etc-passwd" and slugify("日本") == "card"
    assert len(slugify("a" * 300)) <= 48


def _finder(names):
    def find(phrase):
        return tuple(names.get(phrase.casefold(), ()))
    return find


def test_link_suggestions_name_candidates_and_never_choose():
    guild, city = SourceRef("world:a", "group", "guild", "r1"), SourceRef("world:a", "location", "vell", "r1")
    twin = SourceRef("world:b", "location", "vell", "r1")
    found = suggest_links(mention_texts(parse_card(V3)), _finder(
        {"silver guild": [guild], "port vell": [city, twin], "the": [guild]}))
    by_phrase = {link.phrase: link for link in found.links}
    assert by_phrase["Silver Guild"].candidates == (guild,) and not by_phrase["Silver Guild"].ambiguous
    assert by_phrase["Port Vell"].ambiguous and by_phrase["Port Vell"].fields == ("data.description",)
    assert "the" not in by_phrase and not found.truncated  # a lowercase word starts no phrase


def test_link_suggestions_exclude_and_bound():
    own = SourceRef("card:mara", "character", "mara-vell", "r1")
    found = suggest_links({"a": "Mara Vell"}, _finder({"mara vell": [own]}), exclude=lambda ref: ref.source == "card:mara")
    assert found.links == ()
    many = " ".join(f"W{n}" for n in range(30_000))
    assert suggest_links({"a": many}, _finder({})).truncated


def test_a_blank_greeting_is_skipped_and_reported():
    parsed = parse_card(card(alternate_greetings=["  ", "Hello."]))
    assert [g.greeting_id for g in parsed.greetings] == ["first", "alt-2", "group-1"]
    assert Loss("data.alternate_greetings[0]", "blank") in parsed.losses


def test_greeting_ids_follow_the_card_indexes():
    parsed = parse_card(card(alternate_greetings=[None, "  ", "Real."]))
    assert [g.greeting_id for g in parsed.greetings if not g.group_only] == ["first", "alt-3"]
    assert Loss("data.alternate_greetings[0]", "wrong_type") in parsed.losses
    assert Loss("data.alternate_greetings[1]", "blank") in parsed.losses


def test_a_lorebook_name_that_is_blank_or_too_long_is_dropped():
    for name, reason in (("  ", None), ("n" * 5000, "too_long")):
        parsed = parse_card({**LOREBOOK, "data": {**LOREBOOK["data"], "name": name}})
        assert parsed.lorebook.name is None
        if reason:
            assert Loss("data.name", reason) in parsed.losses


def test_mentions_find_punctuated_names_and_read_the_description_first():
    city, order = SourceRef("world:a", "location", "saint-malo", "r1"), SourceRef("world:a", "group", "order", "r1")
    texts = {"data.character_book.entries[0]": " ".join(f"Lore{n}" for n in range(25_000)),
             "data.description": "Born in Saint-Malo, sworn to the Order of the Silver Hand."}
    found = suggest_links(texts, _finder({"saint-malo": [city], "order of the silver hand": [order]}))
    assert {link.phrase for link in found.links} >= {"Saint-Malo", "Order of the Silver Hand"}
    assert found.truncated and found.skipped == ("data.character_book.entries[0]",)


def test_a_long_gap_between_words_is_scanned_in_linear_time():
    for gap in (" " * 1_000_000 + "\n", " " * 1_000_000 + ";"):
        started = time.perf_counter()
        suggest_links({"data.description": "Port" + gap + "Vell"}, _finder({}))
        assert time.perf_counter() - started < 2


def test_possessives_marks_and_decomposed_text_still_suggest():
    city, goddess, zoe = (SourceRef("world:a", "location", "vell", "r1"), SourceRef("world:a", "character", "devi", "r1"),
                          SourceRef("world:a", "character", "zoe", "r1"))
    found = suggest_links({"data.description": "She runs Port Vell's docks. वह देवी है. "
                           + unicodedata.normalize("NFD", "Zoë Vell waits.")},
                          _finder({"port vell": [city], "देवी": [goddess], "zoë vell": [zoe]}))
    assert {link.candidates for link in found.links} == {(city,), (goddess,), (zoe,)}


def test_a_lorebook_documents_own_unknowns_never_overwrite_its_books():
    document = {"spec": "lorebook_v3", "x-top": 2,
                "data": {**LOREBOOK["data"], "card": {"precious": 1}, "description": "The Silver Guild rules."}}
    parsed = parse_card(document)
    payload = lorebook_payload(parsed.lorebook, extras=parsed.extras)
    assert payload["extras"] == {"data": {"card": {"precious": 1}}, "card": {"x-top": 2}}
    assert ("data.description", "lorebook_content") in {(piece.field, piece.role) for piece in pieces(parsed)}
    assert "data.description" in mention_texts(parsed)


def test_one_over_deep_extension_drops_only_itself():
    deep: object = "x"
    for _ in range(60):
        deep = [deep]
    parsed = parse_card(card(extensions={"keep": {"depth": 4}, "junk": deep}))
    assert parsed.extensions == {"keep": {"depth": 4}}
    assert Loss("data.extensions.junk", "too_deep") in parsed.losses


def test_names_ending_in_a_full_stop_or_joined_by_a_unicode_hyphen_are_found():
    usa, hotel = SourceRef("world:a", "location", "usa", "r1"), SourceRef("world:a", "location", "hotel", "r1")
    found = suggest_links({"data.description": "Born in the U.S.A. near the Hôtel\u2011Dieu."},
                          _finder({"u.s.a.": [usa], "hôtel\u2011dieu": [hotel]}))
    assert {link.candidates for link in found.links} == {(usa,), (hotel,)}


@pytest.mark.parametrize("dash", ["-", "‐", "‑", "‒", "–", "−", "﹣", "－"])
def test_a_name_written_with_a_unicode_dash_suggests_the_librarys_hyphenated_name(dash):
    city = SourceRef("world:a", "location", "saint-malo", "r1")
    found = suggest_links({"data.description": f"We sailed to Saint{dash}Malo at dawn."},
                          _finder({"saint-malo": [city]}))
    assert [link.candidates for link in found.links] == [(city,)]


def test_lore_entries_keep_their_own_places_past_a_dropped_entry():
    parsed = parse_card(card(character_book={"entries": [{"keys": ["a"], "content": 5},
                                                         {"keys": ["b"], "content": "The Silver Guild meets here."}]}))
    assert Loss("data.character_book.entries[0]", "wrong_type") in parsed.losses
    lore = {piece.field for piece in pieces(parsed) if piece.role == "lorebook_content"}
    assert lore == {"data.character_book.entries[1]"}
    assert "data.character_book.entries[1]" in mention_texts(parsed)
    assert "data.character_book.entries[0]" not in mention_texts(parsed)


def test_a_cast_entry_keeps_its_other_keys():
    document = {**SCENARIO, "cast": [{"name": "Captain Osk", "role": "antagonist"},
                                     {"ref": "world:harbor/character/innkeeper", "note": "x"}, {"name": 3}]}
    parsed = parse_card(document)
    assert [(cast.ref, cast.name, dict(cast.extras)) for cast in parsed.cast] == [
        (None, "Captain Osk", {"role": "antagonist"}), ("world:harbor/character/innkeeper", None, {"note": "x"})]
    assert Loss("cast[2].name", "wrong_type") in parsed.losses
    assert scenario_payload(parsed)["cast"][0] == {"name": "Captain Osk", "extras": {"role": "antagonist"}}
