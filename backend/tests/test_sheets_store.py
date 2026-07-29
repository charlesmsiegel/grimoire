import json

import pytest

from grimoire.store import campaigns, characters, entities, locks, modules, pcs, sheets, worlds


def _campaign(monkeypatch, tmp_path, module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=module)
    return wid, cid


def test_write_and_read_with_derived(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 2, "grace": 3, "wits": 4, "occult": 2,
                  "essence": {"current": 6, "max": 10}}, expected=None)
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
    sheets.write(cid, "items", "moon-disc", "talisman", None, expected=None)
    s = sheets.read(cid, "items", "moon-disc")
    assert s["fields"]["power"] == 1                       # schema default
    assert s["fields"]["charges"] == {"current": 10, "max": 10}  # default max
    assert s["errors"] == []


def test_pcs_validate_against_characters(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "pcs", "seraphine", "medium", None, expected=None)
    assert sheets.read(cid, "pcs", "seraphine")["sheet_type"] == "medium"


def test_write_rejects_kind_mismatch(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "talisman", None, expected=None)  # items type


def test_write_rejects_unknown_type_and_bad_values(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "ghost", None, expected=None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", {"vigor": 99}, expected=None)


def test_unknown_key_rejected_not_filtered(monkeypatch, tmp_path):
    # _checked_write must stop silently dropping unknown submitted field
    # keys -- validate_sheet_values sees the full payload and rejects it.
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError, match="not a field"):
        sheets.write(cid, "characters", "mara", "medium", {"ghost_key": 1}, expected=None)


def test_write_without_module_rejected(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", None, expected=None)


def test_type_change_preserves_shared_drops_orphans(monkeypatch, tmp_path):
    # A type change is no longer server-filtered: the caller must send a
    # clean payload containing only keys valid for the new type (the
    # frontend's SheetEditor already builds this "survivors" map itself
    # before calling putSheet). Feeding an old-type-only key ("essence",
    # medium-only) while switching to "shifter" is rejected outright.
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}}, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    with pytest.raises(sheets.SheetError, match="not a field"):
        sheets.write(cid, "characters", "mara", "shifter",
                     {"vigor": 3, "essence": {"current": 5, "max": 10}}, expected=snap)
    sheets.write(cid, "characters", "mara", "shifter", {"vigor": 3}, expected=snap)
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "shifter"
    assert s["fields"]["vigor"] == 3         # shared via attributes group
    assert "essence" not in s["fields"]      # medium-only field never carried over


