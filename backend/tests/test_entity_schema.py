from pathlib import Path

from grimoire.store import entities, entity_schema


def test_descriptor_shape():
    assert [f["key"] for f in entity_schema.FIELDS["items"]] == ["item_type", "rarity", "holder"]
    assert [f["key"] for f in entity_schema.FIELDS["groups"]] == [
        "group_type", "leader", "headquarters"]
    assert [f["key"] for f in entity_schema.FIELDS["creatures"]] == [
        "creature_type", "threat", "habitat"]
    assert all(f["widget"] in ("text", "ref", "number", "choice")
               for fs in entity_schema.FIELDS.values() for f in fs)


def test_each_widget_carries_exactly_its_own_extras():
    """The spec is the one declaration both sides read (#221): a constraint
    lives on the spec, in the keys its widget defines, and nowhere else."""
    for fs in entity_schema.FIELDS.values():
        for f in fs:
            extras = set(f) - {"key", "label", "widget"}
            if f["widget"] == "text":
                assert extras == set(), f
            elif f["widget"] == "ref":
                assert extras <= {"kinds", "multi"} and "kinds" in extras, f
            elif f["widget"] == "number":
                assert extras <= {"min", "max"}, f
                if "min" in f and "max" in f:
                    assert f["min"] <= f["max"], f
            elif f["widget"] == "choice":
                # exactly one of a static option list or a named source
                assert extras in ({"options"}, {"source"}), f
                if "source" in f:
                    assert f["source"] in entity_schema.OPTION_SOURCES, f
                else:
                    assert f["options"] and all(isinstance(o, str) for o in f["options"]), f


def test_the_location_weather_fields_declare_their_constraints():
    specs = {f["key"]: f for f in entity_schema.FIELDS["locations"]}
    assert specs["climate"]["widget"] == "choice" and specs["climate"]["source"] == "climates"
    assert specs["persistence"]["widget"] == "number"
    assert (specs["persistence"]["min"], specs["persistence"]["max"]) == (0, 1)
    assert specs["weather_zone"]["widget"] == "text"


def test_climate_options_are_the_climate_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import climates
    spec = next(f for f in entity_schema.FIELDS["locations"] if f["key"] == "climate")
    got = entity_schema.choice_options(spec)
    assert got == [c["id"] for c in climates.list_climates()]
    assert "temperate-interior" in got


def test_a_static_choice_accepts_only_its_options():
    spec = {"key": "rarity", "label": "Rarity", "widget": "choice",
            "options": ("common", "rare")}
    assert entity_schema.valid_value(spec, "rare")
    assert not entity_schema.valid_value(spec, "legendary")
    assert not entity_schema.valid_value(spec, ["rare"])
    assert entity_schema.choice_options(spec) == ["common", "rare"]


def test_a_number_spec_bounds_only_what_it_declares():
    open_ended = {"key": "n", "label": "N", "widget": "number"}
    for v in ("-5", "0", "1e6", 3, 2.5):
        assert entity_schema.valid_value(open_ended, v), v
    for v in ("NaN", "inf", "wet", True, 10 ** 1000):
        assert not entity_schema.valid_value(open_ended, v), v
    floor_only = {"key": "n", "label": "N", "widget": "number", "min": 1}
    assert entity_schema.valid_value(floor_only, "1")
    assert entity_schema.valid_value(floor_only, "1000")
    assert not entity_schema.valid_value(floor_only, "0.5")


def test_a_text_field_must_be_a_string():
    # The store would stringify anything else and the file would carry that
    # spelling -- the same rule `upsert_content` applies to module content.
    spec = {"key": "weather_zone", "label": "Zone", "widget": "text"}
    assert entity_schema.valid_value(spec, "saltmarch")
    assert not entity_schema.valid_value(spec, ["saltmarch"])
    assert entity_schema.invalid_values("locations", {"weather_zone": 3}) == ["weather_zone"]


def test_invalid_keys():
    assert entity_schema.invalid_keys("items", {"rarity": "rare"}) == []
    assert entity_schema.invalid_keys("items", {"colour": "red", "rarity": "x"}) == ["colour"]
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


