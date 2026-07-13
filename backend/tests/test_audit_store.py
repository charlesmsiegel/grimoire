"""Scene-start sheet baselines (mechanics Phase 5, roadmap #826)."""

import threading

import pytest

from grimoire.store import (audit, appearances, campaigns, characters, dice,
                            modules, rolls, scenes, sheets, worlds)


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
    # gen mismatch: delete + recreate -> report-only
    g = sheets.read(cid, "characters", "mara")["gen"]
    sheets.delete(cid, "characters", "mara", expected_gen=g)
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}}, expected=None)
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


# ---- Part 2: audit prompt / parse / materialize (Task 7) ----


def test_parse_output_fail_closed():
    for bad in ("no json here", "{}", '{"warnings": null, "sheet_deltas": []}',
                '{"warnings": [], "sheet_deltas": {}}', '{"warnings": []}'):
        with pytest.raises(audit.AuditParseError):
            audit.parse_output(bad)


def test_parse_output_item_tolerance():
    out = audit.parse_output(
        '{"warnings": ["w1", 42], "sheet_deltas": '
        '[{"id": "characters:mara", "field": "hp", "value": {"current": 3}}, "junk"]}')
    assert out["warnings"] == ["w1"]
    assert len(out["sheet_deltas"]) == 1
    assert len(out["dropped"]) == 2          # the 42 and the "junk"


def test_render_value():
    assert audit.render_value({"key": "hp", "type": "resource"},
                              {"current": 6, "max": 10}) == "hp 6/10"
    assert audit.render_value({"key": "wounds", "type": "track"}, 3) == "wounds 3"
    assert audit.render_value({"key": "conditions", "type": "list"},
                              ["prone", "dazed"]) == "conditions:\n- prone\n- dazed"
    assert audit.render_value({"key": "conditions", "type": "list"}, []) == "conditions: (empty)"
    assert audit.render_value({"key": "notes", "type": "text"}, "quiet") == "notes quiet"


def test_sheet_scope_includes_present_cast(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    scope = audit.sheet_scope(cid, sid)
    assert ("characters", "mara", "Mara") in scope


def test_roll_lines_filters_by_scene(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    other_sid = scenes.create_scene(cid, "Elsewhere")
    rolls.append(cid, sid, "Attack", dice.roll("1d20", seed=1))
    rolls.append(cid, other_sid, "Defense", dice.roll("1d20", seed=1))
    lines = audit.roll_lines(cid, sid)
    assert len(lines) == 1 and "Attack" in lines[0] and "1d20" in lines[0]


def test_roll_lines_renders_tier(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    rolls.append(cid, sid, "Brawl", dice.roll("1d20", seed=1), tier="success")
    lines = audit.roll_lines(cid, sid)
    assert len(lines) == 1 and "success" in lines[0]


def test_roll_lines_omits_tier_when_absent(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    rolls.append(cid, sid, "Brawl", dice.roll("1d20", seed=1))
    lines = audit.roll_lines(cid, sid)
    assert len(lines) == 1 and "1d20" in lines[0]


def test_build_prompt_wires_system_and_user(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    blocks, _ = audit.sheet_blocks(cid, sid)
    line_log = audit.roll_lines(cid, sid)
    msgs = audit.build_prompt("Something happened.", blocks, line_log)
    assert msgs[0]["role"] == "system" and "JSON object" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "Something happened." in msgs[1]["content"]
    assert "characters:mara" in msgs[1]["content"]


def test_materialize_happy_resource(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast       # fixture: scene + present mara + baseline
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "hp",
         "value": {"current": live["current"] - 2}, "note": "took a hit"}]}
    edits, dropped = audit.materialize(cid, sid, parsed)
    assert dropped == [] and len(edits) == 1
    e = edits[0]
    assert e["kind"] == "sheet" and e["id"] == "sheet:characters:mara:hp"
    assert e["payload"]["expect"] == live
    assert e["payload"]["value"] == {"current": live["current"] - 2, "max": live["max"]}
    assert e["before"].startswith("hp ") and e["after"].startswith("hp ")
    # before/after are rendered from payload.expect/payload.value, not restated
    assert e["before"] == audit.render_value({"key": "hp", "type": "resource"}, e["payload"]["expect"])
    assert e["after"] == audit.render_value({"key": "hp", "type": "resource"}, e["payload"]["value"])


def test_materialize_track_roundtrip(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "wounds", "value": 2, "note": "took a wound"}]}
    edits, dropped = audit.materialize(cid, sid, parsed)
    assert dropped == [] and len(edits) == 1
    e = edits[0]
    assert e["id"] == "sheet:characters:mara:wounds"
    assert e["payload"]["expect"] == 0 and e["payload"]["value"] == 2
    assert e["before"] == "wounds 0" and e["after"] == "wounds 2"


def test_materialize_list_roundtrip(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "conditions",
         "value": ["prone"], "note": "knocked down"}]}
    edits, dropped = audit.materialize(cid, sid, parsed)
    assert dropped == [] and len(edits) == 1
    e = edits[0]
    assert e["id"] == "sheet:characters:mara:conditions"
    assert e["payload"]["expect"] == [] and e["payload"]["value"] == ["prone"]
    assert e["before"] == "conditions: (empty)" and e["after"] == "conditions:\n- prone"