def test_invalid_after_module_switch(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
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
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    sheets.write(cid, "items", "moon-disc", "talisman", None, expected=None)
    assert sheets.list_refs(cid) == [("characters", "mara"), ("items", "moon-disc")]
    g = sheets.read(cid, "items", "moon-disc")["gen"]
    assert sheets.delete(cid, "items", "moon-disc", expected_gen=g) is True
    assert sheets.delete(cid, "items", "moon-disc", expected_gen=g) is False
    assert sheets.list_refs(cid) == [("characters", "mara")]


def test_bad_kind_and_eid_rejected(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "vehicles", "cart", "medium", None, expected=None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "../escape", "medium", None, expected=None)
    assert sheets.read(cid, "vehicles", "cart") is None


def test_write_rejects_wrong_typed_arguments(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", ["medium"], None, expected=None)  # type: ignore[arg-type]
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "mara", "medium", [1, 2], expected=None)  # type: ignore[arg-type]
    assert sheets.read(cid, "characters", 7) is None  # type: ignore[arg-type]


def test_world_write_bad_mid_and_colon_eid_rejected(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_world(wid, "..", "characters", "mara", "medium", None, expected=None)
    with pytest.raises(sheets.SheetError):
        sheets.write(cid, "characters", "c:evil", "medium", None, expected=None)


# World sheet tests

def test_world_sheet_crud_keyed_by_module(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None, expected=None)
    s = sheets.read_world(wid, "pool-basic", "characters", "mara")
    assert s["sheet_type"] == "medium" and s["errors"] == []
    assert sheets.world_sheet_modules(wid) == ["pool-basic"]
    assert sheets.world_list_refs(wid, "pool-basic") == [("characters", "mara")]
    assert sheets.read_world(wid, "d20-basic", "characters", "mara") is None
    assert sheets.delete_world(wid, "pool-basic", "characters", "mara",
                               expected_gen=s["gen"]) is True


def test_write_world_requires_cas(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    sheets.write_world(wid, "pool-basic", "characters", "winifred", "medium", None, expected=None)
    stored = sheets.read_world(wid, "pool-basic", "characters", "winifred")
    # stale snapshot: wrong gen
    with pytest.raises(sheets.SheetConflict):
        sheets.write_world(wid, "pool-basic", "characters", "winifred", "medium",
                           {"vigor": 2},
                           expected={"sheet_type": "medium", "fields": {}, "gen": "stale"})
    ok = {"sheet_type": stored["sheet_type"], "fields": stored["fields"], "gen": stored["gen"]}
    sheets.write_world(wid, "pool-basic", "characters", "winifred", "medium",
                       {"vigor": 2}, expected=ok)
    assert sheets.read_world(wid, "pool-basic", "characters", "winifred")["fields"]["vigor"] == 2


def test_write_world_creation_requires_cas(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Winifred")  # id: "winifred"
    sheets.write_world_creation(wid, "chargen", "characters", "winifred", "hero",
                                {}, expected=None)
    with pytest.raises(sheets.SheetConflict):
        sheets.write_world_creation(wid, "chargen", "characters", "winifred", "hero",
                                    {}, expected=None)   # already exists


def test_delete_world_requires_gen(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    sheets.write_world(wid, "pool-basic", "characters", "winifred", "medium", None, expected=None)
    stored = sheets.read_world(wid, "pool-basic", "characters", "winifred")
    with pytest.raises(sheets.SheetConflict):
        sheets.delete_world(wid, "pool-basic", "characters", "winifred", expected_gen="stale")
    assert sheets.delete_world(wid, "pool-basic", "characters", "winifred",
                               expected_gen=stored["gen"]) is True


def test_write_world_unknown_module(monkeypatch, tmp_path):
    wid, _ = _campaign(monkeypatch, tmp_path, module=None)
    with pytest.raises(modules.ModuleNotFound):
        sheets.write_world(wid, "ghost", "characters", "mara", "medium", None, expected=None)


def test_seed_on_create_matching_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium",
                       {"vigor": 3}, expected=None)
    sheets.write_world(wid, "d20-basic", "characters", "mara", "warrior", None, expected=None)
    cid = campaigns.create_campaign("Run", wid, module="pool-basic")
    s = sheets.read(cid, "characters", "mara")
    assert s["sheet_type"] == "medium" and s["fields"]["vigor"] == 3
    # only the matching module's sheets seeded
    assert sheets.list_refs(cid) == [("characters", "mara")]


def test_no_seed_without_module_and_no_reseed_on_bind(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None, expected=None)
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
    sheets.write(cid, "items", "moon-disc", "talisman", None, expected=None)
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
    sheets.write(cid, "items", "moon-disc", "talisman", None, expected=None)
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
    sheets.write_world(wid, "pool-basic", "items", "moon-disc", "talisman", None, expected=None)
    cov = sheets.world_coverage(wid, "pool-basic")
    assert cov["items"] == {"total": 1, "sheeted": 1, "invalid": 0}
    assert sheets.world_coverage(wid, "ghost") == {}


def test_seed_via_world_default_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    modules.set_world_module(wid, "pool-basic")
    sheets.write_world(wid, "pool-basic", "characters", "mara", "medium", None, expected=None)
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

    # sheets writes through store.atomic since #233; patch the replace there
    monkeypatch.setattr(sheets.atomic.os, "replace", boom)
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
                          {"attributes": {"strength": 12, "wits": 3}}, expected=None)
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"] == []
    assert s["fields"]["strength"] == 12
    assert s["fields"]["wits"] == 3


def test_write_creation_over_budget_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"strength": 20, "wits": 5}}, expected=None)


def test_write_creation_field_outside_range_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"strength": 999, "wits": 0}}, expected=None)


def test_write_creation_field_not_in_pool_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"attributes": {"hp": 5}}, expected=None)


def test_write_creation_omitted_costed_field_uses_floor_not_default(monkeypatch, tmp_path):
    # strength's schema default is 10 (well above its floor of 1) but it's
    # omitted from spends -- must resolve to the pool floor (1), not the
    # schema default (10), or the budget-omission loophole reopens.
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero",
                          {"attributes": {"wits": 2}}, expected=None)
    s = sheets.read(cid, "characters", "mara")
    assert s["fields"]["strength"] == 1
    assert s["fields"]["wits"] == 2


