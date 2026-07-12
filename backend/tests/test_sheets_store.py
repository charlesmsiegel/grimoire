import json

import pytest

from grimoire.store import campaigns, modules, sheets, worlds


def _campaign(monkeypatch, tmp_path, module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=module)
    return wid, cid


def test_write_and_read_with_derived(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 2, "grace": 3, "wits": 4, "occult": 2,
                  "essence": {"current": 6, "max": 10}})
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "medium"
    assert s["errors"] == []
    assert s["derived"]["sight_pool"] == 6          # wits + occult
    assert s["derived"]["awareness"] == 7           # group derived: wits + grace


def test_read_missing_returns_none(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    assert sheets.read(cid, "characters", "nobody") is None


def test_write_defaults_when_fields_none(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    s = sheets.read(cid, "items", "moon-disc")
    assert s["fields"]["power"] == 1                       # schema default
    assert s["fields"]["charges"] == {"current": 10, "max": 10}  # default max
    assert s["errors"] == []


def test_pcs_validate_against_characters(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "pcs", "seraphine", "medium", None)
    assert sheets.read(cid, "pcs", "seraphine")["sheet_type"] == "medium"


def test_write_rejects_kind_mismatch(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "talisman", None)  # items type


def test_write_rejects_unknown_type_and_bad_values(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "ghost", None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", {"vigor": 99})


def test_write_without_module_rejected(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", None)


def test_type_change_preserves_shared_drops_orphans(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}})
    sheets.write(cid, "characters", "mara", "shifter",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}})
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "shifter"
    assert s["fields"]["vigor"] == 3         # shared via attributes group
    assert "essence" not in s["fields"]      # medium-only field dropped


def test_invalid_after_module_switch(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    modules.set_campaign_module(cid, "d20-basic")
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"]                        # flagged, not deleted
    assert s["sheet_type"] == "medium"
    modules.set_campaign_module(cid, "none")
    assert any("module" in e for e in sheets.read(cid, "characters", "mara")["errors"])


def test_malformed_sheet_file_tolerated(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "sheets"
    d.mkdir(exist_ok=True)
    (d / "characters--mara.json").write_text("{nope", encoding="utf-8")
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] is None and s["fields"] == {} and s["errors"]


def test_delete_and_list_refs(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    assert sheets.list_refs(cid) == [("characters", "mara"), ("items", "moon-disc")]
    assert sheets.delete(cid, "items", "moon-disc") is True
    assert sheets.delete(cid, "items", "moon-disc") is False
    assert sheets.list_refs(cid) == [("characters", "mara")]


def test_bad_kind_and_eid_rejected(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "vehicles", "cart", "medium", None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "../escape", "medium", None)
    assert sheets.read(cid, "vehicles", "cart") is None