def test_materialize_xp_pool_award(scene_with_sheeted_cast):
    """XP is an ordinary resource field -- no special-cased delta shape."""
    cid, sid = scene_with_sheeted_cast
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "xp",
         "value": {"current": 3}, "note": "closed a plot thread"}]}
    edits, dropped = audit.materialize(cid, sid, parsed)
    assert dropped == [] and len(edits) == 1
    e = edits[0]
    assert e["id"] == "sheet:characters:mara:xp"
    assert e["payload"]["value"] == {"current": 3, "max": 999}


def test_materialize_resource_max_tamper_ignored(scene_with_sheeted_cast):
    """A proposed 'max' is ignored -- canonical value always adopts the LIVE max."""
    cid, sid = scene_with_sheeted_cast
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "hp",
         "value": {"current": 5, "max": 999}, "note": "tamper attempt"}]}
    edits, dropped = audit.materialize(cid, sid, parsed)
    assert dropped == [] and len(edits) == 1
    assert edits[0]["payload"]["value"] == {"current": 5, "max": 12}


def test_materialize_gates(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    cases = [
        ({"id": "characters:nobody", "field": "hp", "value": {"current": 1}}, "unknown"),
        ({"id": "characters:mara", "field": "athletics", "value": 3}, "static"),
        ({"id": "characters:mara", "field": "nonesuch", "value": 1}, "unknown field"),
        ({"id": "characters:mara", "field": "hp", "value": {"current": "lots"}}, "bad value"),
    ]
    for delta, _why in cases:
        edits, dropped = audit.materialize(
            cid, sid, {"warnings": [], "dropped": [], "sheet_deltas": [dict(delta, note="")]})
        assert edits == [] and len(dropped) == 1


def test_materialize_gates_out_of_scope_entity(scene_with_sheeted_cast, user_pack_path):
    """A sheeted, existing entity that simply never appeared in THIS scene is
    dropped the same as an unknown one -- scope, not existence, is the gate."""
    cid, sid = scene_with_sheeted_cast
    characters.create_character(worlds.world_root(
        campaigns.read_campaign(cid)["meta"]["world"]), "Bystander", "default",
        characters.blank_card("Bystander"))
    sheets.write(cid, "characters", "bystander", "warrior",
                 {"hp": {"current": 12, "max": 12}}, expected=None)
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:bystander", "field": "hp",
                          "value": {"current": 5}, "note": ""}]})
    assert edits == []
    assert dropped and "scope" in dropped[0]["reason"]


def test_materialize_gates_unsheeted_entity_in_scope(scene_with_sheeted_cast):
    """Present in the scene but never given a sheet at all -- dropped, not a crash."""
    cid, sid = scene_with_sheeted_cast
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    characters.create_character(worlds.world_root(wid), "Extra", "default",
                                characters.blank_card("Extra"))
    appearances.appear(cid, sid, "characters", "extra", "default", "npc")
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:extra", "field": "hp",
                          "value": {"current": 5}, "note": ""}]})
    assert edits == []
    assert dropped and "no readable sheet" in dropped[0]["reason"]


def test_materialize_gates_invalid_sheet(scene_with_sheeted_cast):
    """A present, sheeted entity whose sheet file is corrupt is dropped, not crashed on."""
    cid, sid = scene_with_sheeted_cast
    p = sheets._campaign_path(cid, "characters", "mara")
    p.write_text("{not json", encoding="utf-8")
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": 5}, "note": ""}]})
    assert edits == []
    assert dropped and "no readable sheet" in dropped[0]["reason"]


