"""Boundary contracts shared by projections, scene ideas, and actor snapshots."""

import pytest

from grimoire.store import _idea_provenance as idea_provenance
from grimoire.store import characters, fieldtext, pcs
from grimoire.store.absorb import conflicts


@pytest.mark.parametrize("value, expected", [
    (None, " fallback "), (False, " fallback "), (42, " fallback "),
    ([], " fallback "), ({"title": "Realm"}, " fallback "),
    ("", ""), (" \t\n", ""), ("\u00a0Mara\u00a0", "Mara"),
])
def test_text_only_strips_actual_strings_and_keeps_fallback_spelling(value, expected):
    assert fieldtext.text(value, " fallback ") == expected


def test_scene_idea_shapes_keep_valid_reference_spelling_and_empty_other_fields():
    driver = {"ref": " thread:realm ", "action": "", "extra": "discard"}
    anchor = {"ref": " event:realm ", "relation": "", "native": "", "extra": "discard"}
    assert idea_provenance.drivers([None, {"ref": " ", "action": "advance"}, driver]) == [
        {"ref": " thread:realm ", "action": ""}]
    assert idea_provenance.anchor(anchor) == {"ref": " event:realm ", "relation": "", "native": ""}
    assert driver["extra"] == anchor["extra"] == "discard"


@pytest.mark.parametrize("actor", [characters, pcs])
def test_actor_snapshot_hash_preserves_order_utf8_and_existing_concatenation(actor):
    pairs = [("Mara", "\u03b1"), ("Realm", "\U0001f332")]
    assert actor.dir_content_hash(pairs) == "4ef5c6076e54c7aaea22ba376982bb786655ca015802bf4e50125c5ab5d126cf"
    assert actor.dir_content_hash(pairs) != actor.dir_content_hash(list(reversed(pairs)))
    # Existing synchronization hashes concatenate without separators.
    assert actor.dir_content_hash(pairs) == actor.dir_content_hash([("Mara\u03b1Realm", "\U0001f332")])


@pytest.mark.parametrize("record, expected", [
    ({"status": " active ", "text": " Realm "}, "active \u2014 Realm"),
    ({"status": [], "text": {"title": "Realm"}, "superseded_by": 42}, "active"),
    ({"status": "retired", "text": "Realm", "superseded_by": " Mara "},
     "retired (superseded by Mara) \u2014 Realm"),
])
def test_fact_conflict_fingerprints_normalize_hand_edited_fields(record, expected):
    assert conflicts.fact_line(record) == expected
