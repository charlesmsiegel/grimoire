import json

import pytest

from grimoire.store import modules


GOOD_SHEETS = {
    "groups": {
        "attributes": {
            "label": "Attributes",
            "fields": [
                {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1},
                {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1},
            ],
            "derived": {"reflex": "min(vigor, wits)"},
        },
    },
    "sheet_types": {
        "warden": {
            "label": "Warden",
            "kind": "characters",
            "groups": ["attributes"],
            "fields": [{"key": "essence", "label": "Essence", "type": "resource", "max": 10}],
            "derived": {"surge": "reflex + essence_max - essence"},
        },
    },
}


def make_pack(root, mid="testmod", sheets=None, manifest=None, checks=None,
              rules=None, content=None):
    d = root / "modules" / mid
    d.mkdir(parents=True)
    (d / "module.md").write_text(
        manifest
        or "---\nname: Test Module\ndescription: fixture\nversion: 0.1\ndice: 1d20\n---\nnotes\n",
        encoding="utf-8",
    )
    (d / "sheets.json").write_text(
        json.dumps(sheets if sheets is not None else GOOD_SHEETS), encoding="utf-8"
    )
    if checks is not None:
        (d / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
    if rules:
        rd = d / "rules"
        rd.mkdir()
        for name, text in rules.items():
            (rd / f"{name}.md").write_text(text, encoding="utf-8")
    if content:
        for rel, text in content.items():
            p = d / "content" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    return d


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def test_load_good_pack(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []
    assert pack["manifest"]["name"] == "Test Module"
    assert pack["source"] == "user"
    assert "warden" in pack["sheets"]["sheet_types"]


def test_missing_module(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    with pytest.raises(modules.ModuleNotFound):
        modules.load_pack("nope")


def test_manifest_requires_name(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path), manifest="---\nversion: 1\n---\n")
    assert any("name" in e for e in modules.load_pack("testmod")["errors"])


def test_manifest_bad_dice(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              manifest="---\nname: X\ndice: 1dbanana\n---\n")
    assert any("dice" in e for e in modules.load_pack("testmod")["errors"])


def _sheets_error(monkeypatch, tmp_path, mutate):
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    mutate(sheets)
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets)
    return modules.load_pack("testmod")["errors"]


def test_unknown_group_ref(monkeypatch, tmp_path):
    errs = _sheets_error(monkeypatch, tmp_path,
                         lambda s: s["sheet_types"]["warden"]["groups"].append("ghost"))
    assert any("ghost" in e for e in errs)


def test_unknown_kind(monkeypatch, tmp_path):
    errs = _sheets_error(monkeypatch, tmp_path,
                         lambda s: s["sheet_types"]["warden"].update(kind="vehicles"))
    assert any("vehicles" in e for e in errs)


def test_duplicate_field_key(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["fields"].append(
            {"key": "vigor", "type": "number"}))
    assert any("duplicate" in e for e in errs)


def test_derived_collides_with_field(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["derived"].update(vigor="1"))
    assert any("collide" in e for e in errs)


def test_bad_field_type(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "aura", "type": "sparkles"}))
    assert any("sparkles" in e for e in errs)


def test_dots_requires_max(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "aura", "type": "dots"}))
    assert any("max" in e for e in errs)


def test_derived_unparseable(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["derived"].update(bad="a.b"))
    assert any("bad" in e for e in errs)


def test_derived_unknown_name(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["derived"].update(bad="charm + 1"))
    assert any("charm" in e for e in errs)


def test_sheets_json_invalid_json(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "sheets.json").write_text("{nope", encoding="utf-8")
    assert any("sheets.json" in e for e in modules.load_pack("testmod")["errors"])


def test_assembled_fields_and_numeric_names():
    fields = modules.assembled_fields(GOOD_SHEETS, "warden")
    assert [f["key"] for f in fields] == ["vigor", "wits", "essence"]
    assert modules.numeric_names(fields) == {"vigor", "wits", "essence", "essence_max"}


