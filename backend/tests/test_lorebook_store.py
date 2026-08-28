import json

import pytest

from grimoire.store import cards, characters, entities, lorebook


def test_normalize_standalone_export_dict_entries():
    book = {"entries": {
        "0": {"key": ["pact", "salt"], "comment": "Salt Pact", "content": "The pact binds."},
        "1": {"key": ["king"], "comment": "Constant Lore", "content": "Always here.", "constant": True},
        "2": {"key": ["ghost"], "comment": "Disabled", "content": "skip me", "disable": True},
        "3": {"key": ["blank"], "comment": "Blank", "content": "   "},
    }}
    out = lorebook._normalize(book)
    by_name = {e["name"]: e for e in out}
    assert set(by_name) == {"Salt Pact", "Constant Lore"}      # disabled + blank dropped
    assert by_name["Salt Pact"]["keys"] == ["pact", "salt"]
    assert by_name["Salt Pact"]["body"] == "The pact binds."
    assert by_name["Salt Pact"]["category"] == "lore"
    assert by_name["Constant Lore"]["keys"] == []              # constant -> always-on (keyless)


def test_normalize_character_book_list_entries():
    book = {"entries": [
        {"keys": ["sea"], "name": "The Sea", "content": "salt water", "enabled": True},
        {"keys": ["off"], "name": "Off", "content": "nope", "enabled": False},
    ]}
    out = lorebook._normalize(book)
    assert [e["name"] for e in out] == ["The Sea"]             # enabled:false dropped
    assert out[0]["keys"] == ["sea"]


def test_normalize_name_falls_back_to_first_key():
    book = {"entries": [{"keys": ["solo"], "content": "x"}]}
    assert lorebook._normalize(book)[0]["name"] == "solo"


