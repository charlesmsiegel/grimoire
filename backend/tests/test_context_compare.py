"""`context.compare_breakdowns` (#130): what changed between two compositions.

Pure over two `_breakdown` payloads, so it is tested that way -- the routes
covering the live/frozen wiring live in `test_prompt_log_routes.py`.
"""

from grimoire.store import context
from grimoire.store.context import compare


def _row(rid, text, **over):
    row = {"id": rid, "label": rid.title(), "text": text, "tier": "background",
           "dropped": False, "trimmed": 0, "pinned": False,
           "tokens": len(text.split())}
    row.update(over)
    return row


def _bd(*rows, total=0):
    return {"sections": list(rows), "total_tokens": total,
            "dropped_tokens": 0, "budget_tokens": 0}


def _by_id(result):
    return {r["id"]: r for r in result["sections"]}


def test_identical_compositions_report_nothing_changed():
    bd = _bd(_row("world", "a keep by the sea"), _row("cast", "Seraphine"))
    result = context.compare_breakdowns(bd, bd)
    assert [r["status"] for r in result["sections"]] == ["unchanged", "unchanged"]
    # An unchanged row carries no lines: the reader already has both panels, and
    # the point of the diff is what is NOT in them.
    assert all(r["diff"] == [] for r in result["sections"])


def test_a_changed_section_reports_the_lines_that_moved():
    before = _bd(_row("world", "a keep by the sea\nthe gate is shut"))
    after = _bd(_row("world", "a keep by the sea\nthe gate is open"))
    row = _by_id(context.compare_breakdowns(before, after))["world"]
    assert row["status"] == "changed"
    assert row["diff"] == [{"op": "equal", "text": "a keep by the sea"},
                           {"op": "delete", "text": "the gate is shut"},
                           {"op": "insert", "text": "the gate is open"}]


def test_added_and_removed_sections_are_named_as_such():
    before = _bd(_row("world", "x"), _row("gone", "y"))
    after = _bd(_row("world", "x"), _row("fresh", "z"))
    rows = _by_id(context.compare_breakdowns(before, after))
    assert rows["gone"]["status"] == "removed"
    assert rows["gone"]["head"] is None
    assert rows["gone"]["base"]["tokens"] == 1
    assert rows["fresh"]["status"] == "added"
    assert rows["fresh"]["base"] is None
    assert rows["fresh"]["head"]["label"] == "Fresh"
    # Both carry their whole text: the reader is looking at ONE panel, so the
    # side that no longer exists is nowhere else on screen.
    assert rows["gone"]["diff"] == [{"op": "delete", "text": "y"}]
    assert rows["fresh"]["diff"] == [{"op": "insert", "text": "z"}]
    # A key that changed is a removal and an addition, not a rename — and the
    # removal reads first, so the row a section was replaced BY comes after the
    # one it replaced.
    assert [r["id"] for r in context.compare_breakdowns(before, after)["sections"]] \
        == ["world", "gone", "fresh"]


def test_a_removal_does_not_make_every_later_section_look_changed():
    """Sections are matched by id, not by position. Pairing by index would
    report the whole tail of the prompt as rewritten because one row above it
    dropped out -- which is the noise that would make the panel useless."""
    before = _bd(_row("one", "1"), _row("two", "2"), _row("three", "3"))
    after = _bd(_row("one", "1"), _row("three", "3"))
    rows = _by_id(context.compare_breakdowns(before, after))
    assert rows["two"]["status"] == "removed"
    assert rows["one"]["status"] == rows["three"]["status"] == "unchanged"


def test_the_merged_order_keeps_a_removal_where_it_stood():
    """Neither side's order on its own can show a removal in place. The result
    is the two merged, so reading top to bottom reads the new prompt with the
    old one's cuts still visible."""
    before = _bd(_row("one", "1"), _row("two", "2"), _row("three", "3"))
    after = _bd(_row("one", "1"), _row("three", "3"))
    assert [r["id"] for r in context.compare_breakdowns(before, after)["sections"]] \
        == ["one", "two", "three"]


def test_a_section_the_packer_dropped_is_a_change_even_with_identical_text():
    """The case a token-count or text-only comparison hides, and the one most
    worth catching: the words are the same, and the model never saw them."""
    before = _bd(_row("lore", "the pact was signed at dusk"))
    after = _bd(_row("lore", "the pact was signed at dusk", dropped=True))
    row = _by_id(context.compare_breakdowns(before, after))["lore"]
    assert row["status"] == "changed"
    assert row["diff"] == []          # nothing moved; the flag did
    assert (row["base"]["dropped"], row["head"]["dropped"]) == (False, True)


