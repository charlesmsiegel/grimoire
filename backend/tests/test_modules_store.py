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
