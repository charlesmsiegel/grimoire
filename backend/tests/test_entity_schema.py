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


def test_locations_accept_weather_fields():
    from grimoire.store import entity_schema
    assert entity_schema.invalid_keys(
        "locations", {"climate": "temperate-interior", "persistence": "0.3",
                      "weather_zone": "saltmarch"}) == []


def test_valid_location_weather_values_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    assert entity_schema.invalid_values(
        "locations", {"climate": "temperate-interior", "persistence": "0.3",
                      "weather_zone": "anything-goes"}) == []


def test_unknown_climate_is_rejected_at_save(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    assert entity_schema.invalid_values(
        "locations", {"climate": "temperate-costal"}) == ["climate"]


def test_out_of_range_persistence_is_rejected_at_save(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    for bad in ("2", "-1", "NaN", "wet"):
        assert entity_schema.invalid_values("locations", {"persistence": bad}) == ["persistence"], bad


def test_empty_values_are_treated_as_clears_not_rejections(monkeypatch, tmp_path):
    # EntityEditor sends "" for a field the user cleared or never set, and the
    # store drops empties only after route validation.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    assert entity_schema.invalid_values(
        "locations", {"climate": "", "persistence": ""}) == []


def test_boundary_persistence_values_are_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    for good in ("0", "1", "0.0", "1.0"):
        assert entity_schema.invalid_values("locations", {"persistence": good}) == [], good


def test_other_kinds_are_unaffected():
    from grimoire.store import entity_schema
    assert entity_schema.invalid_values("items", {"item_type": "anything"}) == []
