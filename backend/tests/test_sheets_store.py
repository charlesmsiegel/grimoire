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


def test_write_rejects_wrong_typed_arguments(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", ["medium"], None)  # type: ignore[arg-type]
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", [1, 2])  # type: ignore[arg-type]
    assert sheets.read(cid, "characters", 7) is None  # type: ignore[arg-type]


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


def test_campaign_coverage(monkeypatch, tmp_path):
    from grimoire.store import entities as ent, overlay
    wid, cid = _campaign(monkeypatch, tmp_path)          # pool-basic
    ent.create_entity(worlds.world_root(wid), "items", "Moon Disc")
    ent.create_entity(worlds.world_root(wid), "locations", "Old Chapel")
    overlay.create_entity(cid, "items", "Salt Knife")
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    cov = sheets.coverage(cid)
    assert cov["items"] == {"total": 2, "sheeted": 1, "invalid": 0}
    assert cov["locations"]["total"] == 1
    # pool-basic has no lore/groups/creatures sheet types -> absent rows
    assert "lore" not in cov and "creatures" not in cov
    assert "characters" in cov and "pcs" in cov          # separate rows


def test_coverage_counts_invalid(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import overlay
    overlay.create_entity(cid, "items", "Moon Disc")
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    modules.set_campaign_module(cid, "d20-basic")        # talisman now unknown
    cov = sheets.coverage(cid)
    assert cov["items"]["invalid"] == 1


def test_coverage_empty_without_module(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path, module=None)
    assert sheets.coverage(cid) == {}


def test_world_coverage(monkeypatch, tmp_path):
    from grimoire.store import entities as ent
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    ent.create_entity(worlds.world_root(wid), "items", "Moon Disc")
    sheets.write_world(wid, "pool-basic", "items", "moon-disc", "talisman", None)
    cov = sheets.world_coverage(wid, "pool-basic")
    assert cov["items"] == {"total": 1, "sheeted": 1, "invalid": 0}
    assert sheets.world_coverage(wid, "ghost") == {}