def test_sheets_json_wrong_shape_accumulates_error(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "sheets.json").write_text("[1, 2, 3]", encoding="utf-8")
    errs = modules.load_pack("testmod")["errors"]
    assert any("sheets.json" in e for e in errs)


def test_sheets_json_wrong_typed_entries(monkeypatch, tmp_path):
    sheets = {"groups": {"attributes": "nope"}, "sheet_types": {"warden": ["x"]}}
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets)
    errs = modules.load_pack("testmod")["errors"]
    assert any("groups.attributes" in e for e in errs)
    assert any("sheet_types.warden" in e for e in errs)


def test_missing_sheets_json(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "sheets.json").unlink()
    assert any("sheets.json" in e for e in modules.load_pack("testmod")["errors"])


def test_bool_max_rejected(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "aura", "type": "dots", "max": True}))
    assert any("max" in e for e in errs)


def test_user_module_shadows_builtin(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    b = tmp_path / "builtins" / "testmod"
    b.mkdir(parents=True)
    (b / "module.md").write_text("---\nname: Builtin Copy\n---\n", encoding="utf-8")
    (b / "sheets.json").write_text('{"groups": {}, "sheet_types": {}}', encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_MODULES", str(tmp_path / "builtins"))
    # builtin-only resolution works
    assert modules.load_pack("testmod")["source"] == "builtin"
    assert modules.load_pack("testmod")["manifest"]["name"] == "Builtin Copy"
    # user copy shadows it
    make_pack(tmp_path)
    assert modules.load_pack("testmod")["source"] == "user"
    assert modules.load_pack("testmod")["manifest"]["name"] == "Test Module"


def test_wrong_typed_field_entry_in_group(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append("oops"))
    assert any("field must be an object" in e for e in errs)


def test_field_missing_key_does_not_crash(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"type": "dots", "max": 3}))
    assert any("missing key" in e for e in errs)


def test_sheet_type_referencing_wrong_typed_group(monkeypatch, tmp_path):
    def mutate(s):
        s["groups"]["attributes"] = "nope"
    errs = _sheets_error(monkeypatch, tmp_path, mutate)
    assert any("groups.attributes" in e for e in errs)


def test_non_string_derived_expression(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["derived"].update(bad=5))
    assert any("must be a string" in e for e in errs)


def test_unhashable_field_key_in_group(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": ["x"], "type": "number"}))
    assert any("missing key" in e for e in errs)


def test_unhashable_field_key_in_sheet_type(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["fields"].append(
            {"key": ["x"], "type": "number"}))
    assert any("missing key" in e for e in errs)


# Task 3: Checks, rules, and content validation

GOOD_CHECKS = {
    "surge_check": {
        "label": "Surge",
        "roll": "{reflex + essence}d10 t6",
        "requires": ["attributes"],
        "rules": ["combat"],
    },
}
# NOTE: "essence" is a sheet-type field, not in group "attributes" — see
# test_check_names_must_come_from_required_groups below; use "vigor + wits"
# for the passing case.
GOOD_CHECKS["surge_check"]["roll"] = "{vigor + wits}d10 t6"

RULES = {
    "core": "---\nalways: true\n---\nCore rules.\n",
    "combat": "---\nkeys: fight, attack\non_roll: true\n---\nCombat.\n",
    "warden-arts": "---\nsheet_types: warden\n---\nWarden arts.\n",
}


def test_good_checks_and_rules(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path), checks=GOOD_CHECKS, rules=RULES)
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []
    assert "surge_check" in pack["checks"]
    by_id = {r["id"]: r for r in pack["rules"]}
    assert by_id["core"]["always"] is True
    assert by_id["combat"]["keys"] == ["fight", "attack"]
    assert by_id["combat"]["on_roll"] is True
    assert by_id["warden-arts"]["sheet_types"] == ["warden"]