def test_write_creation_unknown_pool_rejected(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.write_creation(cid, "characters", "mara", "hero",
                              {"ghost_pool": {"strength": 12}}, expected=None)


def test_write_creation_empty_spends_falls_through_to_defaults(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_creation(cid, "characters", "mara", "hero", {}, expected=None)
    s = sheets.read(cid, "characters", "mara")
    assert s["fields"]["strength"] == 1   # floor, not schema default 10
    assert s["fields"]["hp"] == {"current": 10, "max": 10}  # non-costed field: schema default


def test_write_world_creation(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    sheets.write_world_creation(wid, "chargen", "characters", "mara", "hero",
                                {"attributes": {"strength": 14}}, expected=None)
    s = sheets.read_world(wid, "chargen", "characters", "mara")
    assert s["fields"]["strength"] == 14


# write_creation / write_world_creation reject a target that doesn't exist
# (#161/#164 follow-up: prevents orphaned sheets for nonexistent records)

def test_write_creation_missing_character_raises_not_found(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(characters.CharacterNotFound):
        sheets.write_creation(cid, "characters", "nobody", "hero", {}, expected=None)


def test_write_creation_missing_pc_raises_not_found(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(pcs.PCNotFound):
        sheets.write_creation(cid, "pcs", "nobody", "hero", {}, expected=None)


def test_write_creation_missing_entity_raises_not_found(monkeypatch, tmp_path):
    _, cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sheets.write_creation(cid, "items", "nobody", "hero", {}, expected=None)


def test_write_world_creation_missing_character_raises_not_found(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(characters.CharacterNotFound):
        sheets.write_world_creation(wid, "chargen", "characters", "nobody", "hero", {}, expected=None)


def test_write_world_creation_missing_pc_raises_not_found(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(pcs.PCNotFound):
        sheets.write_world_creation(wid, "chargen", "pcs", "nobody", "hero", {}, expected=None)


def test_write_world_creation_missing_entity_raises_not_found(monkeypatch, tmp_path):
    wid, _cid = _campaign_with_creation_module(monkeypatch, tmp_path)
    with pytest.raises(entities.EntityNotFound):
        sheets.write_world_creation(wid, "chargen", "items", "nobody", "hero", {}, expected=None)


# advance() -- resource-pool spend to raise a sheet field (#164, Phase 7)


def _campaign_with_advancement_module(monkeypatch, tmp_path, campaign_name="Run"):
    # campaign_name is overridable because cid = slugify(name), and
    # locks._campaign_locks is a module-level global that outlives any one
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
    sheets.write(cid, "characters", "mara", "hero", {"wits": 2, "xp": {"current": 20, "max": 999}},
                 expected=None)
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
    sheets.write(cid, "characters", "poor", "hero", {"wits": 2, "xp": {"current": 1, "max": 999}},
                 expected=None)
    with pytest.raises(sheets.SheetError, match="needs 9"):
        sheets.advance(cid, "characters", "poor", "wits")


def test_advance_field_at_max(monkeypatch, tmp_path):
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Capped")  # id: "capped"
    sheets.write(cid, "characters", "capped", "hero", {"wits": 5, "xp": {"current": 999, "max": 999}},
                 expected=None)
    with pytest.raises(sheets.SheetError):
        sheets.advance(cid, "characters", "capped", "wits")


def test_advance_no_advancement_block(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)  # pool-basic has no advancement block yet
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
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
                 {"wits": 2, "xp": {"current": 20, "max": 999}}, expected=None)
    assert sheets.read(cid, "characters", "ghost") is not None  # orphan sheet really exists
    with pytest.raises(characters.CharacterNotFound):
        sheets.advance(cid, "characters", "ghost", "wits")


def test_advance_concurrent_calls_only_one_succeeds(monkeypatch, tmp_path):
    import threading
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Duelist")  # id: "duelist"
    sheets.write(cid, "characters", "duelist", "hero", {"wits": 2, "xp": {"current": 9, "max": 999}},
                 expected=None)
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
    # exercises campaign_lock's cold-registry path directly, not just contention
    # on an already-created lock. Uses a campaign name unique to this test so
    # its cid can't already be registered by a sibling advance test that ran
    # first in the same process (see _campaign_with_advancement_module).
    import threading
    wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path, campaign_name="Cold Registry Run")
    characters.create_character(worlds.world_root(wid), "First")  # id: "first"
    sheets.write(cid, "characters", "first", "hero", {"wits": 2, "xp": {"current": 9, "max": 999}},
                 expected=None)
    # sheets.write above now also serializes on campaign_lock(cid) (Task 2),
    # which warms the registry -- pop it back out so this test still exercises
    # the concurrent first-ever-call race through advance()'s own lock.
    del locks._campaign_locks[cid]
    assert cid not in locks._campaign_locks
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
                 {"strength": 2, "dexterity": 3, "xp": {"current": 12, "max": 999}}, expected=None)
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
                 {"strength": 2, "dexterity": 3, "xp": {"current": 11, "max": 999}}, expected=None)
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
                 {"toughness": 4, "xp": {"current": 999, "max": 999}}, expected=None)
    with pytest.raises(sheets.SheetError, match="must be a positive integer"):
        sheets.advance(cid, "characters", "mara", "toughness")


# gen -- sheet generation nonce (mechanics Phase 5, Task 1)


def test_gen_minted_on_create(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    s = sheets.read(cid, "characters", "mara")
    assert isinstance(s["gen"], str) and len(s["gen"]) == 32


def test_gen_preserved_on_same_type_write(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "medium", {**snap["fields"], "vigor": 3}, expected=snap)
    assert sheets.read(cid, "characters", "mara")["gen"] == g1


def test_gen_minted_on_type_change(monkeypatch, tmp_path):
    # pool-basic has two characters-kind sheet types (medium, shifter; see
    # test_type_change_preserves_shared_drops_orphans) -- a real type change,
    # not a hand-edited fake, is enough to exercise the new-gen path.
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "essence": {"current": 5, "max": 10}}, expected=None)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "shifter", {"vigor": 3}, expected=snap)
    assert sheets.read(cid, "characters", "mara")["gen"] != g1