def test_materialize_noop_dropped_silently(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"]}, "note": ""}]})
    assert edits == [] and dropped == []      # agreement, not loss


def test_materialize_suppresses_baseline_less_entity(scene_with_sheeted_cast):
    """THE regression: no valid baseline -> zero StagedEdits, whatever the model says."""
    cid, sid = scene_with_sheeted_cast
    g = sheets.read(cid, "characters", "mara")["gen"]
    sheets.delete(cid, "characters", "mara", expected_gen=g)
    sheets.write(cid, "characters", "mara", "warrior", None, expected=None)  # new gen
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, dropped = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 1}, "note": ""}]})
    assert edits == [] and dropped and "baseline" in dropped[0]["reason"]


def test_sheet_blocks_marks_and_excludes(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    blocks, excluded = audit.sheet_blocks(cid, sid)
    assert any("characters:mara" in b for b in blocks)
    assert any("start" in b or "->" in b for b in blocks)   # start -> current markers
    mara_block = next(b for b in blocks if "characters:mara" in b)
    # FULL blocks: text/number fields are present and marked static, never delta-eligible
    assert "[static]" in mara_block
    assert any(line.strip().startswith("notes") and "[static]" in line
              for line in mara_block.splitlines())
    assert any(line.strip().startswith("athletics") and "[static]" in line
              for line in mara_block.splitlines())
    assert excluded == []
    # corrupt the sheet -> excluded, not silently missing
    p = sheets._campaign_path(cid, "characters", "mara")
    p.write_text("{not json", encoding="utf-8")
    blocks, excluded = audit.sheet_blocks(cid, sid)
    assert all("characters:mara" not in b for b in blocks)
    assert excluded and excluded[0]["id"] == "characters:mara"


def test_sheet_blocks_skips_unsheeted_scope_entries(scene_with_sheeted_cast):
    """Present but never sheeted -- silently absent from both blocks and
    excluded (sheet_scope's docstring: 'unsheeted entries included; callers
    filter'); sheet_blocks is that filter, and it drops them quietly rather
    than reporting a spurious exclusion."""
    cid, sid = scene_with_sheeted_cast
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    characters.create_character(worlds.world_root(wid), "Extra", "default",
                                characters.blank_card("Extra"))
    appearances.appear(cid, sid, "characters", "extra", "default", "npc")
    blocks, excluded = audit.sheet_blocks(cid, sid)
    assert all("characters:extra" not in b for b in blocks)
    assert all(e["id"] != "characters:extra" for e in excluded)


# ---- apply_delta (Task 8) ----


def test_apply_delta_happy_and_conflict(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    parsed = {"warnings": [], "dropped": [], "sheet_deltas": [
        {"id": "characters:mara", "field": "hp",
         "value": {"current": live["current"] - 2}, "note": ""}]}
    edits, _ = audit.materialize(cid, sid, parsed)
    audit.apply_delta(cid, sid, edits[0])
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"]["current"] == live["current"] - 2
    with pytest.raises(sheets.SheetConflict):      # double-apply reports
        audit.apply_delta(cid, sid, edits[0])


def test_apply_delta_rejects_out_of_scope_and_baseline_less(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    characters.create_character(worlds.world_root(wid), "Winifred", "default",
                                characters.blank_card("Winifred"))
    sheets.write(cid, "characters", "winifred", "warrior",
                 {"hp": {"current": 12, "max": 12}, "xp": {"current": 0, "max": 999},
                  "wounds": 0, "conditions": []}, expected=None)
    # forge an edit for a sheeted entity that is NOT in the scene (winifred
    # exists + sheeted but never appeared) with a CORRECT expect:
    live = sheets.read(cid, "characters", "winifred")["fields"]["hp"]
    forged = {"id": "sheet:characters:winifred:hp", "kind": "sheet",
              "target": {"kind": "characters", "id": "winifred"}, "field": "hp",
              "label": "x", "before": "", "after": "", "authored": False,
              "payload": {"field": "hp", "value": {"current": 1, "max": live["max"]},
                          "expect": live, "note": ""}}
    with pytest.raises(sheets.SheetError):
        audit.apply_delta(cid, sid, forged)
    assert sheets.read(cid, "characters", "winifred")["fields"]["hp"] == live


def test_apply_delta_vs_delete_recreate_race(scene_with_sheeted_cast):
    """gen re-check inside the lock: recreated sheet is untouched."""
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 2}, "note": ""}]})
    g = sheets.read(cid, "characters", "mara")["gen"]
    sheets.delete(cid, "characters", "mara", expected_gen=g)
    sheets.write(cid, "characters", "mara", "adventurer", None, expected=None)
    fresh = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    with pytest.raises(sheets.SheetError):
        audit.apply_delta(cid, sid, edits[0])
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"] == fresh
