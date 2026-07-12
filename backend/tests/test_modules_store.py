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