def test_legacy_file_without_gen_reads_none_and_gains_one_on_write(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    p = sheets._campaign_path(cid, "characters", "mara")
    data = json.loads(p.read_text(encoding="utf-8"))
    del data["gen"]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert sheets.read(cid, "characters", "mara")["gen"] is None
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "medium", data["fields"], expected=snap)
    assert isinstance(sheets.read(cid, "characters", "mara")["gen"], str)


def test_advance_preserves_gen(monkeypatch, tmp_path):
    # reuses the existing advancement fixture (Phase 7 tests, above)
    _wid, cid = _campaign_with_advancement_module(monkeypatch, tmp_path)
    g1 = sheets.read(cid, "characters", "mara")["gen"]
    sheets.advance(cid, "characters", "mara", "wits")
    assert sheets.read(cid, "characters", "mara")["gen"] == g1


# campaign_lock() -- the shared per-campaign RLock every sheet mutator takes
# (#164, Phase 5; registry moved to store/locks.py in #245, tested there)


def test_write_resolves_module_inside_the_lock(monkeypatch, tmp_path):
    """Rebind serialization invariant: no campaign mutator may call
    modules.resolve outside campaign_lock(cid) -- otherwise a writer could resolve
    module A, lose the CPU to a rebind publishing B under the lock, then
    write under A after B is visible."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import modules as modules_mod
    real = modules_mod.resolve
    seen = []

    def spy(c):
        seen.append(locks.campaign_lock(c)._is_owned())  # RLock: owned by us?
        return real(c)

    monkeypatch.setattr(modules_mod, "resolve", spy)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    assert seen and all(seen)


# expected -- mandatory whole-sheet CAS (mechanics Phase 5, Task 3)
# NOTE: the brief's snippet uses a placeholder sheet type "adventurer" and a
# placeholder field "athletics" -- adapted here to pool-basic's real
# "medium" characters type and its "vigor" dots field (see
# _campaign_with_creation_module etc. above for the pattern of adapting
# brief placeholders to real fixtures).


def _snapshot(cid, kind, eid):
    s = sheets.read(cid, kind, eid)
    return {"sheet_type": s["sheet_type"], "fields": s["fields"], "gen": s["gen"]}


def test_cas_none_expected_creates_then_conflicts(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    with pytest.raises(sheets.SheetConflict):
        sheets.write(cid, "characters", "mara", "medium", None, expected=None)


def test_cas_matching_snapshot_writes(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "medium",
                 {**snap["fields"], "vigor": 3}, expected=snap)
    assert sheets.read(cid, "characters", "mara")["fields"]["vigor"] == 3


def test_cas_stale_fields_conflict(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "medium",
                 {**snap["fields"], "vigor": 3}, expected=snap)
    with pytest.raises(sheets.SheetConflict):  # snap is now stale
        sheets.write(cid, "characters", "mara", "medium",
                     {**snap["fields"], "vigor": 1}, expected=snap)


def test_cas_gen_mismatch_with_identical_content_conflicts(monkeypatch, tmp_path):
    """ABA: delete + recreate with identical type/default fields must still
    409 a stale editor whose snapshot matches by value."""
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    snap = _snapshot(cid, "characters", "mara")
    sheets.delete(cid, "characters", "mara", expected_gen=snap["gen"])
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    live = _snapshot(cid, "characters", "mara")
    assert live["sheet_type"] == snap["sheet_type"] and live["fields"] == snap["fields"]
    with pytest.raises(sheets.SheetConflict):
        sheets.write(cid, "characters", "mara", "medium",
                     snap["fields"], expected=snap)


# delete CAS -- mandatory expected_gen (mechanics Phase 5, Task 4)
# NOTE: the brief's snippet uses a placeholder sheet type "adventurer" and a
# `cid` fixture -- adapted here to pool-basic's real "medium" characters type
# and this file's `_campaign` helper, matching the CAS tests above.


def test_delete_cas(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    g = sheets.read(cid, "characters", "mara")["gen"]
    with pytest.raises(sheets.SheetConflict):
        sheets.delete(cid, "characters", "mara", expected_gen="stale" + g[:28])
    assert sheets.read(cid, "characters", "mara") is not None
    assert sheets.delete(cid, "characters", "mara", expected_gen=g) is True
    assert sheets.delete(cid, "characters", "mara", expected_gen=g) is False  # nothing left


def test_delete_missing_file_never_conflicts(monkeypatch, tmp_path):
    """A missing sheet returns False regardless of expected_gen -- it is
    never a conflict, even when the caller passes a bogus gen."""
    _, cid = _campaign(monkeypatch, tmp_path)
    assert sheets.delete(cid, "characters", "mara", expected_gen="anything") is False
    assert sheets.delete(cid, "characters", "mara", expected_gen=None) is False


def test_delete_legacy_gen_null_matches_expected_none(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    p = sheets._campaign_path(cid, "characters", "mara")
    data = json.loads(p.read_text(encoding="utf-8"))
    del data["gen"]
    p.write_text(json.dumps(data), encoding="utf-8")
    assert sheets.read(cid, "characters", "mara")["gen"] is None
    assert sheets.delete(cid, "characters", "mara", expected_gen=None) is True


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
            snap = _snapshot(cid, "characters", "mara")
            sheets.write(cid, "characters", "mara", snap["sheet_type"],
                         {**base_fields, "wits": 2}, expected=snap)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=do_advance), threading.Thread(target=do_write)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Serialization guarantee is about atomicity, not order: whichever ran
    # second operated on the first's committed state, so neither raced a torn
    # read-modify-write. With Task 3's mandatory CAS, do_write's snapshot
    # (read outside the lock) can go stale if do_advance's in-lock
    # read-modify-write lands first -- that surfaces as a clean SheetConflict
    # rejection, not corruption. This test now only proves the lock still
    # serializes both writers so the file is never torn.
    s = sheets.read(cid, "characters", "mara")
    assert s["errors"] == []
    assert all(isinstance(e, sheets.SheetConflict) for e in errs)


# set_field -- per-field strict-CAS apply primitive (mechanics Phase 5, Task 5)
# NOTE: the brief's snippet uses a placeholder sheet type "adventurer" with
# placeholder fields "hp"/"athletics" -- adapted here to pool-basic's real
# "medium" characters type: "essence" (resource), "health" (track), "gear"
# (list) as the three mutable-type fields, and "vigor" (dots, static) as the
# rejected-at-the-write-boundary field, matching the CAS tests above.


def _setup_medium_sheet(monkeypatch, tmp_path):
    _, cid = _campaign(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", None, expected=None)
    return cid, sheets.read(cid, "characters", "mara")


def test_set_field_resource_happy_path(monkeypatch, tmp_path):
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["essence"]                     # {"current": C, "max": M}
    sheets.set_field(cid, "characters", "mara", "essence",
                     {"current": live["current"] - 2}, expect=live)
    got = sheets.read(cid, "characters", "mara")["fields"]["essence"]
    assert got == {"current": live["current"] - 2, "max": live["max"]}


def test_set_field_track_happy_path(monkeypatch, tmp_path):
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["health"]                      # plain int, default 0
    sheets.set_field(cid, "characters", "mara", "health", live + 3, expect=live)
    assert sheets.read(cid, "characters", "mara")["fields"]["health"] == live + 3


def test_set_field_list_happy_path(monkeypatch, tmp_path):
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["gear"]                        # []
    sheets.set_field(cid, "characters", "mara", "gear", ["silver dagger"], expect=live)
    assert sheets.read(cid, "characters", "mara")["fields"]["gear"] == ["silver dagger"]


def test_set_field_max_tamper_ignored(monkeypatch, tmp_path):
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["essence"]
    sheets.set_field(cid, "characters", "mara", "essence",
                     {"current": 1, "max": 999}, expect=live)
    assert sheets.read(cid, "characters", "mara")["fields"]["essence"]["max"] == live["max"]


def test_set_field_static_field_rejected_at_write_boundary(monkeypatch, tmp_path):
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid, "characters", "mara", "vigor",   # dots: static
                         s["fields"].get("vigor", 0) + 1,
                         expect=s["fields"].get("vigor", 0))


def test_set_field_unknown_field_rejected(monkeypatch, tmp_path):
    cid, _s = _setup_medium_sheet(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid, "characters", "mara", "nonesuch", 1, expect=0)


def test_set_field_conflict_on_stale_expect(monkeypatch, tmp_path):
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["essence"]
    sheets.set_field(cid, "characters", "mara", "essence",
                     {"current": live["current"] - 1}, expect=live)
    with pytest.raises(sheets.SheetConflict):
        sheets.set_field(cid, "characters", "mara", "essence",
                         {"current": live["current"] - 2}, expect=live)


def test_set_field_conflict_even_when_live_equals_value(monkeypatch, tmp_path):
    """Duplicate save / independent same-value mutation must be REPORTED."""
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["essence"]
    target = {"current": live["current"] - 2}
    sheets.set_field(cid, "characters", "mara", "essence", target, expect=live)
    with pytest.raises(sheets.SheetConflict) as ei:
        sheets.set_field(cid, "characters", "mara", "essence", target, expect=live)
    assert "already applied or independently changed" in str(ei.value)


def test_set_field_single_field_isolation(monkeypatch, tmp_path):
    """An unrelated field changed between materialize and apply survives."""
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live_essence = s["fields"]["essence"]
    snap = {"sheet_type": s["sheet_type"], "fields": s["fields"], "gen": s["gen"]}
    sheets.write(cid, "characters", "mara", s["sheet_type"],
                 {**s["fields"], "vigor": 4}, expected=snap)
    sheets.set_field(cid, "characters", "mara", "essence",
                     {"current": live_essence["current"] - 1}, expect=live_essence)
    got = sheets.read(cid, "characters", "mara")["fields"]
    assert got["vigor"] == 4 and got["essence"]["current"] == live_essence["current"] - 1


def _campaign_with_advancement_and_hp_module(monkeypatch, tmp_path):
    """Like _campaign_with_advancement_module, plus an extra 'hp' resource
    field untouched by advancement -- exercises set_field racing advance()
    on genuinely independent fields of the same sheet."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    d = tmp_path / "modules" / "advhptest"
    d.mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Advancement HP Test\n---\n", encoding="utf-8")
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
                "fields": [
                    {"key": "xp", "type": "resource", "max": 999},
                    {"key": "hp", "type": "resource", "max": 20},
                ],
                "advancement": {"pool": "xp", "costs": {"wits": "new * 3"}},
            },
        },
    }), encoding="utf-8")
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara")  # id: "mara"
    cid = campaigns.create_campaign("Advancement HP Run", wid, module="advhptest")
    sheets.write(cid, "characters", "mara", "hero",
                 {"wits": 2, "xp": {"current": 20, "max": 999}, "hp": {"current": 10, "max": 20}},
                 expected=None)
    return cid


