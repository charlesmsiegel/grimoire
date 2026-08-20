"""User pins and excludes (#129): the primitive the packer is not allowed to argue with.

Two properties carry the whole feature, and both are about *time*: a rule the
reader set has to outlive the pressure that would otherwise drop what it names,
and a rule with a TTL has to stop existing on its own. Everything else here is
the ordinary shape of a campaign-scoped JSON store.
"""

import json
import threading

import pytest

from grimoire.store import campaigns, pins, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("Realm"))


def _path(cid):
    return campaigns.campaign_root(cid) / "pins.json"


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert pins.read(cid) == {}
    assert pins.records(cid, "0001", 0) == []
    assert pins.active(cid, "0001", 0) == {"pinned": frozenset(), "excluded": frozenset()}


def test_set_rule_stores_a_scene_pin(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rec = pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=3, posts=10)
    assert rec["ref"] == "lore:tide-oath"
    assert rec["mode"] == "pin"
    assert rec["scope"] == "scene"
    assert rec["sid"] == "0001"
    assert rec["ttl_posts"] == 3
    assert rec["remaining"] == 3
    assert rec["created"]
    assert json.loads(_path(cid).read_text(encoding="utf-8"))["0001:lore:tide-oath"]["mode"] == "pin"


def test_a_pin_and_an_exclude_reach_the_assembler_as_two_sets(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")
    pins.set_rule(cid, "characters:mara", pins.EXCLUDE, sid="0001")
    assert pins.active(cid, "0001", 0) == {"pinned": frozenset({"lore:tide-oath"}),
                                           "excluded": frozenset({"characters:mara"})}


def test_a_scene_rule_is_invisible_to_other_scenes(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")
    assert pins.active(cid, "0002", 0)["pinned"] == frozenset()


def test_a_campaign_rule_reaches_every_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, scope=pins.CAMPAIGN)
    for sid in ("0001", "0002"):
        assert pins.active(cid, sid, 99)["excluded"] == frozenset({"lore:tide-oath"})


def test_a_scene_rule_overrides_the_campaign_default_for_that_ref(monkeypatch, tmp_path):
    """The more specific rule wins: "never in this campaign, except here"."""
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, scope=pins.CAMPAIGN)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")
    here = pins.active(cid, "0001", 0)
    assert here["pinned"] == frozenset({"lore:tide-oath"})
    assert here["excluded"] == frozenset()
    assert pins.active(cid, "0002", 0)["excluded"] == frozenset({"lore:tide-oath"})


def test_setting_the_same_target_twice_replaces_rather_than_stacks(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=2, posts=0)
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, sid="0001")
    assert len(pins.read(cid)) == 1
    assert pins.active(cid, "0001", 0) == {"pinned": frozenset(),
                                           "excluded": frozenset({"lore:tide-oath"})}


def test_re_pinning_restarts_the_clock(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=2, posts=0)
    assert pins.active(cid, "0001", 2)["pinned"] == frozenset()   # exhausted
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=2, posts=2)
    assert pins.active(cid, "0001", 3)["pinned"] == frozenset({"lore:tide-oath"})


# --- TTL decay --------------------------------------------------------------

def test_a_ttl_pin_survives_its_window_and_then_stops(monkeypatch, tmp_path):
    """Posts, not wall-clock: the window is measured in transcript growth."""
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=3, posts=10)
    for posts in (10, 11, 12):
        assert pins.active(cid, "0001", posts)["pinned"] == frozenset({"lore:tide-oath"})
    assert pins.active(cid, "0001", 13)["pinned"] == frozenset()


def test_no_ttl_means_no_decay(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=0, posts=0)
    assert pins.active(cid, "0001", 5000)["pinned"] == frozenset({"lore:tide-oath"})
    assert pins.records(cid, "0001", 5000)[0]["remaining"] is None


def test_an_expired_scene_rule_yields_to_the_campaign_default(monkeypatch, tmp_path):
    """Expired means *gone*, not merely inert — so the standing rule applies again."""
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, scope=pins.CAMPAIGN)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=1, posts=0)
    assert pins.active(cid, "0001", 0)["pinned"] == frozenset({"lore:tide-oath"})
    assert pins.active(cid, "0001", 1)["excluded"] == frozenset({"lore:tide-oath"})


def test_records_reports_the_countdown_and_hides_the_dead(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=4, posts=2)
    pins.set_rule(cid, "characters:mara", pins.EXCLUDE, sid="0001", ttl_posts=2, posts=2)
    assert [(r["ref"], r["remaining"]) for r in pins.records(cid, "0001", 3)] == [
        ("characters:mara", 1), ("lore:tide-oath", 3)]
    assert [r["ref"] for r in pins.records(cid, "0001", 4)] == ["lore:tide-oath"]


