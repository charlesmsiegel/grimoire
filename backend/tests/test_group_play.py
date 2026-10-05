"""Group play's settings and speaker planner (`store/group_play.py`) are pure."""

from __future__ import annotations

import random

import pytest

from grimoire.store import group_play

R = [{"ref": "characters:mara", "name": "Mara"}, {"ref": "characters:winifred", "name": "Winifred"},
     {"ref": "characters:seraphine", "name": "Seraphine Vale"}]


def test_parse_is_lenient_per_key():
    s = group_play.parse('{"order":"bogus","auto_rounds":9,"talkativeness":{"characters:mara":150,"characters:winifred":20}}')
    assert s == {"order": "directed", "order_list": [], "sitting_out": [], "auto_rounds": 0,
                 "talkativeness": {"characters:winifred": 20}}
    assert group_play.parse("{not json") == group_play.parse("")


def test_parse_drops_non_int_and_bad_shapes():
    s = group_play.parse('{"order":"list","order_list":["a","a",3,""],"sitting_out":"x",'
                         '"auto_rounds":true,"talkativeness":{"a":true,"b":1.5,"c":"7","d":0}}')
    assert s == {"order": "list", "order_list": ["a"], "sitting_out": [], "auto_rounds": 0,
                 "talkativeness": {"d": 0}}
    assert group_play.parse("[1]") == group_play.parse("")


def test_settings_of_reads_the_meta_key():
    assert group_play.settings_of({}) == group_play.parse("")
    meta = {"group_play": group_play.dump({**group_play.parse(""), "order": "natural"})}
    assert group_play.settings_of(meta)["order"] == "natural"


def test_dump_is_canonical_and_round_trips():
    s = {**group_play.parse(""), "order": "list", "order_list": ["characters:mara"], "auto_rounds": 3}
    out = group_play.dump(s)
    assert " " not in out
    assert out == group_play.dump(group_play.parse(out))
    assert group_play.parse(out) == s


def test_validate_refuses_out_of_range():
    for bad in ({"auto_rounds": 6}, {"order": "chaos"}, {"talkativeness": {"characters:mara": 101}},
                {"sitting_out": ["grimoire"]}):
        with pytest.raises(ValueError):
            group_play.validate({**group_play.parse(""), **bad})


def test_validate_refuses_bad_types_and_dedupes():
    for bad in ({"auto_rounds": True}, {"auto_rounds": -1}, {"order_list": [""]},
                {"order_list": [3]}, {"sitting_out": [None]}, {"talkativeness": {"": 5}},
                {"talkativeness": {"characters:mara": 1.5}}, {"talkativeness": {"characters:mara": True}}):
        with pytest.raises(ValueError):
            group_play.validate({**group_play.parse(""), **bad})
    out = group_play.validate({"order": "list", "order_list": ["a", "b", "a"], "sitting_out": ["c", "c"]})
    assert out["order_list"] == ["a", "b"] and out["sitting_out"] == ["c"]
    assert out["auto_rounds"] == 0 and out["talkativeness"] == {}


def test_available_drops_sitting_out_in_roster_order():
    s = {**group_play.parse(""), "sitting_out": ["characters:winifred"]}
    assert [e["ref"] for e in group_play.available(s, R)] == ["characters:mara", "characters:seraphine"]


def test_list_order_then_newcomers_and_sit_out():
    s = {**group_play.parse(""), "order": "list", "order_list": ["characters:winifred"],
         "sitting_out": ["characters:seraphine"]}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
    assert (p["actor_ref"], p["plan"]) == ("characters:winifred", ["characters:mara"])


def test_list_keeps_grimoire_at_its_position():
    s = {**group_play.parse(""), "order": "list", "order_list": ["characters:winifred", "grimoire"]}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
    assert [p["actor_ref"], *p["plan"]] == [
        "characters:winifred", "grimoire", "characters:mara", "characters:seraphine"]


def test_natural_named_first_then_talkative_only():
    s = {**group_play.parse(""), "order": "natural",
         "talkativeness": {"characters:mara": 0, "characters:winifred": 100, "characters:seraphine": 0}}
    p = group_play.plan_post(s, R, trigger="Seraphine, look.", history=[], rng=random.Random(0))
    assert [p["actor_ref"], *p["plan"]] == ["characters:seraphine", "characters:winifred"]


def test_natural_nobody_joins_falls_back_to_quietest():
    s = {**group_play.parse(""), "order": "natural", "talkativeness": {r["ref"]: 0 for r in R}}
    history = [{"role": "assistant", "speaker": "Mara", "content": "x"},
               {"role": "assistant", "speaker": "Winifred", "content": "y"}]
    p = group_play.plan_post(s, R, trigger="", history=history, rng=random.Random(0))
    assert (p["actor_ref"], p["plan"]) == ("characters:seraphine", [])


def test_directed_keeps_named_and_force_and_never_empties():
    silent = {**group_play.parse(""), "talkativeness": {r["ref"]: 0 for r in R}}
    p = group_play.plan_post(silent, R, trigger="Mara?", history=[], rng=random.Random(0), force=("characters:seraphine",))
    assert {e["ref"] for e in p["eligible"]} == {"characters:mara", "characters:seraphine"}
    assert (p["mode"], p["actor_ref"], p["plan"]) == ("directed", None, [])
    s = {**silent, "talkativeness": {**silent["talkativeness"], "characters:winifred": 10}}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))  # kept by roll or by fallback
    assert [e["ref"] for e in p["eligible"]] == ["characters:winifred"]


