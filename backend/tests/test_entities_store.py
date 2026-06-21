from pathlib import Path

import pytest

from grimoire.store import entities


def test_create_read_and_stable_id(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "characters", "Seraphine", "Keeper of the library.")
    assert eid == "seraphine"
    got = entities.read_entity(tmp_path, "characters", eid)
    assert got["meta"]["name"] == "Seraphine"
    assert got["body"].strip() == "Keeper of the library."
    # renaming keeps the id; only the name frontmatter changes
    entities.update_entity(tmp_path, "characters", eid, name="Seraphine the Drowned")
    assert eid == "seraphine"
    assert entities.read_entity(tmp_path, "characters", eid)["meta"]["name"] == "Seraphine the Drowned"


def test_collision_suffix(tmp_path: Path):
    a = entities.create_entity(tmp_path, "characters", "Echo")
    b = entities.create_entity(tmp_path, "characters", "Echo")
    assert a == "echo"
    assert b == "echo-2"


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
        entities.read_entity(tmp_path, "characters", "../../secret")
    with pytest.raises(entities.EntityNotFound):
        entities.delete_entity(tmp_path, "characters", "..")
    assert entities.entity_hash(tmp_path, "characters", "../x") is None


def test_all_refs_and_counts(tmp_path: Path):
    entities.create_entity(tmp_path, "characters", "A")
    entities.create_entity(tmp_path, "locations", "B")
    assert set(entities.all_refs(tmp_path)) == {("characters", "a"), ("locations", "b")}
    assert entities.entity_counts(tmp_path) == {"characters": 1, "locations": 1, "lore": 0}