def test_set_field_race_vs_advance(monkeypatch, tmp_path):
    """Threaded: both complete under the lock, neither write lost."""
    import threading
    cid = _campaign_with_advancement_and_hp_module(monkeypatch, tmp_path)
    s = sheets.read(cid, "characters", "mara")
    results = []

    def _try(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            results.append(e)

    t1 = threading.Thread(target=lambda: results.append(
        sheets.advance(cid, "characters", "mara", "wits")))
    t2 = threading.Thread(target=lambda: _try(lambda: sheets.set_field(
        cid, "characters", "mara", "hp", {"current": 1}, expect=s["fields"]["hp"])))
    t1.start(); t2.start(); t1.join(); t2.join()
    got = sheets.read(cid, "characters", "mara")["fields"]
    assert got["hp"]["current"] == 1                          # set_field landed
    assert got["wits"] == s["fields"]["wits"] + 1              # advance landed


def test_set_field_bad_kind_and_eid_rejected(monkeypatch, tmp_path):
    """Same path-traversal guard as write()/delete() -- see
    test_bad_kind_and_eid_rejected above."""
    cid, _s = _setup_medium_sheet(monkeypatch, tmp_path)
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid, "vehicles", "cart", "essence", {"current": 1}, expect=0)
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid, "characters", "../escape", "essence", {"current": 1}, expect=0)


