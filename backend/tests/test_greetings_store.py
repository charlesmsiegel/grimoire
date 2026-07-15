from pathlib import Path

import pytest

from grimoire.store import characters, greetings


def _world(tmp_path) -> Path:
    (tmp_path / "greetings").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_create_read_list_roundtrip(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "Rescued at Sea", "seraphine", "default",
                                    body="You wake on the deck.",
                                    requires_tags=["sailor"], predecessor_join="any")
    g = greetings.read_greeting(root, gid)
    assert g["meta"]["character"] == "seraphine"
    assert g["meta"]["version"] == "default"
    assert g["meta"]["requires_tags"] == ["sailor"]
    assert g["meta"]["predecessor_join"] == "any"
    assert g["body"].strip() == "You wake on the deck."
    assert [x["id"] for x in greetings.list_greetings(root)] == [gid]


def test_create_greeting_bakes_char_macro_to_its_character(tmp_path):
    # #137: {{char}} in a greeting body is the greeting's own associated
    # character -- unambiguous, baked at creation, never left for scene-time
    # substitution (which would be ambiguous with more than one NPC present).
    root = _world(tmp_path)
    characters.create_character(root, "Seraphine", "default")
    gid = greetings.create_greeting(root, "Open", "seraphine", "default",
                                    body="{{char}} looks up from the ledger.")
    assert greetings.read_greeting(root, gid)["body"].strip() == \
        "Seraphine looks up from the ledger."


def test_create_greeting_without_character_leaves_char_macro_literal(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "Cold open", "", "", body="{{char}} is nowhere in sight.")
    assert greetings.read_greeting(root, gid)["body"].strip() == "{{char}} is nowhere in sight."


def test_create_greeting_bakes_to_the_versions_own_card_name(tmp_path):
    # #137 P2: a card's name is version-specific -- self-reference means
    # "this card", not the character container's original creation name.
    root = _world(tmp_path)
    cid, _ = characters.create_character(root, "Seraphine", "default")
    card = characters.blank_card("Sera Alter")
    characters.create_version(root, cid, "alter", card)
    gid = greetings.create_greeting(root, "Open", cid, "alter", body="{{char}} enters.")
    assert greetings.read_greeting(root, gid)["body"].strip() == "Sera Alter enters."


def test_update_greeting_bakes_char_macro(tmp_path):
    root = _world(tmp_path)
    characters.create_character(root, "Seraphine", "default")
    gid = greetings.create_greeting(root, "Open", "seraphine", "default", body="Hello.")
    greetings.update_greeting(root, gid, body="{{char}} returns.")
    assert greetings.read_greeting(root, gid)["body"].strip() == "Seraphine returns."


def test_update_and_missing(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "G", "c", "v")
    greetings.update_greeting(root, gid, body="new text", requires_tags=["a", "b"])
    g = greetings.read_greeting(root, gid)
    assert g["body"].strip() == "new text"
    assert g["meta"]["requires_tags"] == ["a", "b"]
    with pytest.raises(greetings.GreetingNotFound):
        greetings.read_greeting(root, "nope")


def test_present_cast_roundtrips_and_defaults(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "Arrival: Mara & Rowan", "mara", "main",
                                    body="Mara and Rowan greet you.", present=["mara", "rowan"])
    assert greetings.read_greeting(root, gid)["meta"]["present"] == ["mara", "rowan"]
    # absent present -> defaults to just the primary character
    g2 = greetings.create_greeting(root, "Solo", "mara", "main", body="hi")
    assert greetings.read_greeting(root, g2)["meta"]["present"] == ["mara"]


def test_update_present(tmp_path):
    root = _world(tmp_path)
    gid = greetings.create_greeting(root, "G", "mara", "main", present=["mara"])
    greetings.update_greeting(root, gid, present=["mara", "rowan"])
    assert greetings.read_greeting(root, gid)["meta"]["present"] == ["mara", "rowan"]


def test_present_in_detects_source_and_mentioned_names():
    roster = {"Mara": "mara", "Rowan": "rowan", "Lys": "lys"}
    body = "{{char}} waits. Then Rowan walks in with you, {{user}}."
    # source mara ({{char}}) + Rowan mentioned; Lys absent; {{user}} is not a character
    assert greetings.present_in(body, "mara", roster) == ["mara", "rowan"]


def test_present_in_matches_whole_words_only():
    roster = {"Art": "art", "Mara": "mara"}
    # 'Art' is a substring of 'artwork' and must NOT count as present
    assert greetings.present_in("The artwork is heavy.", "mara", roster) == ["mara"]


def test_predecessors_of(tmp_path):
    root = _world(tmp_path)
    a = greetings.create_greeting(root, "A", "c", "v")
    a2 = greetings.create_greeting(root, "A2", "c", "v")
    b = greetings.create_greeting(root, "B", "c", "v")
    greetings.set_edges(root, a, leads_to=[b])
    greetings.set_edges(root, a2, leads_to=[b])
    pm = greetings.read_plotmap(root)
    assert greetings.predecessors_of(pm, b) == [a, a2]   # both lead to b, sorted
    assert greetings.predecessors_of(pm, a) == []


def test_plotmap_edges_and_delete_prunes(tmp_path):
    root = _world(tmp_path)
    a = greetings.create_greeting(root, "A", "c", "v")
    b = greetings.create_greeting(root, "B", "c", "v")
    greetings.set_edges(root, a, leads_to=[b], excludes=[b])
    assert greetings.read_plotmap(root)[a] == {"leads_to": [b], "excludes": [b]}
    greetings.delete_greeting(root, b)
    pm = greetings.read_plotmap(root)
    assert b not in pm
    assert pm[a]["leads_to"] == [] and pm[a]["excludes"] == []


