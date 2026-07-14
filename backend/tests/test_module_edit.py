import json
import threading

import pytest

from grimoire.store import campaigns, module_edit, modules, sheets, worlds


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

    with sheets.lock_for(cid):
        t = threading.Thread(target=edit)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive()          # edit is waiting on the campaign lock
        order.append("lock-released")
    t.join(timeout=5)
    assert not t.is_alive()
    assert order == ["lock-released", "edit-done"]


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
