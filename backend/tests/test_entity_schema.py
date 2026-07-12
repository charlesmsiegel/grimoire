from pathlib import Path

from grimoire.store import entities, entity_schema


def test_descriptor_shape():
    assert [f["key"] for f in entity_schema.FIELDS["items"]] == ["item_type", "rarity"]
    assert [f["key"] for f in entity_schema.FIELDS["groups"]] == ["group_type"]
    assert [f["key"] for f in entity_schema.FIELDS["creatures"]] == ["creature_type", "threat"]
    assert all(f["widget"] == "text" for fs in entity_schema.FIELDS.values() for f in fs)


def test_invalid_keys():
    assert entity_schema.invalid_keys("items", {"rarity": "rare"}) == []
    assert entity_schema.invalid_keys("items", {"holder": "mara", "rarity": "x"}) == ["holder"]
    assert entity_schema.invalid_keys("lore", {"rarity": "x"}) == ["rarity"]  # lore declares no fields


def test_fields_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "items", "Salt Knife", "sharp",
                                 fields={"item_type": "weapon", "rarity": ""})
    got = entities.read_entity(tmp_path, "items", eid)
    assert got["meta"]["item_type"] == "weapon"
    assert "rarity" not in got["meta"]                       # empty omitted on create
    entities.update_entity(tmp_path, "items", eid, fields={"rarity": "rare"})
    assert entities.read_entity(tmp_path, "items", eid)["meta"]["rarity"] == "rare"
    entities.update_entity(tmp_path, "items", eid, fields={"rarity": ""})
    got = entities.read_entity(tmp_path, "items", eid)
    assert "rarity" not in got["meta"]                       # empty clears on update
    assert got["meta"]["item_type"] == "weapon"              # untouched key preserved
    assert got["body"].strip() == "sharp"


def test_fields_survive_in_list_summaries(tmp_path: Path):
    entities.create_entity(tmp_path, "creatures", "Marsh Wyrm", "x", fields={"threat": "apex"})
    assert entities.list_entities(tmp_path, "creatures")[0]["threat"] == "apex"
