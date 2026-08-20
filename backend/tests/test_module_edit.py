import io
import json
import threading
import zipfile

import pytest

from grimoire.store import campaigns, locks, module_edit, modules, worlds
from grimoire.store.frontmatter import parse_frontmatter
from grimoire.store.module_edit import migrate as me_migrate


def _zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return buf.getvalue()


def _mk(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return modules.create_module("Realm System")


GROUP = {"label": "Attributes",
         "fields": [{"key": "strength", "label": "Strength", "type": "dots", "max": 5},
                    {"key": "essence", "label": "Essence", "type": "resource", "max": 10}],
         "derived": {"might": "strength * 2"}}
TYPE = {"label": "Warden", "kind": "characters", "groups": ["attributes"],
        "fields": [{"key": "notes_line", "label": "Notes", "type": "text"}],
        "derived": {"guard": "strength + 1"}}


def _mk_schema(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    assert module_edit.upsert_group(mid, "attributes", GROUP)["ok"]
    assert module_edit.upsert_sheet_type(mid, "warden", TYPE)["ok"]
    return mid


def test_set_manifest_round_trip(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    res = module_edit.set_manifest(mid, name="Realm System", description="d",
                                   version="0.2", dice="1d20", notes="notes body")
    assert res["ok"] is True and res["errors"] == []
    pack = modules.load_pack(mid)
    assert pack["manifest"]["version"] == "0.2"
    assert pack["manifest"]["notes"].strip() == "notes body"


def test_set_manifest_rejects_invalid(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    before = (modules.user_dir() / mid / "module.md").read_text(encoding="utf-8")
    res = module_edit.set_manifest(mid, name="", description="", version="",
                                   dice="", notes="")
    assert res["ok"] is False
    assert any("requires a name" in e for e in res["errors"])
    # live pack untouched, no staging debris
    assert (modules.user_dir() / mid / "module.md").read_text(encoding="utf-8") == before
    staging = tmp_path / ".module-staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    res = module_edit.set_manifest(mid, name="Renamed", description="", version="",
                                   dice="", notes="", dry_run=True)
    assert res["ok"] is True
    assert modules.load_pack(mid)["manifest"]["name"] == "Realm System"


def test_builtin_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(modules.ModuleError):
        module_edit.set_manifest("d20-basic", name="X", description="",
                                 version="", dice="", notes="")


def test_recover_pre_swap_discards(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    # Simulate a crash after journal write, before any rename: live + staging.
    base = tmp_path / ".module-staging" / "nonce1"
    staging = base / mid
    staging.mkdir(parents=True)
    (staging / "module.md").write_text("---\nname: Ghost\n---\n", encoding="utf-8")
    (tmp_path / ".module-staging" / "nonce1.journal.json").write_text(
        json.dumps({"mid": mid, "nonce": "nonce1", "migration": None}), encoding="utf-8")
    module_edit.recover()
    assert modules.load_pack(mid)["manifest"]["name"] == "Realm System"
    assert not (tmp_path / ".module-staging" / "nonce1.journal.json").exists()
    assert not base.exists()


def test_recover_between_renames_publishes(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    # Simulate: live renamed to trash, staging not yet renamed in.
    base = tmp_path / ".module-staging" / "nonce2"
    trash = base / "trash" / mid
    trash.parent.mkdir(parents=True)
    live = modules.user_dir() / mid
    live.rename(trash)
    staging = base / mid
    staging.mkdir(parents=True)
    (staging / "module.md").write_text("---\nname: Published\n---\n", encoding="utf-8")
    (staging / "sheets.json").write_text('{"groups": {}, "sheet_types": {}}', encoding="utf-8")
    (tmp_path / ".module-staging" / "nonce2.journal.json").write_text(
        json.dumps({"mid": mid, "nonce": "nonce2", "migration": None}), encoding="utf-8")
    module_edit.recover()
    assert modules.load_pack(mid)["manifest"]["name"] == "Published"
    assert not base.exists()


def test_recover_post_swap_cleans_trash(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    base = tmp_path / ".module-staging" / "nonce3"
    trash = base / "trash" / mid
    trash.mkdir(parents=True)
    (trash / "module.md").write_text("---\nname: Old\n---\n", encoding="utf-8")
    (tmp_path / ".module-staging" / "nonce3.journal.json").write_text(
        json.dumps({"mid": mid, "nonce": "nonce3", "migration": None}), encoding="utf-8")
    module_edit.recover()
    assert modules.load_pack(mid)["manifest"]["name"] == "Realm System"
    assert not base.exists()


def test_malformed_journal_quarantined_not_destructive(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    d = tmp_path / ".module-staging"
    keep = d / "aaaabbbbccccddddaaaabbbbccccdddd" / mid
    keep.mkdir(parents=True)
    (keep / "module.md").write_text("---\nname: Rescue\n---\n", encoding="utf-8")
    (d / "torn.journal.json").write_text("{not json", encoding="utf-8")
    module_edit.recover()
    assert (d / "torn.journal.bad").exists()       # quarantined, not deleted
    assert keep.exists()                           # recovery data preserved
    assert d.exists()                              # never rmtree'd wholesale


def test_quarantine_persists_across_recover_runs(monkeypatch, tmp_path):
    """P1-1: `quarantined` is local to one recover() call, so a *.journal.bad
    left by a PRIOR run leaves no trace in it. The debris sweep must still
    stay disabled on the NEXT recover() call (e.g. the next edit's implicit
    recover()) or it destroys the very staging dir the quarantine preserved."""
    mid = _mk(monkeypatch, tmp_path)
    d = tmp_path / ".module-staging"
    keep = d / "aaaabbbbccccddddaaaabbbbccccdddd" / mid
    keep.mkdir(parents=True)
    (keep / "module.md").write_text("---\nname: Rescue\n---\n", encoding="utf-8")
    (d / "torn.journal.json").write_text("{not json", encoding="utf-8")
    module_edit.recover()
    assert (d / "torn.journal.bad").exists()
    assert keep.exists()
    # A second, independent recover() call: `quarantined` starts empty again,
    # but the leftover torn.journal.bad file must still veto the sweep.
    module_edit.recover()
    assert (d / "torn.journal.bad").exists()
    assert keep.exists()                           # NOT swept away this time


def test_edit_excludes_campaign_locked_consumer(monkeypatch, tmp_path):
    """User-vs-LLM exclusion: an edit blocks while a campaign lock is held."""
    mid = _mk(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    order: list[str] = []

    def edit():
        module_edit.set_manifest(mid, name="Realm System", description="x",
                                 version="", dice="", notes="")
        order.append("edit-done")

    with locks.campaign_lock(cid):
        t = threading.Thread(target=edit)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive()          # edit is waiting on the campaign lock
        order.append("lock-released")
    t.join(timeout=5)
    assert not t.is_alive()
    assert order == ["lock-released", "edit-done"]


def test_set_layout_lands_with_display_errors(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.set_layout(mid, {"sheet_types": {"warden": {"fields": ["ghost_key"]}}})
    assert res["ok"] is True                       # display problems never reject
    assert any("ghost_key" in e["message"] for e in res["display_errors"])
    assert module_edit.set_layout(mid, {"sheet_types": {"warden": {"group": "attributes"}}})["ok"]
    pack = modules.load_pack(mid)
    assert "warden" in pack["layout"]["sheet_types"] and pack["display_errors"] == []


def test_set_theme_round_trip(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    theme = {"colors": {"bg": "#171a21", "ink": "#d8d2c4"}, "dots": "diamond"}
    assert module_edit.set_theme(mid, theme)["ok"]
    assert modules.load_pack(mid)["theme"]["dots"] == "diamond"


def test_upsert_group_and_type(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    pack = modules.load_pack(mid)
    assert pack["errors"] == []
    assert pack["sheets"]["groups"]["attributes"]["label"] == "Attributes"
    assert pack["sheets"]["sheet_types"]["warden"]["kind"] == "characters"


def test_upsert_group_bad_expression_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    bad = {**GROUP, "derived": {"might": "strength +"}}
    res = module_edit.upsert_group(mid, "attributes", bad)
    assert res["ok"] is False
    assert any("might" in e for e in res["errors"])
    assert modules.load_pack(mid)["errors"] == []  # live pack still valid


def test_delete_group_with_fatal_ref_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.delete_group(mid, "attributes")
    assert res["ok"] is False
    assert any("attributes" in e for e in res["errors"])  # named referee


def test_delete_type_then_group_cascades_layout(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    layout = {"sheet_types": {"warden": {"column": [
        {"group": "attributes"},
        {"fields": ["notes_line"]},
        {"derived": ["guard"]}]}}}
    (modules.user_dir() / mid / "layout.json").write_text(
        json.dumps(layout), encoding="utf-8")
    assert module_edit.delete_sheet_type(mid, "warden")["ok"]
    pack = modules.load_pack(mid)
    assert pack["errors"] == []
    assert pack["layout"]["sheet_types"] == {}      # type's tree dropped
    assert pack["display_errors"] == []             # no dangling display refs
    assert module_edit.delete_group(mid, "attributes")["ok"]
    assert "attributes" not in modules.load_pack(mid)["sheets"]["groups"]


def test_delete_field_prunes_layout_entry(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    layout = {"sheet_types": {"warden": {"column": [
        {"group": "attributes"}, {"fields": ["notes_line"]}]}}}
    (modules.user_dir() / mid / "layout.json").write_text(
        json.dumps(layout), encoding="utf-8")
    slim = {**TYPE, "fields": []}  # drop notes_line (no fatal refs on it)
    assert module_edit.upsert_sheet_type(mid, "warden", slim)["ok"]
    pack = modules.load_pack(mid)
    assert pack["display_errors"] == []
    tree = json.dumps(pack["layout"])
    assert "notes_line" not in tree


def test_prune_scoped_to_composing_types(monkeypatch, tmp_path):
    """Removing warden's notes_line must not touch a disjoint type's
    same-named field in ITS layout tree."""
    mid = _mk_schema(monkeypatch, tmp_path)
    other_group = {"label": "Spirit", "fields": [
        {"key": "notes_line", "label": "Notes", "type": "text"}]}
    other_type = {"label": "Medium", "kind": "characters",
                  "groups": ["spirit"], "fields": []}
    assert module_edit.upsert_group(mid, "spirit", other_group)["ok"]
    assert module_edit.upsert_sheet_type(mid, "medium", other_type)["ok"]
    layout = {"sheet_types": {
        "warden": {"fields": ["notes_line"]},
        "medium": {"fields": ["notes_line"]}}}
    (modules.user_dir() / mid / "layout.json").write_text(
        json.dumps(layout), encoding="utf-8")
    slim = {**TYPE, "fields": []}          # remove warden's notes_line
    assert module_edit.upsert_sheet_type(mid, "warden", slim)["ok"]
    raw = json.loads((modules.user_dir() / mid / "layout.json").read_text(encoding="utf-8"))
    assert "warden" not in raw["sheet_types"] \
        or "notes_line" not in json.dumps(raw["sheet_types"].get("warden"))
    assert raw["sheet_types"]["medium"] == {"fields": ["notes_line"]}


CHECK = {"label": "Guard Reflexes", "roll": "1d20 + {might}", "requires": ["attributes"]}


def test_upsert_and_delete_check(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "guard_reflexes", CHECK)["ok"]
    assert "guard_reflexes" in modules.load_pack(mid)["checks"]
    bad = {**CHECK, "roll": "1d20 + {nonsense}"}
    res = module_edit.upsert_check(mid, "guard_reflexes", bad)
    assert res["ok"] is False and any("nonsense" in e for e in res["errors"])
    assert module_edit.delete_check(mid, "guard_reflexes")["ok"]
    assert "guard_reflexes" not in modules.load_pack(mid)["checks"]


def test_check_defaults_round_trip(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.set_check_defaults(mid, {"difficulty": 12})["ok"]
    assert modules.load_pack(mid)["checks"]["_defaults"]["difficulty"] == 12
    assert module_edit.set_check_defaults(mid, {})["ok"]
    assert "_defaults" not in modules.load_pack(mid)["checks"]


def test_rule_round_trip_and_flags(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.upsert_rule(mid, "combat-basics",
                                  {"always": True, "keys": ["melee", "brawl"],
                                   "sheet_types": ["warden"]},
                                  "Swing first.")
    assert res["ok"]
    pack = modules.load_pack(mid)
    doc = next(r for r in pack["rules"] if r["id"] == "combat-basics")
    assert doc["always"] is True and doc["keys"] == ["melee", "brawl"]
    assert modules.read_rule(mid, "combat-basics")["body"].strip() == "Swing first."
    # unknown sheet_type flag rejects
    bad = module_edit.upsert_rule(mid, "combat-basics", {"sheet_types": ["ghost"]}, "x")
    assert bad["ok"] is False
    # delete blocked while a check references the doc
    assert module_edit.upsert_check(mid, "brawl", {**CHECK, "rules": ["combat-basics"]})["ok"]
    res = module_edit.delete_rule(mid, "combat-basics")
    assert res["ok"] is False and any("combat-basics" in e for e in res["errors"])
    assert module_edit.delete_check(mid, "brawl")["ok"]
    assert module_edit.delete_rule(mid, "combat-basics")["ok"]


def test_content_round_trip_with_sheet(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    item_type = {"label": "Relic", "kind": "items", "groups": [],
                 "fields": [{"key": "power", "type": "dots", "max": 5}]}
    assert module_edit.upsert_sheet_type(mid, "relic", item_type)["ok"]
    res = module_edit.upsert_content(
        mid, "items", "sunblade", name="Sunblade", body="A blade of dawn.",
        keys="sunblade, dawn", fields={},
        sheet={"sheet_type": "relic", "fields": {"power": 3}})
    assert res["ok"]
    got = modules.read_content(mid, "items", "sunblade")
    assert got["name"] == "Sunblade" and got["fields"] == {"power": 3}
    # invalid stat block rejects
    res = module_edit.upsert_content(
        mid, "items", "sunblade", name="Sunblade", body="x", keys="", fields={},
        sheet={"sheet_type": "relic", "fields": {"power": 9}})
    assert res["ok"] is False and any("power" in e for e in res["errors"])
    # delete removes md + sidecar
    assert module_edit.delete_content(mid, "items", "sunblade")["ok"]
    with pytest.raises(modules.ContentNotFound):
        modules.read_content(mid, "items", "sunblade")


def test_content_bad_kind_or_id(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_content(mid, "characters", "x", name="x", body="",
                                      keys="", fields={}, sheet=None)["ok"] is False
    assert module_edit.upsert_content(mid, "items", "../evil", name="x", body="",
                                      keys="", fields={}, sheet=None)["ok"] is False


def _pack_sheets(mid):
    return modules.load_pack(mid)["sheets"]


def test_rename_group_rewrites_refs(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    assert module_edit.set_layout(mid, {"sheet_types": {"warden": {"group": "attributes"}}})["ok"]
    res = module_edit.rename(mid, "group", {"from": "attributes"}, "traits")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    assert "traits" in pack["sheets"]["groups"] and "attributes" not in pack["sheets"]["groups"]
    assert pack["sheets"]["sheet_types"]["warden"]["groups"] == ["traits"]
    assert pack["checks"]["brawl"]["requires"] == ["traits"]
    assert pack["layout"]["sheet_types"]["warden"]["group"] == "traits"
    assert pack["errors"] == [] and pack["display_errors"] == []


def test_rename_field_rewrites_scope_bound(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    # Disjoint second group with the SAME field key, on a different type.
    other_group = {"label": "Spirit", "fields": [
        {"key": "strength", "label": "Will Strength", "type": "dots", "max": 5}],
        "derived": {"spirit_might": "strength * 3"}}
    other_type = {"label": "Medium", "kind": "characters", "groups": ["spirit"],
                  "fields": [], "derived": {}}
    assert module_edit.upsert_group(mid, "spirit", other_group)["ok"]
    assert module_edit.upsert_sheet_type(mid, "medium", other_type)["ok"]
    # NOTE: CHECK's own roll references the derived stat {might}, which never
    # literally contains "strength" (rewriting is textual, not semantic — see
    # test_rename_field_rewrites_scope_bound's assertion on the group's
    # "might" formula for that side of it). Use a local variant whose roll
    # references the renamed field by name, to actually exercise scope-bound
    # placeholder rewriting on an in-scope check.
    assert module_edit.upsert_check(mid, "brawl", {**CHECK, "roll": "1d20 + {strength}"})["ok"]
    spirit_check = {"label": "Channel", "roll": "1d20 + {strength}", "requires": ["spirit"]}
    assert module_edit.upsert_check(mid, "channel", spirit_check)["ok"]
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    g = pack["sheets"]["groups"]
    assert g["traits" if "traits" in g else "attributes"]["fields"][0]["key"] == "brawn"
    assert g["attributes"]["derived"]["might"] == "brawn * 2"
    assert pack["sheets"]["sheet_types"]["warden"]["derived"]["guard"] == "brawn + 1"
    # the OTHER group's same-spelled field and its consumers are untouched
    assert g["spirit"]["fields"][0]["key"] == "strength"
    assert g["spirit"]["derived"]["spirit_might"] == "strength * 3"
    assert pack["checks"]["channel"]["roll"] == "1d20 + {strength}"
    # the in-scope check IS rewritten
    assert pack["checks"]["brawl"]["roll"] == "1d20 + {brawn}"
    assert pack["errors"] == []


def test_rename_field_word_boundary(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    g = {"label": "A", "fields": [
        {"key": "str", "type": "dots", "max": 5},
        {"key": "strength_bonus", "type": "number"}],
        "derived": {"total": "str + strength_bonus"}}
    assert module_edit.upsert_group(mid, "abilities", g)["ok"]
    t = {"label": "Scout", "kind": "characters", "groups": ["abilities"], "fields": []}
    assert module_edit.upsert_sheet_type(mid, "scout", t)["ok"]
    res = module_edit.rename(mid, "field", {"from": "str", "group": "abilities"}, "vigor")
    assert res["ok"], res["errors"]
    d = modules.load_pack(mid)["sheets"]["groups"]["abilities"]["derived"]
    assert d["total"] == "vigor + strength_bonus"   # strength_bonus untouched


def test_rename_resource_rewrites_max_name(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    t2 = {**TYPE, "derived": {"guard": "strength + 1", "reserve": "essence_max - essence"}}
    assert module_edit.upsert_sheet_type(mid, "warden", t2)["ok"]
    res = module_edit.rename(mid, "field", {"from": "essence", "group": "attributes"}, "mana")
    assert res["ok"], res["errors"]
    d = modules.load_pack(mid)["sheets"]["sheet_types"]["warden"]["derived"]
    assert d["reserve"] == "mana_max - mana"


def test_rename_to_reserved_or_collision_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "new")
    assert res["ok"] is False
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "essence")
    assert res["ok"] is False
    # map-key collisions must reject, never overwrite the destination (and
    # the destination definition must survive intact)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    assert module_edit.upsert_check(mid, "melee", {**CHECK, "label": "Melee"})["ok"]
    res = module_edit.rename(mid, "check", {"from": "brawl"}, "melee")
    assert res["ok"] is False
    assert modules.load_pack(mid)["checks"]["melee"]["label"] == "Melee"
    assert module_edit.upsert_group(mid, "spirit", {"label": "Spirit", "fields": []})["ok"]
    res = module_edit.rename(mid, "group", {"from": "spirit"}, "attributes")
    assert res["ok"] is False
    assert modules.load_pack(mid)["sheets"]["groups"]["attributes"]["label"] == "Attributes"


def test_rename_sheet_type_rewrites_flags_layout_sidecars(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_rule(mid, "warden-powers", {"sheet_types": ["warden"]}, "body")["ok"]
    assert module_edit.set_layout(mid, {"sheet_types": {"warden": {"group": "attributes"}}})["ok"]
    res = module_edit.rename(mid, "sheet_type", {"from": "warden"}, "keeper")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    assert "keeper" in pack["sheets"]["sheet_types"]
    doc = next(r for r in pack["rules"] if r["id"] == "warden-powers")
    assert doc["sheet_types"] == ["keeper"]
    assert "keeper" in pack["layout"]["sheet_types"]


def test_rename_traversal_and_file_collision_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    victim = modules.create_module("Victim System")
    before = (modules.user_dir() / victim / "module.md").read_bytes()
    res = module_edit.rename(mid, "rule",
                             {"from": f"../../{victim}/module"}, "stolen")
    assert res["ok"] is False
    assert (modules.user_dir() / victim / "module.md").read_bytes() == before
    assert module_edit.upsert_rule(mid, "a-doc", {}, "a")["ok"]
    assert module_edit.upsert_rule(mid, "b-doc", {}, "b")["ok"]
    res = module_edit.rename(mid, "rule", {"from": "a-doc"}, "b-doc")
    assert res["ok"] is False                          # file collision
    assert modules.read_rule(mid, "b-doc")["body"].strip() == "b"


def test_rename_rule_and_check(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_rule(mid, "combat", {}, "body")["ok"]
    assert module_edit.upsert_check(mid, "brawl", {**CHECK, "rules": ["combat"]})["ok"]
    assert module_edit.rename(mid, "rule", {"from": "combat"}, "combat-core")["ok"]
    pack = modules.load_pack(mid)
    assert pack["checks"]["brawl"]["rules"] == ["combat-core"]
    assert modules.read_rule(mid, "combat-core") is not None
    assert module_edit.rename(mid, "check", {"from": "brawl"}, "melee")["ok"]
    assert "melee" in modules.load_pack(mid)["checks"]


def _bound_campaign(mid):
    """Shared by Tasks 6-8: a world + campaign bound to the module."""
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    modules.set_campaign_module(cid, mid)
    return wid, cid


def test_check_rename_blocked_by_live_proposal(monkeypatch, tmp_path):
    from grimoire.store import proposals
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    wid, cid = _bound_campaign(mid)
    proposals.new(cid, "s1", {"check": "brawl"})
    res = module_edit.rename(mid, "check", {"from": "brawl"}, "melee")
    assert res["ok"] is False and any(cid in e for e in res["errors"])
    res = module_edit.delete_check(mid, "brawl",
                                   pre_swap=module_edit.check_proposal_guard(mid, "brawl"))
    assert res["ok"] is False
    proposals.supersede(cid, "s1")   # superseded is terminal — guard clears
    assert module_edit.rename(mid, "check", {"from": "brawl"}, "melee")["ok"]


def test_shared_fragment_specialized(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    other_group = {"label": "Spirit", "fields": [
        {"key": "strength", "type": "dots", "max": 5}]}
    other_type = {"label": "Medium", "kind": "characters", "groups": ["spirit"], "fields": []}
    assert module_edit.upsert_group(mid, "spirit", other_group)["ok"]
    assert module_edit.upsert_sheet_type(mid, "medium", other_type)["ok"]
    layout = {"fragments": {"stat-block": {"fields": ["strength"]}},
              "sheet_types": {"warden": {"use": "stat-block"},
                              "medium": {"use": "stat-block"}}}
    assert module_edit.set_layout(mid, layout)["ok"]
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    raw = json.loads((modules.user_dir() / mid / "layout.json").read_text(encoding="utf-8"))
    # medium still uses the original fragment; warden repointed to a clone
    assert raw["fragments"]["stat-block"] == {"fields": ["strength"]}
    warden_use = raw["sheet_types"]["warden"]["use"]
    assert warden_use != "stat-block"
    assert raw["fragments"][warden_use] == {"fields": ["brawn"]}
    assert pack["display_errors"] == []


def _write_campaign_sheet(cid, kind, eid, sheet_type, fields):
    p = campaigns.campaign_root(cid) / "sheets" / f"{kind}--{eid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sheet_type": sheet_type, "fields": fields,
                             "gen": "g1"}), encoding="utf-8")
    return p


def test_field_rename_migrates_sheets(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    wp = worlds.world_root(wid) / "sheets" / mid / "characters--winifred.json"
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(json.dumps({"sheet_type": "warden", "fields": {"strength": 2},
                              "gen": "g0"}), encoding="utf-8")
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"], res["errors"]
    assert res["migration"]["migrated"] == 2 and res["migration"]["skipped"] == []
    cdata = json.loads(cp.read_text(encoding="utf-8"))
    assert cdata["fields"] == {"brawn": 3} and cdata["gen"] != "g1"
    wdata = json.loads(wp.read_text(encoding="utf-8"))
    assert wdata["fields"] == {"brawn": 2} and wdata["gen"] != "g0"


def test_field_rename_skips_other_types_and_unbound(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    other = campaigns.create_campaign("Freeform Nights", wid)   # resolves to None
    op = _write_campaign_sheet(other, "characters", "mara", "warden", {"strength": 5})
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"]
    assert json.loads(op.read_text(encoding="utf-8"))["fields"] == {"strength": 5}


def test_both_keys_collision_rejects(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    _write_campaign_sheet(cid, "characters", "mara", "warden",
                          {"strength": 3, "brawn": 4})   # orphaned removed key
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"] is False
    assert any("mara" in e for e in res["errors"])
    # nothing swapped: schema still has strength
    assert "strength" in json.dumps(modules.load_pack(mid)["sheets"])


def test_sheet_type_rename_migrates(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    res = module_edit.rename(mid, "sheet_type", {"from": "warden"}, "keeper")
    assert res["ok"], res["errors"]
    assert json.loads(cp.read_text(encoding="utf-8"))["sheet_type"] == "keeper"


def test_content_rename_migrates_refs(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    ref_type = {"label": "Adept", "kind": "characters", "groups": [],
                "fields": [{"key": "known", "type": "ref", "ref_kind": "lore"}]}
    assert module_edit.upsert_sheet_type(mid, "adept", ref_type)["ok"]
    assert module_edit.upsert_content(mid, "lore", "old-rite", name="Old Rite",
                                      body="", keys="", fields={}, sheet=None)["ok"]
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "adept",
                               {"known": ["lore:module:old-rite", "lore:kept"]})
    res = module_edit.rename(mid, "content", {"from": "old-rite", "kind": "lore"}, "new-rite")
    assert res["ok"], res["errors"]
    got = json.loads(cp.read_text(encoding="utf-8"))["fields"]["known"]
    assert got == ["lore:module:new-rite", "lore:kept"]


def test_unparseable_sheet_skipped(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    bad = campaigns.campaign_root(cid) / "sheets" / "characters--broken.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"]
    assert any("broken" in s for s in res["migration"]["skipped"])


def test_journaled_migration_replays(monkeypatch, tmp_path):
    """Crash after swap, before migration: recovery finishes it."""
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    # Perform the rename with migration suppressed to simulate the crash,
    # leaving a journal exactly as _apply writes it post-swap.
    # Patched on module_edit.migrate, the module that defines it: _apply and
    # _replay_journal both call it as their own global, so the facade
    # attribute is not the binding they read.
    real = me_migrate._run_migration
    monkeypatch.setattr(me_migrate, "_run_migration",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    monkeypatch.setattr(me_migrate, "_run_migration", real)
    # journal survived; pack already published; sheet not yet migrated
    assert json.loads(cp.read_text(encoding="utf-8"))["fields"] == {"strength": 3}
    module_edit.recover()
    assert json.loads(cp.read_text(encoding="utf-8"))["fields"] == {"brawn": 3}
    module_edit.recover()   # idempotent replay
    assert json.loads(cp.read_text(encoding="utf-8"))["fields"] == {"brawn": 3}


def test_dry_run_impact_counts(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    # deleting the sheet type: its sheet becomes newly invalid
    res = module_edit.delete_sheet_type(mid, "warden", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["sheets_newly_invalid"] == 1
    # renaming a field: migration counted, nothing newly invalid
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"},
                             "brawn", dry_run=True)
    assert res["impact"]["sheets_migrated"] == 1
    assert res["impact"]["sheets_newly_invalid"] == 0
    assert "warden" in res["impact"]["sheet_types"]
    # dry-run wrote nothing
    assert "strength" in json.dumps(modules.load_pack(mid)["sheets"])


def test_dry_run_dangling_refs_counts_sidecars(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    ref_type = {"label": "Adept", "kind": "characters", "groups": [],
                "fields": [{"key": "known", "type": "ref", "ref_kind": "lore"}]}
    lore_type = {"label": "Rite", "kind": "lore", "groups": [],
                 "fields": [{"key": "linked", "type": "ref", "ref_kind": "lore"}]}
    assert module_edit.upsert_sheet_type(mid, "adept", ref_type)["ok"]
    assert module_edit.upsert_sheet_type(mid, "rite", lore_type)["ok"]
    assert module_edit.upsert_content(mid, "lore", "old-rite", name="Old Rite",
                                      body="", keys="", fields={}, sheet=None)["ok"]
    assert module_edit.upsert_content(
        mid, "lore", "linked-rite", name="Linked", body="", keys="", fields={},
        sheet={"sheet_type": "rite", "fields": {"linked": ["lore:module:old-rite"]}})["ok"]
    wid, cid = _bound_campaign(mid)
    _write_campaign_sheet(cid, "characters", "mara", "adept",
                          {"known": ["lore:module:old-rite"]})
    res = module_edit.delete_content(mid, "lore", "old-rite", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["dangling_refs"] == 2    # stored sheet + sidecar


def test_content_rename_impact_reads_staged_sidecars(monkeypatch, tmp_path):
    """P2-2: a content-to-content ref to the item being renamed must not
    count as dangling — the STAGED sidecar (already rewritten to the new id
    by the rename's mutate step) is what impact must scan, not the live one."""
    mid = _mk_schema(monkeypatch, tmp_path)
    lore_type = {"label": "Rite", "kind": "lore", "groups": [],
                 "fields": [{"key": "linked", "type": "ref", "ref_kind": "lore"}]}
    assert module_edit.upsert_sheet_type(mid, "rite", lore_type)["ok"]
    assert module_edit.upsert_content(mid, "lore", "old-rite", name="Old Rite",
                                      body="", keys="", fields={}, sheet=None)["ok"]
    assert module_edit.upsert_content(
        mid, "lore", "linked-rite", name="Linked", body="", keys="", fields={},
        sheet={"sheet_type": "rite", "fields": {"linked": ["lore:module:old-rite"]}})["ok"]
    res = module_edit.rename(mid, "content", {"from": "old-rite", "kind": "lore"},
                             "new-rite", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["dangling_refs"] == 0


def test_delete_content_impact_excludes_own_sidecar(monkeypatch, tmp_path):
    """P2-2: a deleted content entry's OWN sidecar (self-referencing) must
    not count as dangling — the staged copy no longer has that sidecar file
    at all (it was deleted along with the entry)."""
    mid = _mk_schema(monkeypatch, tmp_path)
    lore_type = {"label": "Rite", "kind": "lore", "groups": [],
                 "fields": [{"key": "linked", "type": "ref", "ref_kind": "lore"}]}
    assert module_edit.upsert_sheet_type(mid, "rite", lore_type)["ok"]
    assert module_edit.upsert_content(
        mid, "lore", "self-rite", name="Self", body="", keys="", fields={},
        sheet={"sheet_type": "rite", "fields": {"linked": ["lore:module:self-rite"]}})["ok"]
    res = module_edit.delete_content(mid, "lore", "self-rite", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["dangling_refs"] == 0


def test_delete_sheet_type_impact_carries_affected_types(monkeypatch, tmp_path):
    """P2-3: a plain (non-migration) sheet-type delete must still surface
    the affected type in impact.sheet_types, not leave it empty."""
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.delete_sheet_type(mid, "warden", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["sheet_types"] == ["warden"]


def test_group_edit_impact_carries_composing_sheet_types(monkeypatch, tmp_path):
    """P2-3: a plain (non-migration) group upsert must surface the sheet
    types composing that group in impact.sheet_types."""
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.upsert_group(mid, "attributes", GROUP, dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["sheet_types"] == ["warden"]


def test_dry_run_sample_derived(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.upsert_group(mid, "attributes",
                                   {**GROUP, "derived": {"might": "strength * 2"}},
                                   dry_run=True)
    assert res["ok"]
    sample = res["sample"]["warden"]
    assert sample["derived"]["might"] == 0        # defaults: strength 0


# ---- Task 9: duplicate, export, import, transactional create/delete ----


def test_duplicate_builtin(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    new = module_edit.duplicate_module("d20-basic", "My D20")
    assert new == "my-d20"
    pack = modules.load_pack(new)
    assert pack["source"] == "user" and pack["errors"] == []
    # editable now
    assert module_edit.set_manifest(new, name="My D20", description="", version="",
                                    dice="1d20", notes="")["ok"]


def test_duplicate_carries_chosen_name(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    new = module_edit.duplicate_module("d20-basic", "My D20")
    assert new == "my-d20"
    pack = modules.load_pack(new)
    assert pack["manifest"]["name"] == "My D20"
    assert pack["manifest"]["description"] == \
        "Flat d20 + modifiers against a difficulty class."
    assert pack["manifest"]["dice"] == "1d20"
    manifest = modules.pack_root(new)[0] / "module.md"
    _meta, body = parse_frontmatter(manifest.read_text(encoding="utf-8"))
    assert body.strip() == "Reference module proving the flat-roll shape of the contract."


def test_new_mid_reserves_none_and_dedupes(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert module_edit.new_mid("None") != "none"
    a = modules.create_module("Realm System")
    assert module_edit.new_mid("Realm System") != a


def test_create_module_staged_and_locked_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = module_edit.create_module("Realm System")
    assert modules.load_pack(mid)["errors"] == []
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    done = []
    with locks.campaign_lock(cid):        # an LLM flow is mid-computation
        t = threading.Thread(target=lambda: (module_edit.delete_module(mid),
                                             done.append(1)))
        t.start()
        t.join(timeout=0.3)
        assert not done               # delete waits for the campaign lock
    t.join(timeout=5)
    assert done
    with pytest.raises(modules.ModuleNotFound):
        modules.pack_root(mid)


def test_export_import_round_trip(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    data = module_edit.export_module(mid)
    zpath = tmp_path / "pack.zip"
    zpath.write_bytes(data)
    new = module_edit.import_module(zpath)
    assert new != mid                     # deduped
    assert modules.load_pack(new)["errors"] == []
    assert modules.load_pack(new)["sheets"] == modules.load_pack(mid)["sheets"]


def test_import_rejections(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cases = {
        "traversal": {"pack/module.md": "---\nname: X\n---\n",
                      "pack/../evil.txt": "x"},
        "absolute": {"/abs/module.md": "x"},
        "double-slash": {"pack//module.md": "x"},
        "dot-segment": {"pack/./module.md": "x"},
        "drive": {"C:/pack/module.md": "x"},
        "unc": {"//srv/share/module.md": "x"},
        "two-roots": {"a/module.md": "x", "b/module.md": "y"},
        "invalid-pack": {"pack/module.md": "---\nname: X\n---\n"},  # no sheets.json
        "case-collision": {"pack/module.md": "---\nname: X\n---\n",
                           "pack/Sheets.json": "{}",
                           "pack/sheets.json": "{}"},
        "component-drive": {"pack/C:evil.txt": "x"},
        "nested-component-drive": {"pack/sub/C:/module.md": "x"},
    }
    for label, entries in cases.items():
        zpath = tmp_path / f"{label}.zip"
        zpath.write_bytes(_zip_bytes(entries))
        with pytest.raises(modules.ModuleError):
            module_edit.import_module(zpath)
    assert not any(modules.user_dir().iterdir()) if modules.user_dir().is_dir() else True


def test_check_archive_rejects_component_drive_colon(monkeypatch, tmp_path):
    """`_DRIVE_OR_UNC` only anchors at the raw name's start, so a mid-path
    drive segment like 'pack/C:evil.txt' used to slip past _member_parts —
    Path.joinpath then COLLAPSES onto the drive segment, escaping staging
    before the containment recheck ever ran. Assert _check_archive rejects
    both a bare mid-path colon component and a nested one, and that nothing
    ever reaches disk (staging never created, user_dir untouched)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cases = {
        "component-drive": {"pack/C:evil.txt": "x"},
        "nested-component-drive": {"pack/sub/C:/module.md": "x"},
    }
    for label, entries in cases.items():
        buf = io.BytesIO(_zip_bytes(entries))
        with zipfile.ZipFile(buf) as z, pytest.raises(modules.ModuleError):
            module_edit._check_archive(z)
    assert not any(modules.user_dir().iterdir()) if modules.user_dir().is_dir() else True
    staging = module_edit._staging_root()
    assert not staging.is_dir() or not any(staging.iterdir())


def test_import_wraps_extraction_oserror(monkeypatch, tmp_path):
    """Pathological member names (reserved device names like CON on Windows,
    trailing dots/spaces) can raise a raw OSError from mkdir/write_bytes
    mid-extraction. That must never escape import_module as a bare OSError —
    it should surface as modules.ModuleError (or, if the filesystem happens
    to tolerate the name, the import may simply succeed)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    entries = {
        "pack/module.md": "---\nname: X\n---\n",
        "pack/sheets.json": '{\n  "groups": {},\n  "sheet_types": {}\n}\n',
        "pack/CON": "x",
    }
    zpath = tmp_path / "con.zip"
    zpath.write_bytes(_zip_bytes(entries))
    try:
        module_edit.import_module(zpath)
    except modules.ModuleError:
        pass  # extraction failure surfaced cleanly, not a raw OSError


def test_import_wraps_zip_read_errors(monkeypatch, tmp_path):
    """P2-4: pulling a member out of the archive can raise RuntimeError
    (encrypted member), NotImplementedError (unsupported compression), or
    zipfile.BadZipFile (bad CRC/corrupt data) -- none of those may escape
    import_module as a bare exception; they must surface as modules.ModuleError,
    same as the OSError case above.

    Patches ``ZipFile.open``, not ``ZipFile.read``: extraction streams each
    member through a bounded buffer now (store.ziputil.extract), so a patch on
    ``read`` intercepts nothing and this test would go green while proving
    nothing -- the by-value-import failure mode CLAUDE.md describes, in test
    form. Both the raise-at-open and the raise-mid-stream shapes are covered,
    because a bad CRC only surfaces once bytes are actually pulled.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    entries = {
        "pack/module.md": "---\nname: X\n---\n",
        "pack/sheets.json": '{\n  "groups": {},\n  "sheet_types": {}\n}\n',
    }
    real_open = zipfile.ZipFile.open

    class _BoomStream(io.RawIOBase):
        def __init__(self, exc):
            self._exc = exc

        def readable(self):
            return True

        def readinto(self, _b):
            raise self._exc

    for exc in (RuntimeError("Bad password for file"),
                NotImplementedError("compression type 99"),
                zipfile.BadZipFile("Bad CRC-32")):
        for mid_stream in (False, True):
            zpath = tmp_path / "bad.zip"
            zpath.write_bytes(_zip_bytes(entries))

            def boom(self, name, *a, _exc=exc, _mid=mid_stream, **k):
                target = name.filename if isinstance(name, zipfile.ZipInfo) else str(name)
                if target.endswith("module.md"):
                    if _mid:
                        return _BoomStream(_exc)
                    raise _exc
                return real_open(self, name, *a, **k)
            monkeypatch.setattr(zipfile.ZipFile, "open", boom)
            with pytest.raises(modules.ModuleError):
                module_edit.import_module(zpath)
            monkeypatch.setattr(zipfile.ZipFile, "open", real_open)
            staging = module_edit._staging_root()
            assert not staging.is_dir() or not any(staging.iterdir())


def test_delete_module_rejects_builtin_before_campaign_locks(monkeypatch, tmp_path):
    """delete_module must check source and reject a builtin BEFORE taking
    every campaign lock — not after. Monkeypatch _campaign_locks to blow up
    if it's ever entered, so the test fails loudly if the ordering
    regresses."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    def _boom():
        raise AssertionError("_campaign_locks must not run for a builtin delete")
    # module_edit.packs.delete_module reaches this through the migrate module
    # object, so patching the defining module is what the caller reads --
    # patching the facade attribute would leave delete_module calling the
    # original and this test would assert nothing.
    monkeypatch.setattr(me_migrate, "_campaign_locks", _boom)
    with pytest.raises(modules.ModuleError):
        module_edit.delete_module("d20-basic")


# ---- Task 11: R2 lock spans — proposals lock unification ----


def test_llm_consumers_hold_campaign_lock(monkeypatch, tmp_path):
    """An edit holding the campaign locks excludes context assembly (proxy
    for all R2 consumers — they share the campaign_lock domain)."""
    from grimoire.store import proposals
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    # proposals lock domain is unified: campaign_lock(cid) blocks proposals.new
    hit = []
    with locks.campaign_lock(cid):
        t = threading.Thread(target=lambda: (proposals.new(cid, "s1", {}), hit.append(1)))
        t.start()
        t.join(timeout=0.3)
        assert not hit          # blocked while the campaign lock is held
    t.join(timeout=5)
    assert hit


def test_proposal_derivation_excluded_by_edit_lock(monkeypatch, tmp_path):
    """The whole derive→persist span holds the campaign lock: with the lock
    held by an 'edit', a proposal creation cannot even START deriving."""
    from grimoire.store import proposals
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    wid, cid = _bound_campaign(mid)
    derived_under_lock = []

    def derive_and_persist():
        # mirrors the route's shape: lock, read the pack's check, persist
        with locks.campaign_lock(cid):
            pack = modules.load_pack(mid)
            assert "brawl" in pack["checks"]
            derived_under_lock.append(proposals.new(cid, "s1", {"check": "brawl"}))

    with locks.campaign_lock(cid):
        t = threading.Thread(target=derive_and_persist)
        t.start()
        t.join(timeout=0.3)
        assert not derived_under_lock       # blocked before deriving
        # rename the check while the creator is excluded... would deadlock
        # here (we hold cid's lock) — so just release and let both proceed.
    t.join(timeout=5)
    assert derived_under_lock
    rec = proposals.get(cid, "s1")
    assert rec["payload"]["check"] == "brawl"


def test_proposal_route_sites_locked():
    import inspect

    from grimoire.routes import streaming
    src = inspect.getsource(streaming)
    for line_marker in ("proposals.new(",):
        # every proposals.new call site sits inside a
        # `with store.locks.campaign_lock(` block — enforced by review, smoke-
        # checked here: the file must contain at least one such wrap. The
        # proposal finalizers all live in routes/streaming.py, so scanning that
        # one module (rather than the whole package) keeps the check tight.
        assert line_marker in src
        assert "locks.campaign_lock(" in src


def test_r2_consumers_reference_campaign_lock():
    import inspect

    from grimoire.routes import mechanics
    from grimoire.store import checks as checks_mod
    from grimoire.store import context as context_mod
    assert "campaign_lock" in inspect.getsource(checks_mod.resolve_check)
    assert "campaign_lock" in inspect.getsource(context_mod._mechanics)
    assert "campaign_lock" in inspect.getsource(mechanics._continuation_rule_bodies)