def test_a_trimmed_history_is_a_change_even_when_the_join_matches():
    before = _bd(_row("history", "a\nb", trimmed=0))
    after = _bd(_row("history", "a\nb", trimmed=4))
    row = _by_id(context.compare_breakdowns(before, after))["history"]
    assert row["status"] == "changed"
    assert (row["base"]["trimmed"], row["head"]["trimmed"]) == (0, 4)


def test_a_pin_taking_effect_is_a_change():
    before = _bd(_row("lore", "x", pinned=False))
    after = _bd(_row("lore", "x", pinned=True))
    assert _by_id(context.compare_breakdowns(before, after))["lore"]["status"] == "changed"


def test_a_renamed_label_keeps_the_id_and_reports_both():
    """#29 lets a reader rename a section; the id is what it still is. Nothing
    is lost -- each side carries the label it had."""
    before = _bd(_row("lore", "x", label="World lore"))
    after = _bd(_row("lore", "x", label="Background"))
    row = _by_id(context.compare_breakdowns(before, after))["lore"]
    assert row["status"] == "changed"
    assert row["label"] == "Background"          # the heading is the newer one
    assert row["base"]["label"] == "World lore"


def test_long_unchanged_runs_collapse_to_one_skip_row():
    """A prompt section is the whole transcript. One appended exchange must not
    ship four hundred `equal` rows to say the rest stood still."""
    before = _bd(_row("history", "\n".join(f"line {n}" for n in range(200))))
    after = _bd(_row("history", "\n".join(f"line {n}" for n in range(200)) + "\nand then"))
    row = _by_id(context.compare_breakdowns(before, after))["history"]

    skips = [r for r in row["diff"] if r["op"] == "skip"]
    assert len(skips) == 1
    assert skips[0]["count"] == 200 - compare.CONTEXT_LINES
    # Every row still answers `op` and `text`, so a reader of the tagged format
    # meets an op it does not know rather than content that is not there.
    assert all({"op", "text"} <= set(r) for r in row["diff"])
    kept = [r for r in row["diff"] if r["op"] == "equal"]
    assert [r["text"] for r in kept] == [f"line {n}" for n in (197, 198, 199)]
    assert [r["text"] for r in row["diff"] if r["op"] == "insert"] == ["and then"]


def _gap(n: int) -> list[dict]:
    """The rows for two edits `n` unchanged lines apart."""
    mid = "\n".join(f"e{i}" for i in range(n))
    return _by_id(context.compare_breakdowns(
        _bd(_row("lore", f"A\n{mid}\nB")),
        _bd(_row("lore", f"A!\n{mid}\nB!"))))["lore"]["diff"]


def test_a_short_unchanged_run_is_left_alone():
    """Replacing one or two lines with a row that says "1 unchanged line" costs
    the reader the line and saves nothing.

    Paired with the case below so neither is vacuous: at `CONTEXT_LINES` = 3 a
    gap has to reach 7 before ANY of it falls outside the kept window, so a
    shorter example would prove only that the window works.
    """
    rows = _gap(7)          # one line falls outside the window; kept anyway
    assert not any(r["op"] == "skip" for r in rows)
    assert [r["text"] for r in rows if r["op"] == "equal"] == [f"e{i}" for i in range(7)]


def test_a_long_enough_unchanged_run_between_two_edits_collapses():
    rows = _gap(9)
    assert [r for r in rows if r["op"] == "skip"] == [{"op": "skip", "text": "", "count": 3}]
    # The window either side of both edits survives it.
    assert [r["text"] for r in rows if r["op"] == "equal"] \
        == ["e0", "e1", "e2", "e6", "e7", "e8"]