def test_import_from_character(tmp_path):
    root = _world(tmp_path)
    card = characters.blank_card("Seraphine")
    card["data"].update(first_mes="Hello there.",
                         alternate_greetings=["Alt one.", "  ", "Alt two."])
    characters.create_character(root, "Seraphine", "default", card)
    gids = greetings.import_from_character(root, "seraphine", "default")
    # first_mes + 2 non-blank alternates (the blank alt is skipped)
    assert len(gids) == 3
    bodies = sorted(greetings.read_greeting(root, g)["body"].strip() for g in gids)
    assert bodies == ["Alt one.", "Alt two.", "Hello there."]
    assert all(greetings.read_greeting(root, g)["meta"]["character"] == "seraphine" for g in gids)


def test_import_sets_present_from_mentions(tmp_path):
    root = _world(tmp_path)
    characters.create_character(root, "Rowan", "default", characters.blank_card("Rowan"))
    card = characters.blank_card("Seraphine")
    card["data"].update(first_mes="Seraphine and Rowan greet you.", alternate_greetings=[])
    characters.create_character(root, "Seraphine", "default", card)
    gids = greetings.import_from_character(root, "seraphine", "default")
    g = greetings.read_greeting(root, gids[0])
    assert set(g["meta"]["present"]) == {"seraphine", "rowan"}


def test_import_empty_card_returns_empty(tmp_path):
    root = _world(tmp_path)
    characters.create_character(root, "Blank", "default", characters.blank_card("Blank"))
    assert greetings.import_from_character(root, "blank", "default") == []


def test_availability_gate_join_exclusion_tags(tmp_path):
    root = _world(tmp_path)
    a = greetings.create_greeting(root, "A", "c", "v")
    b = greetings.create_greeting(root, "B", "c", "v", predecessor_join="all")
    c = greetings.create_greeting(root, "C", "c", "v", predecessor_join="any")
    greetings.set_edges(root, a, leads_to=[b, c])
    a2 = greetings.create_greeting(root, "A2", "c", "v")
    greetings.set_edges(root, a2, leads_to=[b])  # b now has preds {a, a2}
    locked = greetings.create_greeting(root, "Locked", "c", "v", requires_tags=["vip"])
    excl = greetings.create_greeting(root, "Excl", "c", "v")
    greetings.set_edges(root, a, excludes=[excl])

    pm = greetings.read_plotmap(root)
    items = greetings.list_greetings(root)
    # nothing played, no tags
    avail = {x["id"]: x["available"] for x in greetings.availability(items, pm, set(), set())}
    assert avail[a] is True            # no predecessors
    assert avail[b] is False           # all-join, no preds played
    assert avail[c] is False           # any-join, no preds played
    assert avail[locked] is False      # missing tag
    assert avail[excl] is True         # excluder not played yet

    avail = {x["id"]: x["available"] for x in greetings.availability(items, pm, {a}, {"vip"})}
    assert avail[c] is True            # any-join satisfied by a
    assert avail[b] is False           # all-join still needs a2
    assert avail[locked] is True       # tag now present
    assert avail[excl] is False        # a played -> excl excluded (symmetric)

    avail = {x["id"]: x["available"] for x in greetings.availability(items, pm, {a, a2}, set())}
    assert avail[b] is True            # all preds played


def test_plotmap_hash_roundtrip(tmp_path):
    assert greetings.plotmap_hash(tmp_path) is None
    g = greetings.create_greeting(tmp_path, "A", "c", "v")
    greetings.set_edges(tmp_path, g, leads_to=["b"])
    h1 = greetings.plotmap_hash(tmp_path)
    assert h1
    greetings.set_edges(tmp_path, g, leads_to=["b", "c"])
    assert greetings.plotmap_hash(tmp_path) != h1


def test_availability_skipped_dropped_and_pruned(tmp_path):
    a = greetings.create_greeting(tmp_path, "A", "c", "v")
    b = greetings.create_greeting(tmp_path, "B", "c", "v")
    c = greetings.create_greeting(tmp_path, "C", "c", "v")
    greetings.set_edges(tmp_path, a, leads_to=[c])
    greetings.set_edges(tmp_path, b, leads_to=[c])
    pm = greetings.read_plotmap(tmp_path)
    # skip A: it vanishes from the output, and C's "all" join is satisfied by B alone
    out = {g["id"]: g for g in greetings.availability(greetings.list_greetings(tmp_path), pm, {b}, set(), skipped={a})}
    assert a not in out
    assert out[c]["available"] is True


def test_availability_sole_predecessor_skipped_frees_successor(tmp_path):
    a = greetings.create_greeting(tmp_path, "A", "c", "v")
    c = greetings.create_greeting(tmp_path, "C", "c", "v")
    greetings.set_edges(tmp_path, a, leads_to=[c])
    pm = greetings.read_plotmap(tmp_path)
    out = {g["id"]: g for g in greetings.availability(greetings.list_greetings(tmp_path), pm, set(), set(), skipped={a})}
    assert out[c]["available"] is True


def test_list_greetings_sorts_names_naturally(tmp_path):
    for name in ["A10", "SoL 19", "A1c", "SoL 2", "A2", "sol 10", "A1", "B1"]:
        greetings.create_greeting(tmp_path, name, "ann", "default", "body")
    assert [g["name"] for g in greetings.list_greetings(tmp_path)] == [
        "A1", "A1c", "A2", "A10", "B1", "SoL 2", "sol 10", "SoL 19"]
