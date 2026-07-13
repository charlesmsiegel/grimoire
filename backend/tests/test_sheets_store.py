import json

import pytest

from grimoire.store import campaigns, characters, entities, modules, pcs, sheets, worlds


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


def test_world_write_bad_mid_and_colon_eid_rejected(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_world(wid, "..", "characters", "mara", "medium", None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "c:evil", "medium", None)


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


def test_seed_via_world_default_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    modules.set_world_module(wid, "pool-basic")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None)
    cid = campaigns.create_campaign("Run", wid)   # no explicit module: inherits default
    assert sheets.read(cid, "characters", "mara")["sheet_type"] == "medium"


def test_coverage_excludes_tombstoned_entities(monkeypatch, tmp_path):
    from grimoire.store import overlay
    wid, cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import entities as ent
    ent.create_entity(worlds.world_root(wid), "items", "Moon Disc")
    overlay.add_deleted(cid, "items/moon-disc")
    cov = sheets.coverage(cid)
    assert cov["items"]["total"] == 0


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    p = tmp_path / "sub" / "sheet.json"
    sheets._atomic_write_json(p, {"sheet_type": "medium", "fields": {}})
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["sheet_type"] == "medium"
    leftovers = list((tmp_path / "sub").glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_failure_leaves_no_tmp_file(tmp_path, monkeypatch):
    p = tmp_path / "sheet.json"

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(sheets.os, "replace", boom)
    with pytest.raises(OSError):
        sheets._atomic_write_json(p, {"sheet_type": "medium", "fields": {}})
    assert not p.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# write_creation / write_world_creation


def _campaign_with_creation_module(monkeypatch, tmp_path):
    """A user-library module 'chargen' with one sheet type ('hero') that has
    two creation pools, for write_creation tests."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "chargen"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Chargen Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [
                    {"key": "strength", "type": "number", "min": 1, "max": 20, "default": 10},
                    {"key": "wits", "type": "dots", "max": 5, "default": 1},
                ],
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "hp", "type": "resource", "max": 10}],
                "creation": {"pools": {"attributes": {
                    "budget": 30, "costs": {"strength": 2, "wits": 1}}}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    cid = campaigns.create_campaign("Run", wid, module="chargen")
    return wid, cid


def test_write_creation_happy_path(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero",
                          {"attributes": {"strength": 12, "wits": 3}})
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"] == []
    assert s["fields"]["strength"] == 12
    assert s["fields"]["wits"] == 3


def test_write_creation_over_budget_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"strength": 20, "wits": 5}})


def test_write_creation_field_outside_range_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"strength": 999, "wits": 0}})


def test_write_creation_field_not_in_pool_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"hp": 5}})


def test_write_creation_omitted_costed_field_uses_floor_not_default(monkeypatch, tmp_path):
    # strength's schema default is 10 (well above its floor of 1) but it's
    # omitted from spends -- must resolve to the pool floor (1), not the
    # schema default (10), or the budget-omission loophole reopens.
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero",
                          {"attributes": {"wits": 2}})
    s = sheets.read(cid, "characters", "mara")
    assert s["fields"]["strength"] == 1
    assert s["fields"]["wits"] == 2


def test_write_creation_unknown_pool_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"ghost_pool": {"strength": 12}})


def test_write_creation_empty_spends_falls_through_to_defaults(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero", {})
    s = sheets.read(cid, "characters", "mara")
    assert s["fields"]["strength"] == 1   # floor, not schema default 10
    assert s["fields"]["hp"] == {"current": 10, "max": 10}  # non-costed field: schema default


def test_write_world_creation(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_world_creation(wid, "chargen", "characters", "mara", "hero",
                                {"attributes": {"strength": 14}})
    s = sheets.read_world(wid, "chargen", "characters", "mara")
    assert s["fields"]["strength"] == 14


# write_creation / write_world_creation reject a target that doesn't exist
# (#161/#164 follow-up: prevents orphaned sheets for nonexistent records)

def test_write_creation_missing_character_raises_not_found(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(characters.CharacterNotFound):
        sheets.write_creation(cid, "characters", "nobody", "hero", {})


def test_write_creation_missing_pc_raises_not_found(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(pcs.PCNotFound):
        sheets.write_creation(cid, "pcs", "nobody", "hero", {})


def test_write_creation_missing_entity_raises_not_found(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sheets.write_creation(cid, "items", "nobody", "hero", {})


def test_write_world_creation_missing_character_raises_not_found(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(characters.CharacterNotFound):
        sheets.write_world_creation(wid, "chargen", "characters", "nobody", "hero", {})


def test_write_world_creation_missing_pc_raises_not_found(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(pcs.PCNotFound):
        sheets.write_world_creation(wid, "chargen", "pcs", "nobody", "hero", {})


def test_write_world_creation_missing_entity_raises_not_found(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sheets.write_world_creation(wid, "chargen", "items", "nobody", "hero", {})


# advance() -- resource-pool spend to raise a sheet field (#164, Phase 7)


def _campaign_with_advancement_module(monkeypatch, tmp_path, campaign_name="Run"):
    # campaign_name is overridable because cid = slugify(name), and
    # sheets._campaign_locks is a module-level global that outlives any one
    # test's GRIMOIRE_HOME -- reusing the default "Run"/"run" cid across
    # every advance test in this file would let an earlier test's lock leak
    # into a later test that specifically needs a cold (unregistered) cid.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "advtest"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Advancement Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [{"key": "wits", "type": "dots", "max": 5, "default": 1}],
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "xp", "type": "resource", "max": 999}],
                "advancement": {"pool": "xp", "costs": {"wits": "new * 3"}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    cid = campaigns.create_campaign(campaign_name, wid, module="advtest")
    sheets.write(cid, "characters", "mara", "hero", {"wits": 2, "xp": {"current": 20, "max": 999}})
    return wid, cid


def test_advance_happy_path(monkeypatch, tmp_path):
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    s = sheets.advance(cid, "characters", "mara", "wits")
    assert s["fields"]["wits"] == 3
    assert s["fields"]["xp"]["current"] == 20 - 9   # new=3, cost = 3*3
    assert s["errors"] == []


def test_advance_insufficient_balance(monkeypatch, tmp_path):
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Poor")  # id: "poor"
    sheets.write(cid, "characters", "poor", "hero", {"wits": 2, "xp": {"current": 1, "max": 999}})
    with pytest.raises(sheets.SheetError, match="needs 9"):
        sheets.advance(cid, "characters", "poor", "wits")


def test_advance_field_at_max(monkeypatch, tmp_path):
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Capped")  # id: "capped"
    sheets.write(cid, "characters", "capped", "hero", {"wits": 5, "xp": {"current": 999, "max": 999}})
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "capped", "wits")


def test_advance_no_advancement_block(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)  # pool-basic has no advancement block yet
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    sheets.write(cid, "characters", "mara", "medium", None)
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "mara", "vigor")


def test_advance_unknown_field(monkeypatch, tmp_path):
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "mara", "ghost")


def test_advance_recomputes_from_current_values(monkeypatch, tmp_path):
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.advance(cid, "characters", "mara", "wits")   # 2 -> 3, cost 3*3=9, xp 20 -> 11
    with pytest.raises(sheets.SheetError, match="needs 12"):
        sheets.advance(cid, "characters", "mara", "wits")  # 3 -> 4 would cost 4*3=12, only have 11


def test_advance_missing_character_raises_not_found(monkeypatch, tmp_path):
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    with pytest.raises(characters.CharacterNotFound):
        sheets.advance(cid, "characters", "nobody", "wits")


def test_advance_missing_pc_raises_not_found(monkeypatch, tmp_path):
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    with pytest.raises(pcs.PCNotFound):
        sheets.advance(cid, "pcs", "nobody", "wits")


def test_advance_missing_entity_raises_not_found(monkeypatch, tmp_path):
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sheets.advance(cid, "items", "nobody", "wits")


def test_advance_orphan_sheet_without_backing_character_raises_not_found(monkeypatch, tmp_path):
    # The pre-existing, accepted gap: sheets.write() creates a sheet sidecar
    # without checking that its target entity exists, so a sheet file can be
    # orphaned (backing character never created, or since deleted). advance()
    # must still 404 for this case -- not just when no sheet file exists at
    # all (test_advance_missing_character_raises_not_found above).
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "ghost", "hero",
                 {"wits": 2, "xp": {"current": 20, "max": 999}})
    assert sheets.read(cid, "characters", "ghost") is not None  # orphan sheet really exists
    with pytest.raises(characters.CharacterNotFound):
        sheets.advance(cid, "characters", "ghost", "wits")


def test_advance_concurrent_calls_only_one_succeeds(monkeypatch, tmp_path):
    import threading
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Duelist")  # id: "duelist"
    sheets.write(cid, "characters", "duelist", "hero", {"wits": 2, "xp": {"current": 9, "max": 999}})
    results = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            sheets.advance(cid, "characters", "duelist", "wits")
            results.append("ok")
        except sheets.SheetError:
            results.append("rejected")

    t1, t2 = threading.Thread(target=attempt), threading.Thread(target=attempt)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == ["ok", "rejected"]
    s = sheets.read(cid, "characters", "duelist")
    assert s["fields"]["wits"] == 3
    assert s["fields"]["xp"]["current"] == 0


def test_advance_first_ever_call_cold_registry_race(monkeypatch, tmp_path):
    # exercises _lock_for's cold-registry path directly, not just contention
    # on an already-created lock. Uses a campaign name unique to this test so
    # its cid can't already be registered by a sibling advance test that ran
    # first in the same process (see _campaign_with_advancement_module).
    import threading
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path, campaign_name="Cold Registry Run")
    characters.create_character(worlds.world_root(wid), "First")  # id: "first"
    sheets.write(cid, "characters", "first", "hero", {"wits": 2, "xp": {"current": 9, "max": 999}})
    # sheets.write above now also serializes on lock_for(cid) (Task 2), which
    # warms the registry -- pop it back out so this test still exercises the
    # concurrent first-ever-call race through advance()'s own lock_for(cid).
    del sheets._campaign_locks[cid]
    assert cid not in sheets._campaign_locks
    results = []
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait()
        try:
            sheets.advance(cid, "characters", "first", "wits")
            results.append("ok")
        except sheets.SheetError:
            results.append("rejected")

    t1, t2 = threading.Thread(target=attempt), threading.Thread(target=attempt)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == ["ok", "rejected"]


# advance() cost evaluation -- tentative post-raise scope + runtime positive-cost
# re-check (#164 Phase 7 review findings)


def _campaign_with_tentative_scope_module(monkeypatch, tmp_path, campaign_name="Tentative Run"):
    """A module where the costed field's own cost formula references a
    group-level derived name ('combat') that itself depends on the field
    being raised -- exercises _advancement_cost's tentative (post-raise)
    recomputation of derived values, not the stale pre-raise ones."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "tentativetest"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Tentative Scope Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [
                    {"key": "strength", "type": "dots", "max": 5, "default": 0},
                    {"key": "dexterity", "type": "dots", "max": 5, "default": 0},
                ],
                "derived": {"combat": "strength + dexterity"},
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "xp", "type": "resource", "max": 999}],
                "advancement": {"pool": "xp", "costs": {"strength": "combat * 2"}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign(campaign_name, wid, module="tentativetest")
    return wid, cid


def test_advance_cost_uses_tentative_post_raise_derived_scope(monkeypatch, tmp_path):
    # strength 2 -> 3, dexterity stays 3. Tentative combat = (2+1) + 3 = 6,
    # cost = 6*2 = 12. A stale (pre-raise) recompute would instead see
    # combat = 2+3 = 5, cost = 5*2 = 10 -- the wrong, cheaper answer.
    wid, cid = _campaign_with_tentative_scope_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    sheets.write(cid, "characters", "mara", "hero",
                 {"strength": 2, "dexterity": 3, "xp": {"current": 12, "max": 999}})
    s = sheets.advance(cid, "characters", "mara", "strength")
    assert s["fields"]["strength"] == 3
    # Tripwire: a regression to stale-scope pricing would leave xp.current
    # at 12 - 10 == 2 instead of 12 - 12 == 0.
    assert s["fields"]["xp"]["current"] == 0


def test_advance_cost_rejection_also_uses_tentative_scope(monkeypatch, tmp_path):
    # Balance of 11 is enough for the stale (wrong) cost of 10 but not the
    # tentative (correct) cost of 12 -- proves the rejection path prices the
    # raise with the same tentative scope as the success path.
    wid, cid = _campaign_with_tentative_scope_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Poor")  # id: "poor"
    sheets.write(cid, "characters", "poor", "hero",
                 {"strength": 2, "dexterity": 3, "xp": {"current": 11, "max": 999}})
    with pytest.raises(sheets.SheetError, match="needs 12"):
        sheets.advance(cid, "characters", "poor", "strength")


def _campaign_with_runtime_positive_check_module(monkeypatch, tmp_path):
    """A module whose cost formula ('5 - new') is positive at pack-load-time
    sampling (new=1 -> 4) but goes non-positive for a real raise once the
    field's current value is high enough -- exercises _advancement_cost's
    runtime positive-integer re-check, not just load-time sampling."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "runtimechecktest"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Runtime Positive Check Test\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(json.dumps({
        "groups": {
            "attributes": {
                "label": "Attributes",
                "fields": [{"key": "toughness", "type": "dots", "max": 10, "default": 0}],
            },
        },
        "sheet_types": {
            "hero": {
                "label": "Hero", "kind": "characters", "groups": ["attributes"],
                "fields": [{"key": "xp", "type": "resource", "max": 999}],
                "advancement": {"pool": "xp", "costs": {"toughness": "5 - new"}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Runtime Check Run", wid, module="runtimechecktest")
    return wid, cid


def test_advance_rejects_nonpositive_cost_from_real_values(monkeypatch, tmp_path):
    # toughness at 4 -> new=5, cost = 5-5 = 0: non-positive, must be rejected
    # even though the pack passed load-time validation (new=1 -> 5-1=4 > 0).
    # xp balance is large so insufficient-balance can't be what triggers this.
    wid, cid = _campaign_with_runtime_positive_check_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    sheets.write(cid, "characters", "mara", "hero",
                 {"toughness": 4, "xp": {"current": 999, "max": 999}})
    with pytest.raises(sheets.SheetError, match="must be a positive integer"):
        sheets.advance(cid, "characters", "mara", "toughness")


# gen -- sheet generation nonce (mechanics Phase 5, Task 1)


def test_gen_minted_on_create(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    s = sheets.read(cid, "characters", "mara")
    assert isinstance(s["gen"], str) and len(s["gen"]) == 32


def test_gen_preserved_on_same_type_write(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    fields = sheets.read(cid, "characters", "mara")["fields"]
    sheets.write(cid, "characters", "mara", "medium", {**fields, "vigor": 3})
    assert sheets.read(cid, "characters", "mara")["gen"] == g1


def test_gen_minted_on_type_change(monkeypatch, tmp_path):
    # pool-basic has two characters-kind sheet types (medium, shifter; see
    # test_type_change_preserves_shared_drops_orphans) -- a real type change,
    # not a hand-edited fake, is enough to exercise the new-gen path.
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}})
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    sheets.write(cid, "characters", "mara", "shifter",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}})
    assert sheets.read(cid, "characters", "mara")["gen"] != g1


def test_legacy_file_without_gen_reads_none_and_gains_one_on_write(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None)
    p = sheets._campaign_path(cid, "characters", "mara")
    data = json.loads(p.read_text(encoding="utf-8"))
    del data["gen"]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert sheets.read(cid, "characters", "mara")["gen"] is None
    sheets.write(cid, "characters", "mara", "medium", data["fields"])
    assert isinstance(sheets.read(cid, "characters", "mara")["gen"], str)


def test_advance_preserves_gen(monkeypatch, tmp_path):
    # reuses the existing advancement fixture (Phase 7 tests, above)
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    sheets.advance(cid, "characters", "mara", "wits")
    assert sheets.read(cid, "characters", "mara")["gen"] == g1


# lock_for() -- public per-campaign RLock shared by every sheet mutator (#164, Phase 5)


def test_lock_for_public_and_reentrant(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    lock = sheets.lock_for(cid)
    with lock:
        with lock:  # RLock: no deadlock
            pass
    assert sheets.lock_for(cid) is lock


def test_write_resolves_module_inside_the_lock(monkeypatch, tmp_path):
    """Rebind serialization invariant: no campaign mutator may call
    modules.resolve outside lock_for(cid) -- otherwise a writer could resolve
    module A, lose the CPU to a rebind publishing B under the lock, then
    write under A after B is visible."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import modules as modules_mod
    real = modules_mod.resolve
    seen = []

    def spy(c):
        seen.append(sheets.lock_for(c)._is_owned())  # RLock: owned by us?
        return real(c)

    monkeypatch.setattr(modules_mod, "resolve", spy)
    sheets.write(cid, "characters", "mara", "medium", None)
    assert seen and all(seen)


def test_editor_write_serializes_with_advance(monkeypatch, tmp_path):
    """The pre-existing hole: a whole-sheet write interleaving advance's
    read-modify-write could lose the advancement. Serialized, both survive."""
    import threading
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    base_fields = sheets.read(cid, "characters", "mara")["fields"]
    errs = []

    def do_advance():
        try:
            sheets.advance(cid, "characters", "mara", "wits")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    def do_write():
        try:
            sheet_type = sheets.read(cid, "characters", "mara")["sheet_type"]
            sheets.write(cid, "characters", "mara", sheet_type,
                         {**base_fields, "wits": 2})
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=do_advance), threading.Thread(target=do_write)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Serialization guarantee is about atomicity, not order: whichever ran
    # second operated on the first's committed state, so neither raced a torn
    # read-modify-write. (Same-field last-write-wins between these two writers
    # is resolved by CAS in Task 3; this test only proves lock coverage --
    # no exception from a torn file, file parses cleanly.)
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"] == [] and not errs
