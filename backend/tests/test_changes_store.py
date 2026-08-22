import time

import pytest

from grimoire.store import changes


def test_line_diff_insert_only():
    d = changes.line_diff("a", "a\nb")
    assert d == [{"op": "equal", "text": "a"}, {"op": "insert", "text": "b"}]


def test_line_diff_delete_only():
    d = changes.line_diff("a\nb", "a")
    assert d == [{"op": "equal", "text": "a"}, {"op": "delete", "text": "b"}]


def test_line_diff_replace_emits_delete_then_insert():
    d = changes.line_diff("a\nold", "a\nnew")
    assert d == [{"op": "equal", "text": "a"},
                 {"op": "delete", "text": "old"},
                 {"op": "insert", "text": "new"}]


def test_line_diff_identical_all_equal():
    assert changes.line_diff("a\nb", "a\nb") == [
        {"op": "equal", "text": "a"}, {"op": "equal", "text": "b"}]


def test_line_diff_empty_sides():
    assert changes.line_diff("", "") == []
    assert changes.line_diff("", "x") == [{"op": "insert", "text": "x"}]
    assert changes.line_diff("x", "") == [{"op": "delete", "text": "x"}]


from grimoire.store import (  # noqa: E402 - deliberate late import; see the lines above
    campaigns,
    worlds,
)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_record_and_read_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    fields = [{"field": "body", "label": "Harbor — locations", "before": "old", "after": "new"}]
    changes.record(cid, "s1", {"locations/harbor": fields})
    assert changes.read(cid) == {"locations/harbor": {"scene": "s1", "fields": fields}}


def test_record_replaces_prior_entry(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {"lore/pact": [{"field": "body", "label": "L", "before": "a", "after": "b"}]})
    changes.record(cid, "s2", {"lore/pact": [{"field": "body", "label": "L", "before": "b", "after": "c"}]})
    entry = changes.read(cid)["lore/pact"]
    assert entry["scene"] == "s2" and entry["fields"][0]["before"] == "b"


def test_record_empty_is_noop(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {})
    assert changes.read(cid) == {}


def test_read_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "changes.json").write_text("{not json", encoding="utf-8")
    assert changes.read(cid) == {}


from grimoire.store import (  # noqa: E402 - deliberate late import; see the lines above
    absorb,
    entities,
    scenes,
)


def _lore_edit(before, after):
    return {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body", "before": before, "after": after,
            "authored": False}


def test_apply_records_lore_edit(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "old body\n\nnew line")], "s1")
    entry = changes.read(cid)["lore/pact"]
    assert entry["scene"] == "s1"
    assert entry["fields"] == [{"field": "body", "label": "The Pact — lore",
                                "before": "old body", "after": "old body\n\nnew line"}]


def test_apply_accumulates_multiple_fields_per_record(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, playstate
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    card = characters.blank_card("Mara")
    card["data"]["personality"] = "aloof"
    ch = characters.create_character(croot, "Mara", "main", card)[0]
    playstate.write_state(croot, ch, "calm")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    cs = {"id": f"character_state:{ch}", "kind": "character_state",
          "target": {"kind": "characters", "id": ch}, "label": "Mara — current state",
          "field": "current_state", "before": "calm", "after": "shaken", "authored": False}
    au = {"id": f"authored:{ch}:personality", "kind": "authored",
          "target": {"kind": "characters", "id": ch}, "label": "Mara — personality (card edit)",
          "field": "personality", "before": "aloof", "after": "warmer", "authored": True}
    absorb.apply_edits(cid, [cs, au], sid)
    fields = changes.read(cid)[f"characters/{ch}"]["fields"]
    assert {f["field"] for f in fields} == {"current_state", "personality"}


def test_apply_skips_non_browsable_kinds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot_edit = {"id": "plot:the-map", "kind": "plot", "target": {"kind": "plot", "id": "the-map"},
                 "label": "The map — advanced", "field": "beat", "before": "", "after": "It moved.",
                 "authored": False,
                 "payload": {"id": "the-map", "title": "The map", "status": "advanced", "scene": "s1"}}
    absorb.apply_edits(cid, [plot_edit], "s1")
    assert changes.read(cid) == {}


def test_apply_without_sid_records_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "old body\n\nx")])
    assert changes.read(cid) == {}


# --- the trimmed, bounded diff (#130) -----------------------------------------
# `line_diff` matches the common prefix and suffix off directly and hands only
# the middle to difflib, with `autojunk` back on when that middle is long. Both
# halves of that rule are load-bearing and neither is visible in a small case,
# so both are held here.


def test_one_edit_in_a_huge_repetitive_body_is_one_edit():
    """The case the prompt diff is for, at a size the record log can also reach.

    Exact BECAUSE of the trimming: one edit leaves a one-line middle, so the
    heuristic never engages and cannot refuse to anchor on the repeated line.
    Without the trimming this same pair is 150-plus deletions and insertions
    with `autojunk` on, and takes 13.7s with it off.
    """
    lines = ["- item"] * 10_000
    edited = list(lines)
    edited[5_000] = "- item CHANGED"
    ops = changes.line_diff("\n".join(lines), "\n".join(edited))

    assert [o for o in ops if o["op"] == "delete"] == [{"op": "delete", "text": "- item"}]
    assert [o for o in ops if o["op"] == "insert"] == [{"op": "insert",
                                                       "text": "- item CHANGED"}]
    assert len(ops) == len(lines) + 1          # every other line still reported


