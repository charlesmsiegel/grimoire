"""Scene-start sheet baselines (mechanics Phase 5, roadmap #826)."""

import json
import threading

import pytest

from grimoire.store import audit, campaigns, modules, scenes, sheets, worlds

SHEETS_DEF = {
    "groups": {},
    "sheet_types": {
        "warrior": {
            "label": "Warrior",
            "kind": "characters",
            "groups": [],
            "fields": [
                {"key": "hp", "label": "Hit Points", "type": "resource", "max": 12},
            ],
        }
    },
}


@pytest.fixture
def user_pack_path(monkeypatch, tmp_path):
    """A module pack that lives in the user library (GRIMOIRE_HOME/modules),
    so tests can mutate sheets.json in place (schema_stamp mtime tests)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Test Pack")
    root = modules.user_dir() / mid
    (root / "sheets.json").write_text(json.dumps(SHEETS_DEF), encoding="utf-8")
    return root


@pytest.fixture
def cid_with_sheet(user_pack_path):
    mid = user_pack_path.name
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}})
    return cid


@pytest.fixture
def plain_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


def test_capture_and_read(cid_with_sheet):
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")          # hook fires here
    data = audit.read_baselines(cid)
    assert sid in data and "characters--mara" in data[sid]["sheets"]
    entry = data[sid]["sheets"]["characters--mara"]
    assert entry["sheet_type"] and entry["gen"] and isinstance(entry["fields"], dict)
    assert data[sid]["module"] and data[sid]["schema"]["hash"]


def test_capture_noop_without_module(plain_campaign):
    scenes.create_scene(plain_campaign, "Landing")
    assert audit.read_baselines(plain_campaign) == {}


def test_baseline_field_validity_matrix(cid_with_sheet):
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is not None
    assert audit.baseline_field(cid, "no-such-scene", "characters", "mara", "hp") is None
    assert audit.baseline_field(cid, sid, "characters", "nobody", "hp") is None
    assert audit.baseline_field(cid, sid, "characters", "mara", "nonesuch") is None
    # gen mismatch: delete + recreate -> report-only. (Branch note: this
    # worktree's sheets.delete/write have no expected/expected_gen CAS params
    # yet -- Tasks 3-5 add those on a different branch -- so this uses the
    # current unconditional signatures.)
    sheets.delete(cid, "characters", "mara")
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}})
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is None


def test_baseline_invalid_after_pack_mtime_change(cid_with_sheet, user_pack_path):
    """A->B->A content reversion: hash restored but mtime moved -> invalid."""
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    p = user_pack_path / "sheets.json"       # the campaign's module lives in the
    original = p.read_text(encoding="utf-8")  # user library (GRIMOIRE_HOME/modules)
    p.write_text(original + " ", encoding="utf-8")   # B
    p.write_text(original, encoding="utf-8")          # back to A, mtime moved
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is None


def test_clear_and_repoint(cid_with_sheet):
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    audit.repoint_scenes(cid, {sid: "renamed"})
    assert "renamed" in audit.read_baselines(cid)
    audit.clear_baselines(cid)
    assert audit.read_baselines(cid) == {}


def test_concurrent_capture_and_repoint_both_land(cid_with_sheet):
    """capture_baseline and repoint_scenes take the baseline lock from
    different call sites (capture via sheet-lock -> baseline-lock;
    repoint_scenes standalone, not under the sheet lock -- scene renames
    aren't sheet mutations) -- that's the real race the baseline lock
    guards against. Racing two captures on the *same* cid (the old version
    of this test) can't exercise it: both captures serialize on the shared
    per-cid sheets.lock_for(cid) before either touches the baseline lock."""
    cid = cid_with_sheet
    s1 = scenes.create_scene(cid, "One")    # both captures already ran via
    s2 = scenes.create_scene(cid, "Two")    # the create_scene hook
    audit.clear_baselines(cid)
    audit.capture_baseline(cid, s1)         # give repoint something to move
    t1 = threading.Thread(target=lambda: audit.capture_baseline(cid, s2))
    t2 = threading.Thread(target=lambda: audit.repoint_scenes(cid, {s1: "renamed"}))
    t1.start(); t2.start(); t1.join(); t2.join()
    data = audit.read_baselines(cid)
    assert "renamed" in data and s2 in data


def test_baseline_entry_valid_survives_deleted_module(cid_with_sheet, monkeypatch):
    """Module deleted (or pack otherwise unreadable) between modules.resolve
    and the baseline check must make the baseline invalid, not raise --
    baseline_entry_valid/baseline_field are report-only and documented as
    never-raising."""
    cid = cid_with_sheet
    sid = scenes.create_scene(cid, "Landing")
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is not None

    def _boom(mid):
        raise modules.ModuleNotFound(mid)

    monkeypatch.setattr(modules, "load_pack", _boom)
    assert audit.baseline_field(cid, sid, "characters", "mara", "hp") is None
    sheet = sheets.read(cid, "characters", "mara")
    mid = "some-mid"
    assert audit.baseline_entry_valid(cid, sid, "characters", "mara", mid, sheet) is False