def test_a_bignum_persistence_is_rejected_not_an_overflow(monkeypatch, tmp_path):
    # `fields` is an untyped dict, so a JSON integer this large reaches the
    # validator as a Python int with no float value. Uncaught that is a 500.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    assert entity_schema.invalid_values(
        "locations", {"persistence": 10 ** 1000}) == ["persistence"]


def test_a_boolean_persistence_is_rejected(monkeypatch, tmp_path):
    # float(True) is 1.0, so this would validate — but the store writes it back
    # as the string "True", which the resolver cannot parse, so the setting
    # silently never takes effect.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    for bad in (True, False):
        assert entity_schema.invalid_values("locations", {"persistence": bad}) == ["persistence"], bad


def test_a_non_string_climate_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import entity_schema
    assert entity_schema.invalid_values("locations", {"climate": ["temperate-interior"]}) == ["climate"]


# ---- ref-valued fields (#222) ----------------------------------------------

def test_ref_specs_declare_their_candidate_kinds():
    specs = {f["key"]: f for f in entity_schema.FIELDS["groups"]}
    assert specs["leader"]["widget"] == "ref"
    assert specs["leader"]["kinds"] == ("characters", "pcs")
    assert specs["headquarters"]["kinds"] == ("locations",)
    # Single by default; only a field that means a list says so.
    assert not specs["leader"].get("multi")
    habitat = {f["key"]: f for f in entity_schema.FIELDS["creatures"]}["habitat"]
    assert habitat["multi"] is True


def test_every_ref_spec_names_at_least_one_real_kind():
    for kind, specs in entity_schema.FIELDS.items():
        for spec in specs:
            if spec["widget"] != "ref":
                assert "kinds" not in spec, (kind, spec["key"])
                continue
            assert spec["kinds"], (kind, spec["key"])
            for k in spec["kinds"]:
                assert k in entity_schema.REF_KINDS, (kind, spec["key"], k)


def test_ref_fields_are_declared_keys():
    assert entity_schema.invalid_keys("items", {"holder": "characters:mara"}) == []
    assert entity_schema.invalid_keys("groups", {"leader": "pcs:seraphine"}) == []
    assert entity_schema.invalid_keys("creatures", {"habitat": "locations:saltmarch"}) == []


def test_a_well_formed_ref_passes():
    assert entity_schema.invalid_values("items", {"holder": "characters:mara"}) == []
    assert entity_schema.invalid_values("groups", {"headquarters": "locations:saltmarch"}) == []


def test_a_ref_naming_a_kind_the_field_does_not_accept_is_rejected():
    # A group is led by a person, not by a place.
    assert entity_schema.invalid_values("groups", {"leader": "locations:saltmarch"}) == ["leader"]
    # ...and headquartered in a place, not in a person.
    assert entity_schema.invalid_values("groups", {"headquarters": "characters:mara"}) == ["headquarters"]


def test_a_bare_id_with_no_kind_is_rejected():
    # `mara` could name a character, a location or nothing; the store cannot
    # resolve it and the picker never produces it.
    assert entity_schema.invalid_values("items", {"holder": "mara"}) == ["holder"]


def test_a_ref_whose_id_is_not_a_safe_id_is_rejected():
    for bad in ("characters:../../etc/passwd", "characters:", "characters:.",
                "characters:a:b", ":mara"):
        assert entity_schema.invalid_values("items", {"holder": bad}) == ["holder"], bad


def test_a_single_ref_field_rejects_a_list_of_refs():
    assert entity_schema.invalid_values(
        "groups", {"leader": "characters:mara, characters:winifred"}) == ["leader"]


def test_a_multi_ref_field_accepts_several_and_rejects_one_bad_one():
    assert entity_schema.invalid_values(
        "creatures", {"habitat": "locations:saltmarch, locations:realm"}) == []
    assert entity_schema.invalid_values(
        "creatures", {"habitat": "locations:saltmarch, characters:mara"}) == ["habitat"]


def test_a_non_string_ref_is_rejected():
    assert entity_schema.invalid_values("items", {"holder": ["characters:mara"]}) == ["holder"]
    assert entity_schema.invalid_values("items", {"holder": True}) == ["holder"]


