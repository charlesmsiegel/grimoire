"""The activation-control fields on a world-info entry: what they are, how a
hand-edited value reads (leniently, never raising), and how an editor save is
refused (strictly, naming the key)."""
import pytest

from grimoire.store import lore_fields
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.lore_fields import FIELD_KEYS, Controls, invalid, parse


def test_parse_defaults_when_absent():
    assert parse({}) == Controls()


def test_parse_reads_every_field():
    meta = {
        "secondary_keys": "tide, harbour",
        "key_logic": "not_any",
        "scan_depth": "3",
        "sticky": "2",
        "cooldown": "4",
        "priority": "250",
        "keep": "true",
        "recursion": "pulls_only",
        "known_by": "characters:mara, pcs:winifred",
    }
    assert parse(meta) == Controls(
        secondary_keys=("tide", "harbour"),
        key_logic="not_any",
        scan_depth=3,
        sticky=2,
        cooldown=4,
        priority=250,
        keep=True,
        recursion="pulls_only",
        known_by=("characters:mara", "pcs:winifred"),
    )


def test_parse_is_lenient():
    meta = {"priority": "high", "sticky": "-3", "key_logic": "maybe",
            "scan_depth": "999", "keep": "yes"}
    assert parse(meta) == Controls()


def test_parse_one_bad_field_does_not_spoil_the_others():
    got = parse({"priority": "high", "sticky": "2", "key_logic": "and_all"})
    assert got == Controls(sticky=2, key_logic="and_all")


def test_parse_survives_non_string_values():
    meta = {"priority": ["x"], "sticky": True, "scan_depth": 2.5,
            "secondary_keys": ["a"], "known_by": None, "keep": True}
    assert parse(meta) == Controls()


def test_parse_ints_reject_bools_and_non_integral():
    assert parse({"sticky": True}).sticky == 0
    assert parse({"scan_depth": "2.5"}).scan_depth is None
    assert parse({"scan_depth": " 7 "}).scan_depth == 7
    assert parse({"scan_depth": 7}).scan_depth == 7


def test_parse_bounds_are_inclusive():
    got = parse({"scan_depth": "100", "sticky": "50", "cooldown": "50",
                 "priority": "1000"})
    assert (got.scan_depth, got.sticky, got.cooldown, got.priority) == (
        100, 50, 50, 1000)
    got = parse({"scan_depth": "0", "sticky": "0", "cooldown": "0",
                 "priority": "0"})
    assert (got.scan_depth, got.priority) == (0, 0)
    assert parse({"priority": "1001"}).priority == lore_fields.DEFAULT_PRIORITY


def test_parse_keep_is_exactly_true():
    assert parse({"keep": " TRUE "}).keep is True
    assert parse({"keep": "false"}).keep is False
    assert parse({"keep": "1"}).keep is False


def test_parse_known_by_drops_non_actor_refs():
    got = parse({"known_by": "locations:saltmarch, characters:mara"})
    assert got.known_by == ("characters:mara",)


def test_parse_known_by_drops_malformed_refs():
    got = parse({"known_by": "mara, characters:, characters:a:b, pcs:winifred"})
    assert got.known_by == ("pcs:winifred",)


def test_split_list():
    assert lore_fields.split_list(" a, b ,, a ,c") == ("a", "b", "c")
    assert lore_fields.split_list("") == ()
    assert lore_fields.split_list(None) == ()
    assert lore_fields.split_list(["a"]) == ()


def test_recursion_and_timed_helpers():
    assert Controls().pulled_ok() and Controls().pulls_ok()
    assert Controls(recursion="pulled_only").pulled_ok()
    assert not Controls(recursion="pulled_only").pulls_ok()
    assert Controls(recursion="pulls_only").pulls_ok()
    assert not Controls(recursion="pulls_only").pulled_ok()
    assert not Controls(recursion="none").pulled_ok()
    assert not Controls(recursion="none").pulls_ok()
    assert not Controls().timed()
    assert Controls(sticky=1).timed()
    assert Controls(cooldown=1).timed()


def test_invalid_names_each_bad_key():
    fields = {"priority": "1001", "sticky": "x", "key_logic": "and_any",
              "recursion": "sideways", "known_by": "locations:saltmarch"}
    assert invalid(fields) == ["known_by", "priority", "recursion", "sticky"]


def test_invalid_accepts_blank_as_clear():
    assert invalid(dict.fromkeys(FIELD_KEYS, "")) == []


def test_invalid_ignores_unknown_keys():
    assert invalid({"holder": "x"}) == []


def test_invalid_accepts_good_values_at_the_bounds():
    good = {"secondary_keys": "tide, harbour", "key_logic": "not_all",
            "scan_depth": "100", "sticky": "0", "cooldown": "50",
            "priority": "1000", "keep": "true", "recursion": "none",
            "known_by": "characters:mara, pcs:winifred"}
    assert invalid(good) == []


def test_invalid_rejects_out_of_bound_and_odd_values():
    bad = {"scan_depth": "101", "sticky": "-1", "cooldown": "2.5",
           "priority": "-1", "keep": "false", "key_logic": "maybe",
           "known_by": "characters:a:b"}
    assert invalid(bad) == ["cooldown", "keep", "key_logic", "known_by",
                            "priority", "scan_depth", "sticky"]


def test_invalid_rejects_non_string_values():
    assert invalid({"sticky": True, "secondary_keys": ["a"],
                    "known_by": 3}) == ["known_by", "secondary_keys", "sticky"]


# Every character `str.splitlines` splits on: the frontmatter writer puts a value
# on one line and the reader splits with `splitlines`, so each of these
# truncates the record on its way back.
LINE_BOUNDARIES = ["\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
                   "\x85", "\u2028", "\u2029"]


@pytest.mark.parametrize("boundary", LINE_BOUNDARIES)
def test_invalid_rejects_a_value_spanning_lines(boundary):
    bad = {"secondary_keys": f"tide{boundary}harbour",
           "known_by": f"characters:mara{boundary}x",
           "key_logic": f"and_any{boundary}", "sticky": f"2{boundary}"}
    assert invalid(bad) == ["key_logic", "known_by", "secondary_keys", "sticky"]
    # even a value that is otherwise blank would be written as-is
    assert invalid({"keep": boundary}) == ["keep"]


def test_invalid_accepts_a_tab_inside_a_value():
    assert invalid({"secondary_keys": "tide,\tharbour"}) == []


@pytest.mark.parametrize("boundary", LINE_BOUNDARIES)
def test_single_line_agrees_with_what_frontmatter_can_carry(boundary):
    value = f"tide{boundary}harbour"
    meta, _ = parse_frontmatter(dump_frontmatter({"secondary_keys": value}, "body"))
    assert meta.get("secondary_keys") != value
    assert not lore_fields.single_line(value)
    ok = "tide, harbour"
    meta, _ = parse_frontmatter(dump_frontmatter({"secondary_keys": ok}, "body"))
    assert meta["secondary_keys"] == ok and lore_fields.single_line(ok)
