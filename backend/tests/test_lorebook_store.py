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


def test_normalize_preserves_regex_flag_and_entry_extensions():
    out = lorebook._normalize({"entries": [
        {"keys": ["/salt.*/"], "name": "Rx", "content": "x", "use_regex": True,
         "extensions": {"sticky": 2, "cooldown": 3}},
        # standalone world-info recursion controls are top-level fields, not
        # nested extensions -- they ride the allowlist (Codex review on #20)
        {"keys": ["deep"], "name": "Deep", "content": "z",
         "preventRecursion": True, "delayUntilRecursion": 2},
        # V3 entries routinely carry an EMPTY extensions object; that alone
        # must not put a stash (and so frontmatter) on a simple import.
        {"keys": ["sea"], "name": "Sea", "content": "y", "extensions": {}},
    ]})
    assert out[0]["extensions"] == {"use_regex": True,
                                    "extensions": {"sticky": 2, "cooldown": 3}}
    assert out[1]["extensions"] == {"preventRecursion": True, "delayUntilRecursion": 2}
    assert "extensions" not in out[2]


# --- the audit table (spec 4.1): the stash keeps what used to be lost, and
# `adopt` maps the honoured rows onto native activation fields -----------------


def test_stash_keeps_previously_lost_fields():
    out = lorebook._normalize({"entries": [
        {"keys": ["tide"], "name": "Saltmarch Charter", "content": "x", "sticky": 2, "cooldown": 3,
         "delay": 1, "ignoreBudget": True, "group": "g", "vectorized": True}]})
    assert out[0]["extensions"] == {"sticky": 2, "cooldown": 3, "delay": 1,
                                    "ignoreBudget": True, "group": "g", "vectorized": True}


@pytest.mark.parametrize("field", [
    "ignore_budget", "groupOverride", "group_override", "groupWeight", "group_weight",
    "useGroupScoring", "use_group_scoring", "characterFilter", "character_filter",
    "triggers", "automationId", "automation_id",
])
def test_stash_keeps_every_spelling_of_the_lost_rows(field):
    out = lorebook._normalize({"entries": [
        {"keys": ["tide"], "name": "Saltmarch Charter", "content": "x", field: "v"}]})
    assert out[0]["extensions"] == {field: "v"}


def _both_places(stash):
    """The same stash at the top level and nested under `extensions`, where ST
    writes most advanced fields when it exports a card."""
    return [stash, {"extensions": dict(stash)}]


AUDIT_ROWS = [
    ({"keysecondary": ["tide"], "selective": True, "selectiveLogic": 2},
     {"secondary_keys": "tide", "key_logic": "not_any"}),
    ({"secondary_keys": ["tide", " ", "salt"], "selectiveLogic": 3},
     {"secondary_keys": "tide, salt", "key_logic": "and_all"}),
    ({"keysecondary": ["tide"], "selectiveLogic": 1},
     {"secondary_keys": "tide", "key_logic": "not_all"}),
    # and_any is the native default, so it is not written
    ({"keysecondary": ["tide"], "selectiveLogic": 0}, {"secondary_keys": "tide"}),
    # `selective` absent is ST's default: on
    ({"keysecondary": ["tide"]}, {"secondary_keys": "tide"}),
    # selective off: the secondary list is ignored, so nothing is written
    ({"selective": False, "keysecondary": ["tide"]}, {}),
    ({"selective": False, "keysecondary": ["tide"], "selectiveLogic": 2}, {}),
    ({"keysecondary": []}, {}),
    ({"sticky": 2, "cooldown": 3}, {"sticky": "2", "cooldown": "3"}),
    ({"priority": 5, "order": 900}, {"priority": "5"}),
    ({"order": 900}, {"priority": "900"}),
    ({"insertion_order": 7}, {"priority": "7"}),
    ({"order": 900, "insertion_order": 7}, {"priority": "900"}),
    ({"ignoreBudget": True}, {"keep": "true"}),
    ({"ignore_budget": True}, {"keep": "true"}),
    ({"ignoreBudget": False}, {}),
    ({"excludeRecursion": True}, {"recursion": "pulls_only"}),
    ({"exclude_recursion": True}, {"recursion": "pulls_only"}),
    ({"preventRecursion": True}, {"recursion": "pulled_only"}),
    ({"prevent_recursion": True}, {"recursion": "pulled_only"}),
    ({"excludeRecursion": True, "preventRecursion": True}, {"recursion": "none"}),
    ({"excludeRecursion": False, "preventRecursion": False}, {}),
    ({"scanDepth": None}, {}),
    ({"scan_depth": 4}, {"scan_depth": "4"}),
    ({"scanDepth": 0}, {"scan_depth": "0"}),
    # a value of the wrong type is skipped, and does not spoil its neighbours
    ({"sticky": "abc", "cooldown": 3}, {"cooldown": "3"}),
    ({"selectiveLogic": 9, "keysecondary": ["tide"]}, {"secondary_keys": "tide"}),
    ({"sticky": True}, {}),
    ({"keysecondary": "tide"}, {}),
    # a key that spans lines cannot be written to a single-line scalar: dropped
    ({"keysecondary": ["tide", "har\nbour", "salt\u2028"]}, {"secondary_keys": "tide, salt"}),
    ({"keysecondary": ["a\rb"]}, {}),
]