def test_set_field_rejects_kind_that_traverses_into_another_campaign(monkeypatch, tmp_path):
    """A crafted `kind` combined with FILE_KINDS/safe_id(eid) being
    unchecked would let a caller in one campaign's set_field reach a sheet
    file belonging to a different campaign (kind + eid form the raw
    "<kind>--<eid>.json" filename component). Regression for a real exploit
    found in self-review: `kind` containing "..\\..\\<other-cid>\\sheets\\
    characters" plus eid "mara" resolved outside this campaign's sheets dir
    entirely and overwrote the victim's stored field."""
    _, cid1 = _campaign(monkeypatch, tmp_path)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))  # keep same store
    from grimoire.store import worlds as worlds_mod
    wid = worlds_mod.create_world("Realm 2")
    cid2 = campaigns.create_campaign("Attacker", wid, module="pool-basic")
    sheets.write(cid1, "characters", "mara", "medium", None, expected=None)
    victim = sheets.read(cid1, "characters", "mara")
    p1 = sheets._campaign_path(cid1, "characters", "mara")
    p2dir = sheets._campaign_dir(cid2)
    import os as _os
    rel = _os.path.relpath(p1, p2dir)
    body = rel[:-len(".json")]
    idx = body.rfind("--")
    kind, eid = body[:idx], body[idx + 2:]
    with pytest.raises(sheets.SheetError):
        sheets.set_field(cid2, kind, eid, "essence", {"current": 1},
                         expect=victim["fields"]["essence"])
    assert sheets.read(cid1, "characters", "mara")["fields"]["essence"] == victim["fields"]["essence"]


