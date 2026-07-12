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


def test_list_create_delete(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mid = modules.create_module("Homebrew Nights")
    assert mid == "homebrew-nights"
    listed = {m["id"]: m for m in modules.list_modules()}
    assert listed[mid]["source"] == "user"
    assert listed[mid]["valid"] is True
    # built-ins present alongside (d20-basic/pool-basic land in Task 5;
    # here just assert the user module lists)
    modules.delete_module(mid)
    assert mid not in {m["id"] for m in modules.list_modules()}
    with pytest.raises(modules.ModuleNotFound):
        modules.delete_module(mid)


def test_scaffold_is_valid(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mid = modules.create_module("Fresh")
    assert modules.load_pack(mid)["errors"] == []


def test_delete_builtin_refused(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    # simulate a builtin by pointing GRIMOIRE_MODULES at a temp dir
    b = tmp_path / "builtins" / "stock"
    b.mkdir(parents=True)
    (b / "module.md").write_text("---\nname: Stock\n---\n", encoding="utf-8")
    (b / "sheets.json").write_text('{"groups": {}, "sheet_types": {}}', encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_MODULES", str(tmp_path / "builtins"))
    assert modules.load_pack("stock")["source"] == "builtin"
    with pytest.raises(modules.ModuleError):
        modules.delete_module("stock")


def test_create_module_blank_name_scaffolds_valid(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for raw in ("", "   "):
        mid = modules.create_module(raw)
        pack = modules.load_pack(mid)
        assert pack["errors"] == []
        assert pack["manifest"]["name"] == "Untitled"
        modules.delete_module(mid)


def test_create_module_newline_name_scaffolds_valid(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mid = modules.create_module("Sneaky\ndice: 1dbanana")
    pack = modules.load_pack(mid)
    assert pack["errors"] == []
    assert "dice" not in pack["manifest"] or pack["manifest"].get("dice") != "1dbanana"


def test_create_module_none_reserved(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mid = modules.create_module("None")
    assert mid != "none"
    assert modules.load_pack(mid)["errors"] == []


def test_set_world_module_none_rejected(monkeypatch, tmp_path):
    wid, _cid = _world_campaign(monkeypatch, tmp_path)
    with pytest.raises(modules.ModuleError):
        modules.set_world_module(wid, "none")


def test_pack_root_rejects_unsafe_mids(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for bad in ("D:evil", "UPPER", "a/b"):
        with pytest.raises(modules.ModuleNotFound):
            modules.pack_root(bad)


def test_manifest_id_cannot_be_overridden(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              manifest="---\nname: X\nid: impostor\n---\n")
    pack = modules.load_pack("testmod")
    assert pack["manifest"]["id"] == "testmod"


def test_builtin_reference_modules_validate(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)  # built-ins resolve package-relative
    for mid in ("d20-basic", "pool-basic"):
        pack = modules.load_pack(mid)
        assert pack["errors"] == [], f"{mid}: {pack['errors']}"
        assert pack["source"] == "builtin"
        kinds = {t["kind"] for t in pack["sheets"]["sheet_types"].values()}
        char_types = [t for t in pack["sheets"]["sheet_types"].values()
                      if t["kind"] == "characters"]
        assert len(char_types) >= 2
        assert kinds - {"characters"}          # at least one non-character type
        assert pack["checks"]
        flags = {f for r in pack["rules"]
                 for f in ("always", "on_roll") if r[f]}
        assert flags == {"always", "on_roll"}
        assert any(r["keys"] for r in pack["rules"])
        assert any(r["sheet_types"] for r in pack["rules"])
        assert any(c["sheet_type"] for c in pack["content"])
    assert {m["id"] for m in modules.list_modules()} >= {"d20-basic", "pool-basic"}


# ---- Task 6: binding — world/campaign module: keys + resolve() ----

from grimoire.store import campaigns, worlds


def _world_campaign(monkeypatch, tmp_path, **kw):
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, **kw)
    return wid, cid


def test_resolve_default_none(monkeypatch, tmp_path):
    _, cid = _world_campaign(monkeypatch, tmp_path)
    assert modules.resolve(cid) is None


def test_resolve_inherits_world_default(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_world_module(wid, "pool-basic")
    assert modules.resolve(cid) == "pool-basic"


def test_campaign_none_overrides_world(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_world_module(wid, "pool-basic")
    modules.set_campaign_module(cid, "none")
    assert modules.resolve(cid) is None


def test_campaign_module_overrides_world(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_campaign_module(cid, "d20-basic")
    assert modules.resolve(cid) == "d20-basic"


def test_clear_campaign_setting_reinherits(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_world_module(wid, "pool-basic")
    modules.set_campaign_module(cid, "d20-basic")
    modules.set_campaign_module(cid, "")
    assert modules.resolve(cid) == "pool-basic"


def test_set_unknown_module_rejected(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    with pytest.raises(modules.ModuleNotFound):
        modules.set_campaign_module(cid, "ghost")
    with pytest.raises(modules.ModuleNotFound):
        modules.set_world_module(wid, "ghost")


def test_resolve_missing_module_falls_through(monkeypatch, tmp_path):
    wid, cid = _world_campaign(monkeypatch, tmp_path)
    modules.set_campaign_module(cid, "d20-basic")
    # simulate the module disappearing after binding
    monkeypatch.setenv("GRIMOIRE_MODULES", str(tmp_path / "empty"))
    assert modules.resolve(cid) is None


def test_create_campaign_with_module(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module="pool-basic")
    assert modules.resolve(cid) == "pool-basic"
    with pytest.raises(modules.ModuleNotFound):
        campaigns.create_campaign("Run2", wid, module="ghost")


def test_list_modules_skips_non_slug_dirs(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    d = tmp_path / "modules" / "MyCoolSystem"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Cool\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text('{"groups": {}, "sheet_types": {}}', encoding="utf-8")
    ids = {m["id"] for m in modules.list_modules()}
    assert "MyCoolSystem" not in ids  # skipped, not crashed


# ---- Task 1: Reserved field keys (modules.py) ----


def test_reserved_function_name_key_rejected(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "floor", "type": "number"}))
    assert any("reserved" in e for e in errs)


def test_resource_max_name_collision_rejected(monkeypatch, tmp_path):
    # GOOD_SHEETS' warden has resource "essence" -> implicit "essence_max"
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["sheet_types"]["warden"]["fields"].append(
            {"key": "essence_max", "type": "number"}))
    assert any("essence_max" in e and "resource" in e for e in errs)


def test_builtin_packs_pass_reserved_key_rules(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for mid in ("d20-basic", "pool-basic"):
        assert modules.load_pack(mid)["errors"] == []


def test_fleshed_reference_packs(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    pool = modules.load_pack("pool-basic")
    assert pool["errors"] == []
    medium = pool["sheets"]["sheet_types"]["medium"]
    keys = {f["key"] for f in medium["fields"]}
    assert {"quirk", "gear"} <= keys
    d20 = modules.load_pack("d20-basic")
    assert d20["errors"] == []
    assert any(f["key"] == "spells"
               for f in d20["sheets"]["sheet_types"]["adept"]["fields"])


# ---- Task 2: pack format additions — outcome tiers, _defaults, read_rule ----


def test_check_templates_accept_reserved_names(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "{vigor + modifier}d10 t{difficulty}",
                    "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert modules.load_pack("testmod")["errors"] == []


def test_check_outcomes_validated(monkeypatch, tmp_path):
    good = {"c": {"label": "C", "roll": "1d20 vs {difficulty}", "requires": [],
                  "difficulty": 12,
                  "outcomes": [{"label": "crit", "when": "natural == 20"},
                               {"label": "success", "when": "margin >= 0"}]}}
    make_pack(_home(monkeypatch, tmp_path), checks=good)
    assert modules.load_pack("testmod")["errors"] == []


@pytest.mark.parametrize("outcomes,frag", [
    ([{"label": "", "when": "total > 1"}], "label"),
    ([{"label": "x", "when": "a.b"}], "when"),
    ([{"label": "x", "when": "vigor > 1"}], "vigor"),   # sheet names not in roll scope
    ("nope", "outcomes"),
])
def test_check_outcomes_rejected(monkeypatch, tmp_path, outcomes, frag):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": [],
                    "outcomes": outcomes}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any(frag in e for e in modules.load_pack("testmod")["errors"])


def test_checks_defaults_entry(monkeypatch, tmp_path):
    checks = {"_defaults": {"difficulty": 6,
                            "outcomes": [{"label": "botch",
                                          "when": "successes == 0 and ones > 0"}]},
              "c": {"label": "C", "roll": "{vigor}d10 t{difficulty}",
                    "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []
    assert pack["checks"]["_defaults"]["difficulty"] == 6
    bad = {"_defaults": {"outcomes": [{"label": "x", "when": "("}]}}
    import shutil
    shutil.rmtree(tmp_path / "modules")
    make_pack(tmp_path, checks=bad)
    assert any("_defaults" in e for e in modules.load_pack("testmod")["errors"])


def test_read_rule(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              rules={"core": "---\nalways: true\n---\nCore body.\n"})
    doc = modules.read_rule("testmod", "core")
    assert doc["body"].strip() == "Core body." and doc["meta"]["always"] == "true"
    assert modules.read_rule("testmod", "ghost") is None
    with pytest.raises(modules.ModuleNotFound):
        modules.read_rule("ghost", "core")


def test_reference_packs_have_defaults_and_reserved_names(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for mid, diff in (("pool-basic", 6), ("d20-basic", 12)):
        pack = modules.load_pack(mid)
        assert pack["errors"] == [], f"{mid}: {pack['errors']}"
        assert pack["checks"]["_defaults"]["difficulty"] == diff
        assert any("{difficulty}" in c["roll"]
                   for k, c in pack["checks"].items() if k != "_defaults")


# ---- Task 3: Display loading into load_pack + _scan ----


def test_pack_display_keys_default_empty(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    pack = modules.load_pack("testmod")
    assert pack["layout"] == {"sheet_types": {}}
    assert pack["theme"] == {}
    assert pack["display_errors"] == []


def test_display_errors_do_not_invalidate(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "layout.json").write_text("{broken", encoding="utf-8")
    (d / "theme.css").write_text(".x{}", encoding="utf-8")
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []          # mechanics untouched
    assert len(pack["display_errors"]) == 2
    rows = {m["id"]: m for m in modules.list_modules()}
    assert rows["testmod"]["valid"] is True
    assert rows["testmod"]["display_ok"] is False


def test_display_ok_true_for_clean_pack(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path))
    rows = {m["id"]: m for m in modules.list_modules()}
    assert rows["testmod"]["display_ok"] is True


def test_load_pack_survives_pathological_display_files(monkeypatch, tmp_path):
    d = make_pack(_home(monkeypatch, tmp_path))
    (d / "layout.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    pack = modules.load_pack("testmod")  # must not raise
    assert pack["errors"] == []
    assert pack["layout"] == {"sheet_types": {}}
    assert pack["display_errors"]


def test_resolve_ignores_display_errors(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    d = make_pack(home)
    (d / "layout.json").write_text("{broken", encoding="utf-8")
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    modules.set_campaign_module(cid, "testmod")
    assert modules.resolve(cid) == "testmod"
