"""The shelf's pure model and codec (docs/SHELF.md): tag names, marks, saved filters and their documents."""

from __future__ import annotations

import pytest

from grimoire.core.identity import ChatRef, SourceRef
from grimoire.shelf import (
    CHAT_FORMAT, ENTRY_FORMAT, FILTER_FORMAT, MAX_TAG_CHARS, MAX_TAGS, ChatMarks, EntryMarks, SavedFilter, ShelfError,
    chat_from_document, chat_to_document, clean_tags, edit_tags, entry_from_document, entry_to_document,
    filter_from_document, filter_to_document, record_name, respell, tag_key, tag_name, vocabulary,
)

WREN = SourceRef.parse("world:harbor/character/wren")
CHAT = ChatRef.parse("campaign:quay#chat/main")


def _refused(reason: str, work) -> ShelfError:  # type: ignore[no-untyped-def]
    with pytest.raises(ShelfError) as refused:
        work()
    assert refused.value.reason == reason, str(refused.value)
    return refused.value


# -- tag names ---------------------------------------------------------------------------------------------
def test_a_tag_is_trimmed_single_spaced_and_normalized():
    assert tag_name("  slow   burn ") == "slow burn"
    assert tag_name("café") == "café"  # NFC: one spelling of one name
    assert tag_key("Slow Burn") == tag_key("slow burn") == "slow burn"


@pytest.mark.parametrize("bad", ["", "   ", "a,b", "line\nbreak", "tab\there", "bell\x07", "zero​width",
                                 "over‮ride", "x" * (MAX_TAG_CHARS + 1), None, 7, ["crew"]])
def test_a_tag_that_is_not_a_plain_short_name_is_refused_with_a_reason(bad):
    error = _refused("invalid_tag", lambda: tag_name(bad))
    assert str(error)


def test_a_tag_of_exactly_the_limit_is_kept():
    assert tag_name("x" * MAX_TAG_CHARS) == "x" * MAX_TAG_CHARS


def test_tags_are_unique_whatever_their_case_and_kept_in_key_order():
    assert clean_tags(["noir", "Crew", "NOIR", " crew "]) == ("Crew", "noir")  # the first spelling given wins
    _refused("invalid_tag", lambda: clean_tags("crew"))  # a bare string is not a list of names
    _refused("invalid_tag", lambda: clean_tags({"crew": 1}))
    _refused("too_many_tags", lambda: clean_tags([f"t{n}" for n in range(MAX_TAGS + 1)]))
    assert len(clean_tags([f"t{n}" for n in range(MAX_TAGS)])) == MAX_TAGS


def test_editing_tags_adds_and_removes_by_key_and_keeps_the_rest():
    assert edit_tags(("Crew", "noir"), add=["harbor", "CREW"], remove=["Noir"]) == ("Crew", "harbor")
    assert edit_tags((), add=[], remove=["gone"]) == ()
    _refused("invalid_tag", lambda: edit_tags(("crew",), add=["Noir"], remove=["noir"]))


def test_one_tag_shows_in_one_spelling_the_same_on_every_device():
    sets = [("Noir", "crew"), ("noir",), ("Noir",), ("Crew",)]
    known = vocabulary(sets)
    assert known == {"noir": "Noir", "crew": "Crew"}  # the most used; among equals the first in code-point order
    assert vocabulary(reversed(sets)) == known          # whatever order the files are read in
    assert respell(("noir", "new"), known) == ("new", "Noir")


# -- marks and their documents -----------------------------------------------------------------------------
def test_an_entrys_marks_round_trip_and_are_written_the_same_whatever_order_they_were_given():
    marks = EntryMarks(WREN, ("noir", "Crew"), True)
    document = entry_to_document(marks)
    assert document == {"format": ENTRY_FORMAT, "ref": "world:harbor/character/wren", "tags": ["Crew", "noir"],
                        "pinned": True}
    assert entry_from_document(document) == marks
    assert entry_to_document(EntryMarks(WREN, ("Crew", "noir"), True)) == document
    assert EntryMarks(WREN).empty and not marks.empty


def test_marks_never_name_one_revision():
    _refused("invalid_ref", lambda: EntryMarks(WREN.at("r2")))
    _refused("malformed", lambda: entry_from_document(
        {"format": ENTRY_FORMAT, "ref": "world:harbor/character/wren@r2", "tags": [], "pinned": True}))


