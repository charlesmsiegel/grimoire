from pathlib import Path

import pytest

from grimoire.store import entities


def test_create_read_and_stable_id(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "locations", "Drowned Library", "Halls of brine.")
    assert eid == "drowned-library"
    got = entities.read_entity(tmp_path, "locations", eid)
    assert got["meta"]["name"] == "Drowned Library"
    assert got["body"].strip() == "Halls of brine."
    # renaming keeps the id; only the name frontmatter changes
    entities.update_entity(tmp_path, "locations", eid, name="The Drowned Library")
    assert eid == "drowned-library"
    assert entities.read_entity(tmp_path, "locations", eid)["meta"]["name"] == "The Drowned Library"


def test_collision_suffix(tmp_path: Path):
    a = entities.create_entity(tmp_path, "locations", "Echo")
    b = entities.create_entity(tmp_path, "locations", "Echo")
    assert a == "echo"
    assert b == "echo-2"


def test_synced_refs_includes_greetings(tmp_path: Path):
    (tmp_path / "locations").mkdir()
    (tmp_path / "locations" / "inn.md").write_text("---\nname: Inn\n---\n", encoding="utf-8")
    (tmp_path / "greetings").mkdir()
    (tmp_path / "greetings" / "gala.md").write_text("---\nname: Gala\n---\n", encoding="utf-8")
    assert entities.synced_refs(tmp_path) == [("locations", "inn"), ("greetings", "gala")]
    # greetings are synced but not generic-CRUD entities
    assert "greetings" in entities.SYNCED_KINDS
    assert "greetings" not in entities.ENTITY_KINDS


def test_characters_is_not_a_generic_kind(tmp_path: Path):
    assert "characters" not in entities.ENTITY_KINDS
    with pytest.raises(entities.UnknownKind):
        entities.create_entity(tmp_path, "characters", "X")


def test_unknown_kind_raises(tmp_path: Path):
    with pytest.raises(entities.UnknownKind):
        entities.create_entity(tmp_path, "weapons", "Sword")


def test_missing_entity_raises(tmp_path: Path):
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(tmp_path, "lore", "nope")


def test_hash_changes_only_with_content(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Salt Pact", "Old.")
    h1 = entities.entity_hash(tmp_path, "lore", eid)
    entities.update_entity(tmp_path, "lore", eid, body="Old.")  # no change
    assert entities.entity_hash(tmp_path, "lore", eid) == h1
    entities.update_entity(tmp_path, "lore", eid, body="New.")
    assert entities.entity_hash(tmp_path, "lore", eid) != h1
    assert entities.entity_hash(tmp_path, "lore", "absent") is None


def test_traversal_ids_are_rejected(tmp_path: Path):
    # an id that would escape the kind dir is treated as not-found, never read
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(tmp_path, "locations", "../../secret")
    with pytest.raises(entities.EntityNotFound):
        entities.delete_entity(tmp_path, "locations", "..")
    assert entities.entity_hash(tmp_path, "locations", "../x") is None


def test_keys_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Salt Pact", "the pact", keys="pact, salt")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["keys"] == "pact, salt"
    # update can change keys without touching the body
    entities.update_entity(tmp_path, "lore", eid, keys="pact")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["keys"] == "pact"
    assert got["body"].strip() == "the pact"
    # entities without keys read as empty string
    e2 = entities.create_entity(tmp_path, "lore", "No Keys", "x")
    assert entities.read_entity(tmp_path, "lore", e2)["meta"].get("keys", "") == ""


def test_owners_round_trip(tmp_path: Path):
    eid = entities.create_entity(
        tmp_path, "lore", "Tanaka's exile", "He was cast out.",
        keys="exile", owners="characters:master-tanaka, locations:old-dojo",
    )
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["owners"] == "characters:master-tanaka, locations:old-dojo"
    assert got["meta"]["keys"] == "exile"


def test_owners_absent_when_empty(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "World fact", "Always true.")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert "owners" not in got["meta"]  # mirror keys: omit when empty


def test_update_owners(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Fact", "x")
    entities.update_entity(tmp_path, "lore", eid, owners="pcs:hero")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["owners"] == "pcs:hero"
    # body/name untouched
    assert entities.read_entity(tmp_path, "lore", eid)["body"].strip() == "x"


def test_all_refs_and_counts(tmp_path: Path):
    entities.create_entity(tmp_path, "lore", "A")
    entities.create_entity(tmp_path, "locations", "B")
    assert set(entities.all_refs(tmp_path)) == {("lore", "a"), ("locations", "b")}
    assert entities.entity_counts(tmp_path) == {"locations": 1, "lore": 1}