def test_directed_fallback_ties_go_to_the_longest_silent():
    silent = {**group_play.parse(""), "talkativeness": {r["ref"]: 0 for r in R}}
    history = [{"role": "assistant", "speaker": "Mara", "content": "x"},
               {"role": "assistant", "speaker": "Seraphine Vale", "content": "y"}]
    p = group_play.plan_post(silent, R, trigger="", history=history, rng=random.Random(0))
    assert [e["ref"] for e in p["eligible"]] == ["characters:winifred"]


def test_directed_talkativeness_hundred_keeps_everyone():
    s = {**group_play.parse(""), "talkativeness": {r["ref"]: 100 for r in R}}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
    assert [e["ref"] for e in p["eligible"]] == [r["ref"] for r in R]


def test_manual_has_no_actor_and_no_plan():
    s = {**group_play.parse(""), "order": "manual", "sitting_out": ["characters:mara"]}
    p = group_play.plan_post(s, R, trigger="Mara", history=[], rng=random.Random(0))
    assert p == {"mode": "manual", "eligible": R, "actor_ref": None, "plan": []}


def test_force_is_removed_from_the_sequence():
    s = {**group_play.parse(""), "order": "list"}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0),
                             force=("characters:mara",))
    assert [p["actor_ref"], *p["plan"]] == ["characters:winifred", "characters:seraphine"]


def test_everyone_sitting_out_means_grimoire():
    for order in ("list", "natural"):
        s = {**group_play.parse(""), "order": order, "sitting_out": [r["ref"] for r in R]}
        p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
        assert (p["actor_ref"], p["plan"]) == ("grimoire", [])


def test_plan_continue_rules():
    base = group_play.parse("")
    assert group_play.plan_continue(base, R, last=None, history=[], rng=random.Random(0)) is None
    lst = {**base, "order": "list"}
    assert group_play.plan_continue(lst, R, last={"ref": "characters:winifred", "text": ""},
                                    history=[], rng=random.Random(0)) == "characters:seraphine"
    assert group_play.plan_continue(lst, R, last={"ref": "characters:seraphine", "text": ""},
                                    history=[], rng=random.Random(0)) == "characters:mara"  # wraps
    nat = {**base, "order": "natural", "talkativeness": {r["ref"]: 0 for r in R}}
    assert group_play.plan_continue(nat, R, last={"ref": "characters:mara", "text": "Mara asks Winifred."},
                                    history=[], rng=random.Random(0)) == "characters:winifred"


def test_plan_continue_list_head_without_last_and_manual_is_none():
    lst = {**group_play.parse(""), "order": "list", "order_list": ["characters:seraphine"]}
    assert group_play.plan_continue(lst, R, last=None, history=[], rng=random.Random(0)) == "characters:seraphine"
    manual = {**group_play.parse(""), "order": "manual"}
    assert group_play.plan_continue(manual, R, last=None, history=[], rng=random.Random(0)) is None


def test_plan_continue_list_when_last_is_not_in_the_sequence():
    lst = {**group_play.parse(""), "order": "list"}
    assert group_play.plan_continue(lst, R, last={"ref": "characters:ghost", "text": ""},
                                    history=[], rng=random.Random(0)) == "characters:mara"
    assert group_play.plan_continue(lst, R, last={"ref": None, "text": ""},
                                    history=[], rng=random.Random(0)) == "characters:mara"


def test_plan_continue_natural_never_picks_the_last_speaker():
    nat = {**group_play.parse(""), "order": "natural", "talkativeness": {r["ref"]: 100 for r in R}}
    for seed in range(10):
        got = group_play.plan_continue(nat, R, last={"ref": "characters:mara", "text": "Mara sighs."},
                                       history=[], rng=random.Random(seed))
        assert got in {"characters:winifred", "characters:seraphine"}
    lone = [R[0]]
    assert group_play.plan_continue(nat, lone, last={"ref": "characters:mara", "text": ""},
                                    history=[], rng=random.Random(0)) is None


def test_unknown_refs_are_ignored_by_planning():
    s = {**group_play.parse(""), "order": "list", "order_list": ["characters:ghost", "characters:mara"],
         "sitting_out": ["characters:ghost"]}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
    assert "characters:ghost" not in [p["actor_ref"], *p["plan"]]


def test_duplicate_roster_names_name_nobody_and_do_not_crash():
    dup = [{"ref": "characters:mara", "name": "Mara"}, {"ref": "characters:mara-2", "name": "Mara"},
           {"ref": "characters:winifred", "name": "Winifred"}]
    s = {**group_play.parse(""), "order": "natural", "talkativeness": {e["ref"]: 0 for e in dup}}
    p = group_play.plan_post(s, dup, trigger="Mara and Winifred", history=[], rng=random.Random(0))
    assert [p["actor_ref"], *p["plan"]] == ["characters:winifred"]  # "Mara" is ambiguous
    p = group_play.plan_post(s, dup, trigger="", history=[], rng=random.Random(0))
    assert p["actor_ref"] in {e["ref"] for e in dup} and p["plan"] == []


def test_next_planned_skips_newly_sitting_out():
    s = {**group_play.parse(""), "sitting_out": ["characters:winifred"]}
    assert group_play.next_planned(s, R, ["characters:winifred", "grimoire", "characters:mara"]) == (
        "grimoire", ["characters:mara"])


def test_next_planned_exhausted_and_absent():
    s = {**group_play.parse(""), "sitting_out": ["characters:winifred"]}
    assert group_play.next_planned(s, R, []) == (None, [])
    assert group_play.next_planned(s, R, ["characters:winifred", "characters:ghost"]) == (None, [])