def test_duplicate_keys_pair_one_to_one():
    """`_breakdown` numbers its appended rows precisely because two of them can
    carry the same label, and a snapshot frozen before ids existed has only
    labels to key on. Without the occurrence counter difflib would pair the
    first of each and drop the rest."""
    before = {"sections": [{"label": "Shape rules", "text": "one", "tokens": 1,
                            "tier": "lock-in", "dropped": False, "trimmed": 0},
                           {"label": "Shape rules", "text": "two", "tokens": 1,
                            "tier": "lock-in", "dropped": False, "trimmed": 0}]}
    after = {"sections": [{"label": "Shape rules", "text": "one", "tokens": 1,
                           "tier": "lock-in", "dropped": False, "trimmed": 0},
                          {"label": "Shape rules", "text": "TWO", "tokens": 1,
                           "tier": "lock-in", "dropped": False, "trimmed": 0}]}
    rows = context.compare_breakdowns(before, after)["sections"]
    assert [r["status"] for r in rows] == ["unchanged", "changed"]
    assert all(r["id"] == "Shape rules" for r in rows)


def test_a_snapshot_without_ids_is_matched_by_label_on_both_sides():
    """A snapshot frozen before #29 carries no `id`, and it still has to diff
    against a composition that does.

    The ids are deliberately NOT equal to the labels here. Keying each side by
    whatever it has would compare `Character state` against `character_state`,
    match nothing, and report every section as removed and re-added -- which is
    what an earlier version of this test hid by giving the newer row an id equal
    to its label.
    """
    before = {"sections": [
        {"label": "Character state", "text": "Mara is wary", "tokens": 3,
         "tier": "spotlight", "dropped": False, "trimmed": 0},
        {"label": "World lore", "text": "the marsh floods", "tokens": 3,
         "tier": "background", "dropped": False, "trimmed": 0}]}
    after = _bd(_row("character_state", "Mara is calm", label="Character state"),
                _row("world_lore", "the marsh floods", label="World lore"))

    rows = context.compare_breakdowns(before, after)["sections"]
    assert [r["status"] for r in rows] == ["changed", "unchanged"]
    # ...and the row still names itself by the id the newer side has.
    assert [r["id"] for r in rows] == ["character_state", "world_lore"]


def test_ids_are_used_when_both_sides_have_them_even_if_labels_moved():
    """The other half of the rule: a rename must not look like a replacement."""
    before = _bd(_row("lore", "x", label="World lore"))
    after = _bd(_row("lore", "x", label="Background"))
    rows = context.compare_breakdowns(before, after)["sections"]
    assert [r["status"] for r in rows] == ["changed"]      # not removed + added


def test_a_corrupt_payload_is_described_rather_than_raised_on():
    """Every `prompt_log` reader omits rather than crashes; a comparison of one
    hand-edited file against a live composition must do the same."""
    live = _bd(_row("lore", "x"))
    assert context.compare_breakdowns({}, live)["sections"][0]["status"] == "added"
    assert [r["status"] for r in
            context.compare_breakdowns({"sections": "nonsense"}, live)["sections"]] == ["added"]
    junk = {"sections": [None, 7, {"id": "lore", "label": "Lore"}]}
    row = _by_id(context.compare_breakdowns(junk, live))["lore"]
    assert row["status"] == "changed"
    assert row["base"]["tokens"] == 0
    # ...including a field of the wrong TYPE, which is what a hand edit leaves
    # and what `int()` would raise on from inside the "never raises" promise.
    typed = {"sections": [{"id": "lore", "label": "Lore", "text": 7,
                           "tokens": "lots", "trimmed": None}]}
    row = _by_id(context.compare_breakdowns(typed, live))["lore"]
    assert (row["base"]["tokens"], row["base"]["trimmed"]) == (0, 0)


def test_one_edit_in_a_long_repetitive_section_is_one_edit():
    """difflib's `autojunk` heuristic, which `changes.line_diff` turns off for
    this caller (#130).

    Past 200 lines it treats any line occurring in more than 1% of the input as
    junk that can never anchor a match. Prompt sections are full of exactly that
    -- a roster, a fact list, a set of world-info entries all sharing a bullet
    or a speaker prefix -- and with the heuristic ON, editing one item of a
    300-line list reports 150 deletions and 150 insertions instead of one of
    each. A panel whose noise grows with the length of the section is one nobody
    reads twice. Measured, not assumed: this exact pair is 150/150 with autojunk
    left on.
    """
    lines = ["- item"] * 300
    edited = list(lines)
    edited[150] = "- item CHANGED"
    row = _by_id(context.compare_breakdowns(
        _bd(_row("lore", "\n".join(lines))),
        _bd(_row("lore", "\n".join(edited)))))["lore"]

    assert [r["text"] for r in row["diff"] if r["op"] == "delete"] == ["- item"]
    assert [r["text"] for r in row["diff"] if r["op"] == "insert"] == ["- item CHANGED"]