@pytest.mark.parametrize("change, reason", [
    ({"format": "grimoire.shelf.entry/2"}, "unsupported_format"),
    ({"format": "grimoire.record/1"}, "malformed"),
    ({"format": None}, "malformed"),
    ({"ref": "wren"}, "malformed"),
    ({"ref": 4}, "malformed"),
    ({"tags": "crew"}, "malformed"),
    ({"tags": ["crew", "Crew"]}, "malformed"),
    ({"tags": [" crew"]}, "malformed"),
    ({"tags": ["a,b"]}, "malformed"),
    ({"tags": [3]}, "malformed"),
    ({"pinned": "yes"}, "malformed"),
    ({"pinned": 1}, "malformed"),
    ({"note": "extra"}, "malformed"),
])
def test_an_entry_document_that_is_not_exactly_one_is_refused_with_its_reason(change, reason):
    document = {"format": ENTRY_FORMAT, "ref": str(WREN), "tags": ["crew"], "pinned": False, **change}
    error = _refused(reason, lambda: entry_from_document(document))
    assert str(error)


def test_a_document_missing_a_key_or_of_another_shape_is_refused():
    _refused("malformed", lambda: entry_from_document({"format": ENTRY_FORMAT, "ref": str(WREN), "tags": []}))
    for junk in (None, [], "text", 3):
        _refused("malformed", lambda junk=junk: entry_from_document(junk))
        _refused("malformed", lambda junk=junk: chat_from_document(junk))
        _refused("malformed", lambda junk=junk: filter_from_document(junk))


def test_a_chats_marks_round_trip():
    marks = ChatMarks(CHAT, True)
    assert chat_to_document(marks) == {"format": CHAT_FORMAT, "chat": "campaign:quay#chat/main", "pinned": True}
    assert chat_from_document(chat_to_document(marks)) == marks
    _refused("malformed", lambda: chat_from_document({"format": CHAT_FORMAT, "chat": "quay", "pinned": True}))
    _refused("malformed", lambda: chat_from_document({"format": CHAT_FORMAT, "chat": str(CHAT), "pinned": None}))
    _refused("unsupported_format", lambda: chat_from_document({"format": "grimoire.shelf.chat/9"}))


def test_a_saved_filter_round_trips_with_everything_it_names():
    saved = SavedFilter("f-1", "  Harbor   crew ", role="character", source="world:harbor", tags=("noir", "Crew"),
                        query=" mara ", sort="played")
    assert saved.name == "Harbor crew" and saved.query == "mara" and saved.tags == ("Crew", "noir")
    document = filter_to_document(saved)
    assert document == {"format": FILTER_FORMAT, "id": "f-1", "name": "Harbor crew", "role": "character", "shelf": "",
                        "source": "world:harbor", "tags": ["Crew", "noir"], "query": "mara", "sort": "played"}
    assert filter_from_document(document) == saved
    assert filter_from_document(filter_to_document(SavedFilter("f-2", "Pinned", shelf="pinned"))).shelf == "pinned"
    assert saved.renamed("Quay") == SavedFilter("f-1", "Quay", "character", "", "world:harbor", ("Crew", "noir"),
                                                "mara", "played")


@pytest.mark.parametrize("fields, reason", [
    ({"id": "F 1"}, "invalid_filter"),
    ({"name": ""}, "invalid_name"),
    ({"name": "x" * 61}, "invalid_name"),
    ({"name": "two\nlines"}, "invalid_name"),
    ({"role": "Characters!"}, "invalid_filter"),
    ({"sort": 3}, "invalid_filter"),
    ({"source": "not a source"}, "invalid_filter"),
    ({"source": "world:harbor", "shelf": "cards"}, "invalid_filter"),
    ({"query": "x" * 201}, "invalid_filter"),
    ({"query": "bell\x07"}, "invalid_filter"),
    ({"tags": ("a,b",)}, "invalid_tag"),
])
def test_a_saved_filter_that_is_not_one_is_refused(fields, reason):
    _refused(reason, lambda: SavedFilter(**{"id": "f-1", "name": "Crew", **fields}))


def test_a_filter_document_a_hand_edit_broke_is_refused_as_malformed():
    good = filter_to_document(SavedFilter("f-1", "Crew", tags=("crew",)))
    for change in ({"name": " Crew"}, {"name": ""}, {"source": 3}, {"tags": ["Crew", "crew"]}, {"id": "F1"},
                   {"query": "  spaced  "}, {"more": 1}):
        _refused("malformed", lambda change=change: filter_from_document({**good, **change}))
    _refused("unsupported_format", lambda: filter_from_document({**good, "format": "grimoire.shelf.filter/2"}))


def test_a_records_file_name_is_a_digest_of_its_exact_ref():
    assert record_name(WREN) != record_name(SourceRef.parse("world:harbor/character/wren-2"))
    assert record_name(WREN) == record_name(SourceRef.parse(str(WREN))) and len(record_name(CHAT)) == 32
    assert set(record_name(CHAT)) <= set("0123456789abcdef")