def _reconstruct(ops):
    """`(before, after)` rebuilt from a tagged diff. `equal` belongs to both,
    `delete` to the left only, `insert` to the right only."""
    left = [o["text"] for o in ops if o["op"] in ("equal", "delete")]
    right = [o["text"] for o in ops if o["op"] in ("equal", "insert")]
    return left, right


@pytest.mark.parametrize("before,after", [
    ("", ""),
    ("a", ""),
    ("", "a"),
    ("a\nb\nc", "a\nb\nc"),
    ("a\nb\nc", "c\nb\na"),
    ("\n".join(str(n) for n in range(3000)),
     "\n".join(str(n) for n in range(2999, -1, -1))),
    # over the limit, and every shape the bound routes differently
    ("\n".join(f"l{n}" for n in range(3000)),
     "\n".join(f"l{n}" for n in [*range(1500, 3000), *range(1500)])),
    ("\n".join(["dup"] * 3000), "\n".join(["dup"] * 2999 + ["other"])),
    ("\n".join(f"l{n}" for n in range(3000)), "\n".join(["dup"] * 3000)),
])
def test_the_diff_always_reconstructs_both_sides(before, after):
    """The invariant a hand-rolled diff lives or dies by, and the one thing the
    per-shape tests below cannot check between them: whatever route the bound
    sends a span down -- trimmed, anchored, exact, or coarse -- the `equal` plus
    `delete` rows must still be exactly the left side and the `equal` plus
    `insert` rows exactly the right one. An anchor emitted twice, or a gap
    dropped between two of them, shows up here and nowhere else.
    """
    left, right = _reconstruct(changes.line_diff(before, after))
    assert left == before.splitlines()
    assert right == after.splitlines()


def test_a_reordered_distinct_span_is_bounded_and_still_exact():
    """The case that showed the size cap was never a bound (raised in review):
    `autojunk` only discards POPULAR lines, so all-distinct input leaves every
    line eligible and difflib goes quadratic anyway -- 8,000 lines reordered in
    adjacent pairs measured 4.5s, scaling 4x per doubling.

    Anchoring on lines unique to both sides answers it in milliseconds AND
    exactly: every pair really did swap, so 4,000 of each is the right answer,
    not a coarse one.
    """
    n = 8_000
    a = [f"line {k}" for k in range(n)]
    b = [line for k in range(0, n, 2) for line in (a[k + 1], a[k])]

    started = time.perf_counter()
    ops = changes.line_diff("\n".join(a), "\n".join(b))
    assert time.perf_counter() - started < 10.0

    assert sum(1 for o in ops if o["op"] == "delete") == n // 2
    assert sum(1 for o in ops if o["op"] == "insert") == n // 2


def test_a_long_differing_middle_stays_bounded():
    """The other half: trimming cannot help two long spans that share neither
    end, and there `autojunk` is a BOUND as well as a filter -- without it
    SequenceMatcher is quadratic on repetitive input.

    The ceiling is enormous on purpose. This is not a measurement of the runner
    -- the repo has been bitten by one of those -- it is an algorithmic guard:
    the bounded answer is milliseconds and the unbounded one is tens of seconds,
    so any margin in between catches a regression and none of it flakes.
    """
    n = changes.EXACT_DIFF_LIMIT * 4
    before = "\n".join(["head", *["- item"] * n, "tail"])
    after = "\n".join(["HEAD", *["- item"] * n, "TAIL"])

    started = time.perf_counter()
    ops = changes.line_diff(before, after)
    assert time.perf_counter() - started < 10.0
    assert ops                                  # and it really did diff them


def test_a_long_distinct_middle_still_diffs_precisely():
    """The bound is on LENGTH, but what the heuristic actually punishes is
    duplication -- so the shapes real sections have keep their precision well
    past the limit. Both of these are far over it."""
    lines = [f"**Seraphine:** line {k}" for k in range(2_500) for _ in (0, 1)]
    lines[1::2] = [""] * 2_500
    # a transcript with its front trimmed by the packer and an exchange appended
    ops = changes.line_diff("\n".join(lines[:5_000]),
                            "\n".join([*lines[40:5_000], "**Mara:** and then?", ""]))
    assert sum(1 for o in ops if o["op"] == "delete") == 40
    assert sum(1 for o in ops if o["op"] == "insert") == 2

    entries = [f"- {k}: a fact about the marsh" for k in range(4_000)]
    ops = changes.line_diff("\n".join(["header A", *entries, "footer A"]),
                            "\n".join(["header B", *entries, "footer B"]))
    assert sum(1 for o in ops if o["op"] == "delete") == 2
    assert sum(1 for o in ops if o["op"] == "insert") == 2


def test_a_duplicate_dominated_middle_over_the_limit_degrades():
    """The bound's one real cost, pinned so it is a known limit rather than a
    surprise (raised in review).

    With both ends edited around a long run of IDENTICAL lines, nothing in the
    middle can anchor once the heuristic is back on, and the whole span is
    reported replaced. Under the limit the same shape is exact, which is what
    makes this the bound talking rather than the diff being wrong.
    """
    n = changes.EXACT_DIFF_LIMIT * 4
    ops = changes.line_diff("\n".join(["x", *["- item"] * n, "y"]),
                            "\n".join(["p", *["- item"] * n, "q"]))
    assert sum(1 for o in ops if o["op"] == "delete") == n + 2

    small = changes.EXACT_DIFF_LIMIT // 2
    ops = changes.line_diff("\n".join(["x", *["- item"] * small, "y"]),
                            "\n".join(["p", *["- item"] * small, "q"]))
    assert sum(1 for o in ops if o["op"] == "delete") == 2