def test_check_unknown_required_group(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": ["ghost"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("ghost" in e for e in modules.load_pack("testmod")["errors"])


def test_check_placeholder_unknown_name(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20 + {charm}", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("charm" in e for e in modules.load_pack("testmod")["errors"])


def test_check_names_must_come_from_required_groups(monkeypatch, tmp_path):
    # essence is a warden sheet-type field, not in group "attributes"
    checks = {"c": {"label": "C", "roll": "{essence}d10 t6", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("essence" in e for e in modules.load_pack("testmod")["errors"])


def test_check_template_must_parse_as_dice(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "banana + {vigor}", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("dice" in e.lower() for e in modules.load_pack("testmod")["errors"])


def test_check_unknown_rules_doc(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": [], "rules": ["ghost"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any("ghost" in e for e in modules.load_pack("testmod")["errors"])


def test_rules_unknown_sheet_type(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              rules={"x": "---\nsheet_types: ghost\n---\nX.\n"})
    assert any("ghost" in e for e in modules.load_pack("testmod")["errors"])


def test_content_with_stat_sidecar(monkeypatch, tmp_path):
    content = {
        "items/lantern.md": "---\nname: Lantern of Winnowing\n---\nA lantern.\n",
        "items/lantern.sheet.json": json.dumps(
            {"sheet_type": "warden", "fields": {"vigor": 3}}),
    }
    # warden targets characters, not items -> kind mismatch error
    make_pack(_home(monkeypatch, tmp_path), content=content)
    assert any("kind" in e for e in modules.load_pack("testmod")["errors"])


def test_content_bad_kind_dir(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              content={"vehicles/cart.md": "---\nname: Cart\n---\n"})
    assert any("vehicles" in e for e in modules.load_pack("testmod")["errors"])


def test_validate_sheet_values():
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden",
                                         {"vigor": 3, "essence": {"current": 4, "max": 10}})
    assert errs == []
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"ghost": 1})
    assert any("ghost" in e for e in errs)
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"vigor": 9})
    assert any("max" in e for e in errs)          # dots over max
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"essence": 4})
    assert any("current/max" in e for e in errs)  # resource needs a pair


def test_checks_json_wrong_shape(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    d = tmp_path / "modules" / "testmod"
    (d / "checks.json").write_text("[1, 2]", encoding="utf-8")
    assert any("checks.json" in e for e in modules.load_pack("testmod")["errors"])


def test_check_entry_wrong_type(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path), checks={"c": "oops"})
    assert any("checks.c" in e for e in modules.load_pack("testmod")["errors"])


def test_check_requiring_malformed_group(monkeypatch, tmp_path):
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["groups"]["attributes"] = "oops"
    checks = {"c": {"label": "C", "roll": "1d20", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets, checks=checks)
    errs = modules.load_pack("testmod")["errors"]
    assert any("checks.c" in e for e in errs)


def test_content_sidecar_against_malformed_sheet_type(monkeypatch, tmp_path):
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["sheet_types"]["broken"] = "oops"
    content = {
        "items/orb.md": "---\nname: Orb\n---\nAn orb.\n",
        "items/orb.sheet.json": json.dumps({"sheet_type": "broken", "fields": {}}),
    }
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets, content=content)
    errs = modules.load_pack("testmod")["errors"]
    assert any("orb.sheet.json" in e for e in errs)


def test_non_utf8_rules_file(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    rd = d / "rules"
    rd.mkdir(exist_ok=True)
    (rd / "bad.md").write_bytes(b"\xff\xfe\x00garbage")
    errs = modules.load_pack("testmod")["errors"]
    assert any("bad" in e for e in errs)


def test_sheet_values_reject_bool(monkeypatch, tmp_path):
    errs = modules.validate_sheet_values(GOOD_SHEETS, "warden", {"vigor": True})
    assert any("integer" in e for e in errs)


# ---- Fix report 2: structural never-raise guards ----

def test_sidecar_top_level_not_object(monkeypatch, tmp_path):
    content = {
        "items/orb.md": "---\nname: Orb\n---\nAn orb.\n",
        "items/orb.sheet.json": json.dumps([1, 2, 3]),
    }
    make_pack(_home(monkeypatch, tmp_path), content=content)
    errs = modules.load_pack("testmod")["errors"]
    assert any("orb.sheet.json" in e and "must be an object" in e for e in errs)


def test_sidecar_fields_not_object(monkeypatch, tmp_path):
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["sheet_types"]["widget"] = {"label": "Widget", "kind": "items", "groups": [], "fields": []}
    content = {
        "items/orb.md": "---\nname: Orb\n---\nAn orb.\n",
        "items/orb.sheet.json": json.dumps(
            {"sheet_type": "widget", "fields": ["not", "a", "dict"]}),
    }
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets, content=content)
    errs = modules.load_pack("testmod")["errors"]
    assert any("orb.sheet.json" in e and "fields must be an object" in e for e in errs)