@pytest.mark.parametrize("stash,expected", AUDIT_ROWS)
def test_adopt_maps_the_audit_table(stash, expected):
    for placed in _both_places(stash):
        assert lorebook.adopt(placed).fields == expected


def test_adopt_clamps_into_bounds():
    assert lorebook.adopt({"order": 5000, "sticky": 99, "cooldown": 7, "scanDepth": 500}).fields == {
        "priority": "1000", "sticky": "50", "cooldown": "7", "scan_depth": "100"}
    # clamped to the bottom is the default, which is not written
    assert lorebook.adopt({"order": -5, "cooldown": -4}).fields == {"priority": "0"}


def test_adopt_reports_unmapped():
    res = lorebook.adopt({"probability": 50, "position": 1, "delay": 2})
    assert res.unmapped == ("delay", "position", "probability")
    assert res.fields == {}


def test_adopt_reports_unmapped_in_either_spelling_and_place():
    res = lorebook.adopt({
        "case_sensitive": True, "matchWholeWords": True,
        "extensions": {"useRegex": True, "group": "g", "automation_id": "a",
                       "delayUntilRecursion": 1, "characterFilter": {"names": ["Mara"]}},
        "sticky": 2,
    })
    assert res.unmapped == ("automation_id", "case_sensitive", "characterFilter",
                            "delayUntilRecursion", "group", "matchWholeWords", "useRegex")
    assert res.fields == {"sticky": "2"}


def test_adopt_unmapped_is_deduplicated_and_skips_absent_values():
    res = lorebook.adopt({"delay": 1, "extensions": {"delay": 2, "depth": None}})
    assert res.unmapped == ("delay",)


def test_adopt_unmapped_ignores_the_defaults_an_st_export_writes():
    res = lorebook.adopt({
        "group": "", "groupOverride": False, "vectorized": False,
        "triggers": [], "characterFilter": {}, "automationId": "", "probability": 100,
        "useProbability": False, "caseSensitive": None, "matchWholeWords": False,
        "extensions": {"use_regex": False, "automation_id": "", "depth": None},
    })
    assert res.unmapped == ()
    assert res.fields == {}


# A stock entry as SillyTavern's world-info editor writes it (camelCase), and
# as its V3 card export files one under `extensions` (snake_case): every field
# at its default. Adopting either must change nothing and report nothing --
# otherwise every import looks customised.
ST_STOCK_WORLD_INFO = {
    "keysecondary": [], "selective": True, "selectiveLogic": 0, "constant": False,
    "vectorized": False, "order": 100, "position": 0, "ignoreBudget": False,
    "excludeRecursion": False, "preventRecursion": False, "delayUntilRecursion": False,
    "probability": 100, "useProbability": True, "depth": 4, "group": "",
    "groupOverride": False, "groupWeight": 100, "scanDepth": None,
    "caseSensitive": None, "matchWholeWords": None, "useGroupScoring": None,
    "automationId": "", "role": 0, "sticky": 0, "cooldown": 0, "delay": 0,
    "triggers": [], "characterFilter": {"isExclude": False, "names": [], "tags": []},
}
ST_STOCK_V3 = {
    "secondary_keys": [], "selective": True, "insertion_order": 100,
    "position": "before_char", "constant": False,
    "extensions": {
        "position": 0, "exclude_recursion": False, "probability": 100,
        "useProbability": True, "depth": 4, "selectiveLogic": 0, "group": "",
        "group_override": False, "group_weight": 100, "prevent_recursion": False,
        "delay_until_recursion": 0, "scan_depth": None, "match_whole_words": None,
        "use_group_scoring": False, "case_sensitive": None, "automation_id": "",
        "role": None, "vectorized": False, "sticky": 0, "cooldown": 0, "delay": 0,
    },
}


@pytest.mark.parametrize("stash", [ST_STOCK_WORLD_INFO, ST_STOCK_V3])
def test_adopt_of_a_stock_st_entry_is_empty(stash):
    for placed in _both_places(stash):
        assert lorebook.adopt(placed) == lorebook.AdoptResult({}, ())


