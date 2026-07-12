"""Sheet store tests (campaign + world sheets)."""

import pytest
from grimoire.store import campaigns, entities, modules, sheets, worlds


def _campaign(monkeypatch, tmp_path, module=None):
    """Create a campaign for testing, returning (cid, wid)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=module)
    return cid, wid


def test_read_returns_none_for_malformed(monkeypatch, tmp_path):
    cid, _ = _campaign(monkeypatch, tmp_path, module="pool-basic")
    assert sheets.read(cid, "characters", "nonexistent") is None


def test_write_and_read_campaign_sheet(monkeypatch, tmp_path):
    cid, _ = _campaign(monkeypatch, tmp_path, module="pool-basic")
    sheets.write(cid, "characters", "mara", "medium", None)
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "medium" and s["errors"] == []


def test_read_with_no_module_resolved(monkeypatch, tmp_path):
    cid, _ = _campaign(monkeypatch, tmp_path, module=None)
    assert sheets.read(cid, "characters", "any") is None


def test_delete_campaign_sheet(monkeypatch, tmp_path):
    cid, _ = _campaign(monkeypatch, tmp_path, module="pool-basic")
    sheets.write(cid, "characters", "mara", "medium", None)
    assert sheets.delete(cid, "characters", "mara") is True
    assert sheets.delete(cid, "characters", "mara") is False


def test_write_with_no_module_raises(monkeypatch, tmp_path):
    cid, _ = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(sheets.SheetError, match="no module resolved"):
        sheets.write(cid, "characters", "mara", "medium", None)


def test_list_refs(monkeypatch, tmp_path):
    cid, _ = _campaign(monkeypatch, tmp_path, module="pool-basic")
    sheets.write(cid, "characters", "mara", "medium", None)
    sheets.write(cid, "characters", "winifred", "shifter", None)
    assert sheets.list_refs(cid) == [("characters", "mara"), ("characters", "winifred")]


# World sheet tests

def test_world_sheet_crud_keyed_by_module(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None)
    s = sheets.read_world(wid, "pool-basic", "characters", "mara")
    assert s["sheet_type"] == "medium" and s["errors"] == []
    assert sheets.world_sheet_modules(wid) == ["pool-basic"]
    assert sheets.world_list_refs(wid, "pool-basic") == [("characters", "mara")]
    assert sheets.read_world(wid, "d20-basic", "characters", "mara") is None
    assert sheets.delete_world(wid, "pool-basic", "characters", "mara") is True


def test_write_world_unknown_module(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(modules.ModuleNotFound):
        sheets.write_world(wid, "ghost", "characters", "mara", "medium", None)


def test_seed_on_create_matching_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium",
                       {"vigor": 3})
    sheets.write_world(wid, "d20-basic", "characters", "mara", "warrior", None)
    cid = campaigns.create_campaign("Run", wid, module="pool-basic")
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "medium" and s["fields"]["vigor"] == 3
    # only the matching module's sheets seeded
    assert sheets.list_refs(cid) == [("characters", "mara")]


def test_no_seed_without_module_and_no_reseed_on_bind(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None)
    cid = campaigns.create_campaign("Run", wid)
    assert sheets.list_refs(cid) == []
    modules.set_campaign_module(cid, "pool-basic")   # later binding
    assert sheets.list_refs(cid) == []               # never re-seeds