def test_validate_sheet_values_rejects_non_dict():
    assert modules.validate_sheet_values(GOOD_SHEETS, "warden", ["nope"]) == [
        "fields must be an object"
    ]


def test_check_roll_not_a_string(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": 5, "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    errs = modules.load_pack("testmod")["errors"]
    assert any("checks.c" in e and "roll must be a string" in e for e in errs)


def test_check_requires_not_a_list(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": "attributes"}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    errs = modules.load_pack("testmod")["errors"]
    assert any("checks.c" in e and "requires must be a list" in e for e in errs)


def test_check_rules_not_a_list(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": [], "rules": "combat"}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks, rules=RULES)
    errs = modules.load_pack("testmod")["errors"]
    assert any("checks.c" in e and "rules must be a list" in e for e in errs)


def test_check_requires_group_with_non_list_fields(monkeypatch, tmp_path):
    import copy
    sheets = copy.deepcopy(GOOD_SHEETS)
    sheets["groups"]["attributes"]["fields"] = 5
    checks = {"c": {"label": "C", "roll": "1d20", "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), sheets=sheets, checks=checks)
    # must not raise; the malformed group fields shape is reported once via
    # sheets validation, and the check itself resolves an empty scope.
    errs = modules.load_pack("testmod")["errors"]
    assert any("groups.attributes" in e and "fields must be a list" in e for e in errs)


def test_sidecar_unhashable_sheet_type(monkeypatch, tmp_path):
    content = {
        "items/orb.md": "---\nname: Orb\n---\nAn orb.\n",
        "items/orb.sheet.json": json.dumps({"sheet_type": ["not", "hashable"], "fields": {}}),
    }
    make_pack(_home(monkeypatch, tmp_path), content=content)
    errs = modules.load_pack("testmod")["errors"]
    assert any("orb.sheet.json" in e and "unknown sheet type" in e for e in errs)


def _mutate_variants(doc):
    """Yield copies of doc with each nested value replaced by wrong-typed junk."""
    import copy

    def paths(node, prefix=()):
        if isinstance(node, dict):
            for k, v in node.items():
                yield prefix + (k,)
                yield from paths(v, prefix + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield prefix + (i,)
                yield from paths(v, prefix + (i,))

    for path in list(paths(doc)):
        for junk in (5, ["x"], {"y": 1}, None, True):
            variant = copy.deepcopy(doc)
            target = variant
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = junk
            yield path, junk, variant


def test_load_pack_never_raises_mutation_sweep(monkeypatch, tmp_path):
    import json as _json
    import shutil

    base = _home(monkeypatch, tmp_path)
    count = 0
    for which, doc in (("sheets", GOOD_SHEETS), ("checks", GOOD_CHECKS)):
        for path, junk, variant in _mutate_variants(doc):
            shutil.rmtree(base / "modules", ignore_errors=True)
            if which == "sheets":
                make_pack(base, sheets=variant, checks=GOOD_CHECKS, rules=RULES)
            else:
                make_pack(base, checks=variant, rules=RULES)
            try:
                modules.load_pack("testmod")
            except modules.ModuleNotFound:
                raise
            except Exception as e:  # noqa: BLE001 - the assertion IS "no exception"
                raise AssertionError(
                    f"{which}.json mutation at {path} -> {junk!r} raised {type(e).__name__}: {e}"
                ) from e
            count += 1
    assert count > 100  # sanity: the sweep actually generated variants