@pytest.mark.parametrize("stash,expected", [
    ({"priority": 100}, {}),
    ({"sticky": 0, "cooldown": 0}, {}),
    ({"excludeRecursion": False, "preventRecursion": False}, {}),
    ({"ignoreBudget": False}, {}),
    # one field off its default is written; its default-valued neighbours are not
    ({"order": 100, "sticky": 0, "cooldown": 2}, {"cooldown": "2"}),
    ({"order": 99}, {"priority": "99"}),
])
def test_adopt_skips_a_value_that_is_the_native_default(stash, expected):
    assert lorebook.adopt(stash).fields == expected


@pytest.mark.parametrize("stash,unmapped", [
    ({"delay": 0, "depth": 4, "position": 0, "groupWeight": 100, "role": 0}, ()),
    ({"position": "before_char", "group_weight": 100, "useProbability": True}, ()),
    ({"delayUntilRecursion": 0, "useGroupScoring": None}, ()),
    ({"characterFilter": {"isExclude": False, "names": [], "tags": []}}, ()),
    ({"delay": 1, "depth": 2, "position": 4, "groupWeight": 50, "role": 1},
     ("delay", "depth", "groupWeight", "position", "role")),
    ({"position": "after_char"}, ("position",)),
    ({"characterFilter": {"isExclude": True, "names": [], "tags": []}}, ("characterFilter",)),
    ({"characterFilter": {"names": ["Mara"]}}, ("characterFilter",)),
])
def test_adopt_unmapped_ignores_st_per_field_defaults(stash, unmapped):
    for placed in _both_places(stash):
        assert lorebook.adopt(placed).unmapped == unmapped


def test_adopt_unmapped_keeps_a_probability_that_is_not_always():
    assert lorebook.adopt({"probability": 99}).unmapped == ("probability",)
    assert lorebook.adopt({"probability": 0}).unmapped == ("probability",)


def test_every_unhonoured_name_is_stashed():
    assert set(lorebook._ST_EXTENSION_FIELDS).issuperset(lorebook._UNHONOURED)


def test_adopt_of_an_empty_stash_is_empty():
    res = lorebook.adopt({})
    assert res.fields == {} and res.unmapped == ()


def test_adopt_ignores_a_non_dict_extensions_object():
    assert lorebook.adopt({"extensions": "junk", "sticky": 1}).fields == {"sticky": "1"}


def test_commit_writes_native_fields_and_stash(tmp_path):
    entries = lorebook._normalize({"entries": [
        {"keys": ["tide"], "name": "Saltmarch Charter", "content": "x", "keysecondary": ["salt"],
         "selectiveLogic": 2, "sticky": 2, "probability": 50}]})
    [created] = lorebook.commit(tmp_path, entries)
    meta = entities.read_entity(tmp_path, created["kind"], created["id"])["meta"]
    assert meta["secondary_keys"] == "salt"
    assert meta["key_logic"] == "not_any"
    assert meta["sticky"] == "2"
    assert json.loads(meta["st_extensions"])["keysecondary"] == ["salt"]
    assert json.loads(meta["st_extensions"])["probability"] == 50


def test_reimport_is_still_a_noop(tmp_path):
    book = {"entries": [{"keys": ["tide"], "name": "Saltmarch Charter", "content": "x",
                         "keysecondary": ["salt"], "sticky": 2}]}
    assert len(lorebook.commit(tmp_path, lorebook._normalize(book))) == 1
    assert lorebook.commit(tmp_path, lorebook._normalize(book)) == []
    assert len(entities.list_entities(tmp_path, "lore")) == 1


def test_pending_adopt_skips_fields_the_record_has():
    stash = {"sticky": 2, "cooldown": 3, "probability": 40}
    meta = {"name": "Saltmarch Charter", "sticky": "5", "st_extensions": json.dumps(stash)}
    res = lorebook.pending_adopt(meta)
    assert res.fields == {"cooldown": "3"}
    assert res.unmapped == ("probability",)


def test_pending_adopt_treats_a_blank_value_as_not_there():
    meta = {"sticky": "  ", "st_extensions": json.dumps({"sticky": 2})}
    assert lorebook.pending_adopt(meta).fields == {"sticky": "2"}


def test_pending_adopt_is_empty_without_a_stash():
    res = lorebook.pending_adopt({"name": "Saltmarch Charter"})
    assert res.fields == {} and res.unmapped == ()


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '"text"', "null", "", 7])
def test_pending_adopt_tolerates_garbled_stash(raw):
    res = lorebook.pending_adopt({"st_extensions": raw})
    assert res.fields == {} and res.unmapped == ()