def test_records_puts_this_scene_ahead_of_the_campaign_standing_rules(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:always", pins.EXCLUDE, scope=pins.CAMPAIGN)
    pins.set_rule(cid, "characters:mara", pins.PIN, sid="0001")
    assert [(r["ref"], r["scope"]) for r in pins.records(cid, "0001", 0)] == [
        ("characters:mara", "scene"), ("lore:always", "campaign")]


def test_a_spent_rule_is_swept_when_the_next_one_lands(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=1, posts=0)
    pins.set_rule(cid, "characters:mara", pins.PIN, sid="0001", posts=9)
    assert list(pins.read(cid)) == ["0001:characters:mara"]


# --- refusals ---------------------------------------------------------------

def test_an_unknown_mode_or_scope_is_refused(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        pins.set_rule(cid, "lore:tide-oath", "maybe", sid="0001")
    with pytest.raises(ValueError):
        pins.set_rule(cid, "lore:tide-oath", pins.PIN, scope="world", sid="0001")


def test_a_campaign_rule_cannot_carry_a_post_ttl(monkeypatch, tmp_path):
    """A TTL counts posts, and a campaign-wide rule has no scene to count them in."""
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        pins.set_rule(cid, "lore:tide-oath", pins.PIN, scope=pins.CAMPAIGN, ttl_posts=5)


def test_a_scene_rule_needs_a_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="")


def test_a_ref_is_stored_in_the_form_the_assembler_compares_against(monkeypatch, tmp_path):
    """`split_ref` tolerates the whitespace a pasted ref arrives with, so storing
    the ref as typed filed a rule that validated, listed, and matched nothing."""
    cid = _campaign(monkeypatch, tmp_path)
    rec = pins.set_rule(cid, "lore:  tide-oath  ", pins.PIN, sid="0001")
    assert rec["ref"] == "lore:tide-oath"
    assert pins.active(cid, "0001", 0)["pinned"] == frozenset({"lore:tide-oath"})
    assert pins.remove(cid, "lore:tide-oath", sid="0001") is True


def test_an_unpinnable_kind_is_refused(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        pins.set_rule(cid, "recipes:soup", pins.PIN, sid="0001")
    with pytest.raises(ValueError):
        pins.set_rule(cid, "tide-oath", pins.PIN, sid="0001")


# --- removal, renames, deletions --------------------------------------------

def test_remove_takes_one_rule_and_reports_whether_it_was_there(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")
    assert pins.remove(cid, "lore:tide-oath", sid="0001") is True
    assert pins.remove(cid, "lore:tide-oath", sid="0001") is False
    assert pins.active(cid, "0001", 0)["pinned"] == frozenset()


def test_remove_is_scope_precise(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, scope=pins.CAMPAIGN)
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, sid="0001")
    assert pins.remove(cid, "lore:tide-oath", sid="0001") is True
    assert pins.active(cid, "0001", 0)["pinned"] == frozenset({"lore:tide-oath"})


def test_a_renamed_scene_keeps_its_rules(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001", ttl_posts=2, posts=1)
    pins.repoint_scenes(cid, {"0001": "0007"})
    assert pins.active(cid, "0007", 2)["pinned"] == frozenset({"lore:tide-oath"})
    assert pins.records(cid, "0007", 2)[0]["sid"] == "0007"


def test_a_deleted_scene_takes_its_rules_with_it(monkeypatch, tmp_path):
    """Scene ids are recycled, so a rule left behind would be adopted by the
    next scene to take the number."""
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, scope=pins.CAMPAIGN)
    pins.drop_scene(cid, "0001")
    assert list(pins.read(cid)) == ["*:lore:tide-oath"]


# --- a hand-edited file -----------------------------------------------------

def test_a_garbled_record_is_stepped_over_not_inherited(monkeypatch, tmp_path):
    """pins.json is hand-editable and read by a bare json.loads. One bad row
    must not take the reader's other rules down with it."""
    cid = _campaign(monkeypatch, tmp_path)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")
    data = pins.read(cid)
    data["0001:lore:junk"] = ["not", "a", "record"]
    data["0001:lore:half"] = {"mode": {"deep": "wrong"}, "scope": "scene", "sid": "0001"}
    _path(cid).write_text(json.dumps(data), encoding="utf-8")
    assert pins.active(cid, "0001", 0)["pinned"] == frozenset({"lore:tide-oath"})
    assert [r["ref"] for r in pins.records(cid, "0001", 0)] == ["lore:tide-oath"]


def test_an_unparseable_file_does_not_take_the_prompt_down(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _path(cid).write_text("{ not json", encoding="utf-8")
    assert pins.active(cid, "0001", 0) == {"pinned": frozenset(), "excluded": frozenset()}
    assert pins.records(cid, "0001", 0) == []


def test_a_file_of_the_wrong_shape_is_refused_by_the_writers(monkeypatch, tmp_path):
    """Substituting {} would publish an empty rule set over whatever was there."""
    cid = _campaign(monkeypatch, tmp_path)
    _path(cid).write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid="0001")


def test_concurrent_writes_do_not_lose_one(monkeypatch, tmp_path):
    """The file is rewritten whole, so the mutators serialize on the campaign lock."""
    cid = _campaign(monkeypatch, tmp_path)
    refs = [f"lore:entry-{n}" for n in range(12)]
    threads = [threading.Thread(target=pins.set_rule, args=(cid, ref, pins.PIN),
                                kwargs={"sid": "0001"}) for ref in refs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert pins.active(cid, "0001", 0)["pinned"] == frozenset(refs)