def test_set_field_resolves_module_inside_the_lock(monkeypatch, tmp_path):
    """Same rebind-serialization invariant as write() -- see
    test_write_resolves_module_inside_the_lock above."""
    cid, s = _setup_medium_sheet(monkeypatch, tmp_path)
    live = s["fields"]["essence"]
    from grimoire.store import modules as modules_mod
    real = modules_mod.resolve
    seen = []

    def spy(c):
        seen.append(locks.campaign_lock(c)._is_owned())  # RLock: owned by us?
        return real(c)

    monkeypatch.setattr(modules_mod, "resolve", spy)
    sheets.set_field(cid, "characters", "mara", "essence",
                     {"current": live["current"] - 1}, expect=live)
    assert seen and all(seen)


def test_instance_errors_includes_derived_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Realm System")
    root = modules.user_dir() / mid
    (root / "sheets.json").write_text(json.dumps({
        "groups": {"a": {"fields": [{"key": "strength", "type": "dots", "max": 5}],
                          "derived": {"bad": "10 // strength"}}},
        "sheet_types": {"warden": {"label": "W", "kind": "characters",
                                   "groups": ["a"], "fields": []}}}), encoding="utf-8")
    pack = modules.load_pack(mid)
    assert pack["errors"] == []          # valid at defaults... unless default 0
    errs = sheets.instance_errors(pack, "characters", "warden", {"strength": 0})
    assert any("bad" in e for e in errs)     # division by zero at stored values
    assert sheets.instance_errors(pack, "characters", "warden", {"strength": 2}) == []
    assert sheets.instance_errors(pack, "characters", "ghost", {}) != []
    assert sheets.instance_errors(pack, "items", "warden", {}) != []