def test_parse_lorebook_and_card_and_errors():
    # standalone lorebook bytes
    data = json.dumps({"entries": {"0": {"key": ["a"], "content": "body", "comment": "A"}}}).encode()
    assert lorebook.parse(data, "lorebook")[0]["name"] == "A"
    # a card with an embedded character_book (json format)
    card = characters.blank_card("Hero")
    card["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "c", "name": "K"}]}
    assert lorebook.parse(json.dumps(card).encode(), "json")[0]["name"] == "K"
    # a card with no character_book -> []
    assert lorebook.parse(json.dumps(characters.blank_card("Z")).encode(), "json") == []
    # bad lorebook json -> LorebookError
    with pytest.raises(lorebook.LorebookError):
        lorebook.parse(b"not json", "lorebook")
    # bad card -> CardParseError
    with pytest.raises(cards.CardParseError):
        lorebook.parse(b"garbage", "json")


def test_parse_extracts_character_book_from_charx():
    # the card-format path is shared with cards.loads; verify a packaged .charx round-trips
    card = characters.blank_card("Hero")
    card["data"]["character_book"] = {"entries": [{"keys": ["relic"], "content": "ancient", "name": "Relic"}]}
    blob = cards.dumps(card, "charx")
    out = lorebook.parse(blob, "charx")
    assert [e["name"] for e in out] == ["Relic"]
    assert out[0]["keys"] == ["relic"]


def test_commit_routes_and_writes_keys(tmp_path):
    created = lorebook.commit(tmp_path, [
        {"name": "Salt Pact", "keys": ["pact", "salt"], "body": "binds", "category": "lore"},
        {"name": "The Docks", "keys": ["docks"], "body": "wet", "category": "locations"},
        {"name": "No Cat", "keys": [], "body": "x"},   # default category -> lore
    ])
    assert [c["kind"] for c in created] == ["lore", "locations", "lore"]
    lore_ids = [e["id"] for e in entities.list_entities(tmp_path, "lore")]
    assert created[0]["id"] in lore_ids and created[2]["id"] in lore_ids
    # keys round-trip as the builder reads them (comma-joined frontmatter)
    e = entities.read_entity(tmp_path, "lore", created[0]["id"])
    assert e["meta"]["keys"] == "pact,salt"
    assert e["body"].strip() == "binds"


def test_commit_skips_exact_duplicates(tmp_path):
    entries = [{"name": "Salt Pact", "keys": ["pact", "salt"], "body": "binds", "category": "lore"}]
    assert len(lorebook.commit(tmp_path, entries)) == 1

    again = lorebook.commit(tmp_path, [
        {"name": "Salt Pact", "keys": ["pact", "salt"], "body": "binds"},      # exists -> skipped
        {"name": "Salt Pact", "keys": ["pact", "salt"], "body": "binds"},      # in-batch dupe -> skipped
        {"name": "Salt Pact", "keys": ["pact", "salt"], "body": "different"},  # body differs -> created
        {"name": "Salt Pact", "keys": ["other"], "body": "binds"},             # keys differ -> created
    ])
    assert len(again) == 2
    # only the original + the two genuinely-new variants exist on disk
    assert len(entities.list_entities(tmp_path, "lore")) == 3


def test_commit_unknown_category_raises(tmp_path):
    with pytest.raises(lorebook.LorebookError):
        lorebook.commit(tmp_path, [{"name": "X", "keys": [], "body": "y", "category": "bogus"}])


def test_from_character_book_normalizes():
    book = {"entries": [
        {"keys": ["pact"], "content": "the salt pact", "name": "Pact", "enabled": True},
        {"keys": ["off"], "content": "skip me", "enabled": False},
    ]}
    out = lorebook.from_character_book(book)
    assert out == [{"name": "Pact", "keys": ["pact"], "body": "the salt pact", "category": "lore"}]
    assert lorebook.from_character_book(None) == []


# Every book shape the two paths have to agree on -- both ST schemas, the
# containers `_entries_container` accepts, and each reason `_importable` drops
# an entry. Reused by the equivalence test below so a new shape is covered by
# both at once.
# One entry survives; every other carries a different reason to be dropped.
EVERY_DROP_REASON = {"entries": [
    {"keys": ["a"], "content": "kept"},
    {"keys": ["b"], "content": "off", "enabled": False},
    {"keys": ["c"], "content": "   "},                               # blank
    {"keys": ["d"], "content": "gone", "disable": True},
    {"keys": ["e"], "content": None},                                # non-str content
    {"keys": ["f"]},                                                 # no content at all
]}
BOOK_SHAPES = [
    None,
    {},
    {"entries": []},
    {"entries": {}},
    "not a book",
    {"entries": "not entries"},
    {"entries": [None, 3, "x"]},                                     # non-dict entries
    {"entries": [{"keys": ["a"], "content": "kept"}]},
    EVERY_DROP_REASON,
    {"0": {"key": ["a"], "content": "kept"},                         # bare dict container
     "1": {"key": ["b"], "content": "off", "disable": True}},
    [{"keys": ["a"], "content": "kept"}],                            # bare list container
]


@pytest.mark.parametrize("book", BOOK_SHAPES)
def test_importable_count_matches_what_normalize_yields(book):
    """`importable_count` exists to skip building the entries, so nothing forces
    the two to agree except this. They share `_importable`; if a future edit
    inlines the rule back into one of them, this is what fails."""
    assert lorebook.importable_count(book) == len(lorebook.from_character_book(book))


def test_importable_count_drops_exactly_what_the_import_drops():
    """Pins the number itself, not just that the two paths agree -- an
    `_importable` that dropped everything would satisfy the equivalence."""
    assert lorebook.importable_count(EVERY_DROP_REASON) == 1
    assert [e["body"] for e in lorebook.from_character_book(EVERY_DROP_REASON)] == ["kept"]


def test_commit_accepts_new_kind_categories(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import lorebook
    created = lorebook.commit(tmp_path, [
        {"name": "Salt Knife", "keys": ["knife"], "body": "sharp", "category": "items"}])
    assert created == [{"kind": "items", "id": "salt-knife"}]


# ---- preserving ST's advanced fields on import (#20) ----

ADVANCED_ENTRY = {
    "keys": ["pact"], "secondary_keys": ["salt"], "selective": True,
    "name": "Salt Pact", "content": "The pact binds.",
    "position": "after_char", "insertion_order": 7, "priority": 2,
    "probability": 50, "useProbability": True, "case_sensitive": True,
}


def test_normalize_stashes_advanced_st_fields():
    out = lorebook._normalize({"entries": [dict(ADVANCED_ENTRY)]})
    [e] = out
    assert e["name"] == "Salt Pact" and e["keys"] == ["pact"]
    assert e["extensions"] == {
        "secondary_keys": ["salt"], "selective": True, "position": "after_char",
        "insertion_order": 7, "priority": 2, "probability": 50,
        "useProbability": True, "case_sensitive": True,
    }


def test_normalize_constant_keeps_raw_keys_in_extensions():
    # `constant` still means keyless activation, but the stash records the raw
    # flag and the keys it suppressed, so "keyless because constant" stays
    # distinguishable from "keyless because no keys".
    out = lorebook._normalize({"entries": [
        {"keys": ["king"], "name": "Crown", "content": "Always on.", "constant": True}]})
    [e] = out
    assert e["keys"] == []
    assert e["extensions"] == {"constant": True, "keys": ["king"]}


def test_normalize_simple_entry_carries_no_extensions_key():
    out = lorebook._normalize({"entries": [{"keys": ["sea"], "name": "Sea", "content": "x"}]})
    assert "extensions" not in out[0]


def test_commit_stashes_extensions_as_frontmatter_and_roundtrips(tmp_path):
    entries = lorebook._normalize({"entries": [dict(ADVANCED_ENTRY)]})
    [created] = lorebook.commit(tmp_path, entries)
    e = entities.read_entity(tmp_path, created["kind"], created["id"])
    stored = json.loads(e["meta"]["st_extensions"])
    assert stored == entries[0]["extensions"]


def test_commit_writes_no_st_extensions_when_absent(tmp_path):
    [created] = lorebook.commit(
        tmp_path, [{"name": "Plain", "keys": ["p"], "body": "x", "category": "lore"}])
    meta = entities.read_entity(tmp_path, "lore", created["id"])["meta"]
    assert "st_extensions" not in meta


def test_parse_to_commit_preserves_extensions_through_a_card(tmp_path):
    card = characters.blank_card("Hero")
    card["data"]["character_book"] = {"entries": [dict(ADVANCED_ENTRY)]}
    entries = lorebook.parse(json.dumps(card).encode(), "json")
    [created] = lorebook.commit(tmp_path, entries)
    stored = json.loads(entities.read_entity(tmp_path, "lore", created["id"])["meta"]["st_extensions"])
    assert stored["secondary_keys"] == ["salt"]
    assert stored["insertion_order"] == 7