def test_an_empty_ref_field_is_a_clear_not_a_rejection():
    # The editor sends "" for a ref the user never picked or has just cleared.
    assert entity_schema.invalid_values("groups", {"leader": ""}) == []


def test_ref_fields_are_not_checked_for_existence(tmp_path: Path):
    # Deliberate: see the module docstring. A ref may name a record this scope
    # cannot see (a campaign tombstone) or one that does not exist yet, and a
    # save must not depend on the order the two records were written in.
    assert entity_schema.invalid_values("items", {"holder": "characters:nobody-at-all"}) == []


def test_parse_refs_matches_the_owners_spelling():
    assert entity_schema.parse_refs(" locations:a ,, locations:b ") == ["locations:a", "locations:b"]
    assert entity_schema.parse_refs("") == []
    assert entity_schema.parse_refs(None) == []


def test_ref_fields_round_trip_as_frontmatter_scalars(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "creatures", "Marsh Wyrm", "big",
                                 fields={"habitat": "locations:saltmarch, locations:realm"})
    got = entities.read_entity(tmp_path, "creatures", eid)
    assert got["meta"]["habitat"] == "locations:saltmarch, locations:realm"
    entities.update_entity(tmp_path, "creatures", eid, fields={"habitat": ""})
    assert "habitat" not in entities.read_entity(tmp_path, "creatures", eid)["meta"]


def test_ref_kinds_is_the_real_set_of_records(monkeypatch, tmp_path):
    # REF_KINDS is spelled out in entity_schema rather than imported, so that
    # `entities` can import this module for the reclassify rewriter without a
    # cycle. This is what keeps the copy honest when a new kind ships.
    from grimoire.store import appearances
    assert set(entity_schema.REF_KINDS) == set(entities.ENTITY_KINDS) | set(appearances.ACTOR_KINDS)


def test_an_id_carrying_the_list_delimiter_cannot_be_referenced():
    # The comma separates refs, so `locations:salt,march` parses as two. slugify
    # cannot produce such an id; a hand-authored or imported file can, and the
    # picker filters it out rather than offering a candidate nothing can save.
    assert entity_schema.referenceable("saltmarch")
    assert not entity_schema.referenceable("salt,march")
    assert entity_schema.invalid_values(
        "creatures", {"habitat": "locations:salt,march"}) == ["habitat"]


def test_referenceable_still_carries_everything_safe_id_rejects():
    # The comma rule is ON TOP of safe_id, not instead of it.
    for bad in ("../escape", "a/b", "", ".", "..", "a:b", "trailing ", "trailing."):
        assert not entity_schema.referenceable(bad), bad


# Every character `str.splitlines` treats as a line boundary. Enumerated rather
# than sampled: the first version of this rule checked \n and \r, and the other
# eight went through silently.
LINE_BOUNDARIES = ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
                   "\x85", "\u2028", "\u2029")


def test_a_line_break_in_a_ref_id_is_rejected():
    # A ref lives in a single-line frontmatter scalar. A newline does not
    # survive the round trip: it is written into the value, the parser reads
    # only the first line, and the record comes back truncated -- a save that
    # reports success and corrupts the file.
    for sep in LINE_BOUNDARIES:
        assert not entity_schema.referenceable(f"a{sep}b"), repr(sep)
        assert entity_schema.invalid_values(
            "items", {"holder": f"characters:a{sep}b"}) == ["holder"], repr(sep)


def test_a_character_that_survives_the_round_trip_is_not_rejected():
    # Line breaks only. A tab round-trips intact, so rejecting one would be a
    # rule with no failure behind it.
    assert entity_schema.referenceable("a\tb")


def test_referenceable_agrees_with_what_frontmatter_can_carry():
    # The rule stated as the property it exists for, against the real writer,
    # so a change to either side has to keep them in step.
    from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
    ok = ("saltmarch", "salt-march", "a\tb", "a b", "a.b")
    for eid in (*ok, *(f"a{sep}b" for sep in LINE_BOUNDARIES)):
        value = f"locations:{eid}"
        back = parse_frontmatter(dump_frontmatter({"name": "X", "habitat": value}, "b"))[0]
        assert entity_schema.referenceable(eid) == (back.get("habitat") == value), eid
