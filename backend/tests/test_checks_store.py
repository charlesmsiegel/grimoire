import pytest

from grimoire.store import campaigns, checks, rolls, scenes, sheets, worlds


def _play(monkeypatch, tmp_path, module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=module)
    sid = scenes.create_scene(cid, "Opening")
    return wid, cid, sid


def test_resolve_check_pool(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "brawl": 2, "wits": 2, "occult": 1,
                  "essence": {"current": 5, "max": 10}}, expected=None)
    res = checks.resolve_check(cid, "brawl", "characters:mara", seed=7)
    assert res["notation"] == "5d10 t6"          # (3+2+0)d10, default diff 6
    assert res["difficulty"] == 6 and res["modifier"] == 0
    assert res["tier"] in ("botch", "exceptional success", "success", "failure")
    res2 = checks.resolve_check(cid, "brawl", "characters:mara", seed=7)
    assert res2["result"] == res["result"]        # seeded determinism
    assert rolls.read(cid) == []                  # PURE: no log writes


def test_resolve_check_difficulty_ladder_and_modifier(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", {"vigor": 3, "brawl": 1}, expected=None)
    res = checks.resolve_check(cid, "brawl", "characters:mara",
                               difficulty=8, modifier=2, seed=1)
    assert res["notation"] == "6d10 t8"


def test_resolve_check_d20_tiers(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path, module="d20-basic")
    sheets.write(cid, "characters", "mara", "warrior",
                 {"strength": 14, "athletics": 3}, expected=None)
    # scan seeds for a natural 20 and a natural 1 to prove tier evaluation
    tiers = set()
    for seed in range(200):
        res = checks.resolve_check(cid, "athletics", "characters:mara", seed=seed)
        tiers.add(res["tier"])
    assert {"critical success", "critical failure", "success", "failure"} <= tiers


def test_resolve_check_errors(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid, "ghost", "characters:mara")        # unknown check
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid, "brawl", "characters:mara")        # no sheet
    sheets.write(cid, "items", "moon-disc", "talisman", None, expected=None)
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid, "brawl", "items:moon-disc")        # requires gating
    cid2 = campaigns.create_campaign("Freeform", worlds.create_world("R2"))
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid2, "brawl", "characters:mara")       # no module


def test_roll_scope_shapes():
    pool = {"dice": [{"value": 8, "rolls": [8], "kept": True},
                     {"value": 1, "rolls": [1], "kept": True}],
            "total": None, "successes": 1, "vs": None, "modifier": 0}
    s = checks.roll_scope(pool)
    assert s["successes"] == 1 and s["ones"] == 1 and s["dice"] == 2
    assert "margin" not in s and "total" not in s
    flat = {"dice": [{"value": 17, "rolls": [17], "kept": True}],
            "total": 20, "successes": None, "vs": 15, "modifier": 3}
    s = checks.roll_scope(flat)
    assert s["natural"] == 17 and s["margin"] == 5 and s["total"] == 20


def test_evaluate_tier_first_match_and_skip(monkeypatch, tmp_path):
    tiers = [{"label": "crit", "when": "natural == 20"},
             {"label": "ok", "when": "margin >= 0"}]
    label, warns = checks.evaluate_tier({"outcomes": tiers}, {}, {"natural": 20, "margin": 5})
    assert label == "crit"
    label, warns = checks.evaluate_tier({"outcomes": tiers}, {}, {"margin": 1})
    assert label == "ok" and warns == []          # first tier skipped (no `natural`), no warning
    # NOTE: a name outside the roll-scope vocabulary raises -> skip + warning:
    label, warns = checks.evaluate_tier({"outcomes": [{"label": "x", "when": "ghost > 1"}]},
                                        {}, {"total": 3})
    assert label is None and warns


def test_evaluate_tier_defaults_fallback_and_check_level_shadows(monkeypatch, tmp_path):
    # check-level ladder present -> defaults ladder is never consulted, even if
    # the check-level ladder matches nothing.
    check_def = {"outcomes": [{"label": "only", "when": "total >= 100"}]}
    defaults = {"outcomes": [{"label": "fallback", "when": "total >= 0"}]}
    label, warns = checks.evaluate_tier(check_def, defaults, {"total": 1})
    assert label is None
    # no check-level ladder -> defaults ladder used
    label, warns = checks.evaluate_tier({}, defaults, {"total": 1})
    assert label == "fallback"


def test_resolve_check_never_appeared_actor(monkeypatch, tmp_path):
    # A real, sheeted character that has never appeared in any scene: no
    # locked version exists, so the label path must not crash on vid=None
    # (regression: TypeError via characters.read_card(croot, eid, None)).
    _, cid, _ = _play(monkeypatch, tmp_path)
    from grimoire.store import characters
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    eid, _vid = characters.create_character(wroot, "Mara")
    sheets.write(cid, "characters", eid, "medium", {"vigor": 3, "brawl": 2}, expected=None)
    res = checks.resolve_check(cid, "brawl", f"characters:{eid}", seed=7)
    assert res["actor_label"] == "Mara"       # container meta name, no version needed
    # campaign-local copy, still never appeared (the reviewer's TypeError repro:
    # read_card(croot, eid, None) passes _require_char, then hits the vid guard --
    # which took a non-string and crashed before it rejected non-strings)
    croot = campaigns.campaign_root(cid)
    eid2, _ = characters.create_character(croot, "Seraphine")
    sheets.write(cid, "characters", eid2, "medium", {"vigor": 2, "brawl": 1}, expected=None)
    res = checks.resolve_check(cid, "brawl", f"characters:{eid2}", seed=7)
    assert res["actor_label"] == "Seraphine"
    # and a sheeted id with no character record at all falls back to the id
    sheets.write(cid, "characters", "winifred", "medium", {"vigor": 1, "brawl": 1}, expected=None)
    res = checks.resolve_check(cid, "brawl", "characters:winifred", seed=7)
    assert res["actor_label"] == "winifred"


def test_rolls_proposal_tag(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    from grimoire.store import dice
    entry = rolls.append(cid, "s1", "test", dice.roll("1d6", seed=1), proposal="pr-000001")
    assert entry["proposal"] == "pr-000001"
    assert rolls.find_by_proposal(cid, "pr-000001")["id"] == entry["id"]
    assert rolls.find_by_proposal(cid, "pr-999999") is None
