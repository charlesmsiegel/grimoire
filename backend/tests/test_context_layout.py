"""The prompt layout: the merge rules, the file, and the toggle (#29)."""

from types import SimpleNamespace

import pytest

from grimoire.store import config
from grimoire.store.context import assemble, layout


class _Sec(SimpleNamespace):
    """A catalog stand-in. `merge` wants only `id`, `label` and `_replace`;
    `describe` reads `tier` as well, to show what the packer would drop first.
    Keeping the tests off the real `Section` is deliberate: neither must care
    what else a section carries."""

    def _replace(self, **kw):
        return _Sec(**{**self.__dict__, **kw})


def _s(sid, label="L", tier="spotlight"):
    return _Sec(id=sid, label=label, tier=tier)


CATALOG = [_s("a"), _s("b"), _s("c"), _s("d")]


@pytest.fixture
def store_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------- the merge

def test_empty_layout_is_the_catalog_unchanged():
    assert [s.id for s in layout.merge(CATALOG, [])] == ["a", "b", "c", "d"]


def test_stored_order_wins():
    stored = [{"id": "d"}, {"id": "c"}, {"id": "b"}, {"id": "a"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["d", "c", "b", "a"]


def test_disabled_section_is_omitted():
    stored = [{"id": "a"}, {"id": "b", "enabled": False}, {"id": "c"}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "c", "d"]


def test_unknown_id_is_ignored():
    """A section a later version removed retires with no migration."""
    stored = [{"id": "a"}, {"id": "gone"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "b", "c", "d"]


def test_new_catalog_section_lands_after_its_neighbour_not_at_the_end():
    """The upgrade rule. The saved layout predates "c"; appending it would put
    a new section below everything, which for a Response format block is the
    one place it must not go."""
    stored = [{"id": "d"}, {"id": "a"}, {"id": "b"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["d", "a", "b", "c"]
    stored = [{"id": "a"}, {"id": "b"}, {"id": "d"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "b", "c", "d"]


def test_new_section_with_no_present_predecessor_goes_to_the_front():
    stored = [{"id": "b"}, {"id": "c"}, {"id": "d"}]
    catalog = [_s("new"), *CATALOG]
    assert [s.id for s in layout.merge(catalog, stored)][0] == "new"


def test_two_new_sections_keep_catalog_order_between_themselves():
    catalog = [_s("a"), _s("x"), _s("y"), _s("b")]
    stored = [{"id": "a"}, {"id": "b"}]
    assert [s.id for s in layout.merge(catalog, stored)] == ["a", "x", "y", "b"]


def test_a_new_section_anchors_to_a_disabled_neighbour_too():
    """"c" is new and its catalog neighbour "b" is switched off. It still
    anchors to "b" -- `_ordered` keeps the disabled rows precisely so it can,
    which is what makes the next test true."""
    stored = [{"id": "b", "enabled": False}, {"id": "a"}, {"id": "d"}]
    assert [r["id"] for r in layout.describe(CATALOG, stored)] == ["b", "c", "a", "d"]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["c", "a", "d"]


def test_switching_a_section_off_does_not_move_a_different_one():
    """The property that matters, and the reason a newcomer anchors to
    disabled rows: a toggle is a statement about one section. Anchoring to
    survivors only would drag "c" up the message every time the reader
    switched off something above it."""
    on = [{"id": "a"}, {"id": "b"}, {"id": "d"}]
    off = [{"id": "a"}, {"id": "b", "enabled": False}, {"id": "d"}]
    assert ([r["id"] for r in layout.describe(CATALOG, on)]
            == [r["id"] for r in layout.describe(CATALOG, off)]
            == ["a", "b", "c", "d"])


def test_an_explicit_off_is_not_undone_by_the_insert_pass():
    """The disabled section is `seen`, so the pass that adds unmentioned
    sections must not treat it as unmentioned and put it back."""
    stored = [{"id": "b", "enabled": False}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "c", "d"]


def test_label_override_applies_and_is_capped():
    stored = [{"id": "a", "label": "  Mine  "}, {"id": "b", "label": "x" * 200}]
    out = {s.id: s.label for s in layout.merge(CATALOG, stored)}
    assert out["a"] == "Mine"
    assert len(out["b"]) == layout.MAX_LABEL


def test_blank_or_non_string_label_falls_back_to_the_catalog():
    stored = [{"id": "a", "label": "   "}, {"id": "b", "label": 7}]
    out = {s.id: s.label for s in layout.merge(CATALOG, stored)}
    assert out["a"] == "L" and out["b"] == "L"


def test_the_catalog_is_never_mutated():
    layout.merge(CATALOG, [{"id": "a", "label": "Mine"}])
    assert [s.label for s in CATALOG] == ["L", "L", "L", "L"]


def test_duplicate_ids_keep_only_the_first():
    """The second "b" is dropped, label and all. "a" then has no surviving
    predecessor, so the insert rule puts it at the front."""
    stored = [{"id": "b"}, {"id": "b", "label": "second"}]
    out = layout.merge(CATALOG, stored)
    assert [s.id for s in out] == ["a", "b", "c", "d"]
    assert {s.id: s.label for s in out}["b"] == "L"


def test_malformed_entries_are_skipped_individually():
    stored = ["not a dict", {"no_id": 1}, {"id": 5}, {"id": "b"}]
    assert [s.id for s in layout.merge(CATALOG, stored)] == ["a", "b", "c", "d"]


# ------------------------------------------------------------- the describe

def test_describe_keeps_disabled_sections_visible_and_in_place():
    """The editor has to list a switched-off section, or there is no way back
    on. It shows where it sits, greyed, not where it would sit if enabled."""
    stored = [{"id": "a"}, {"id": "b", "enabled": False}, {"id": "c"}]
    rows = layout.describe(CATALOG, stored)
    assert [r["id"] for r in rows] == ["a", "b", "c", "d"]
    assert [r["enabled"] for r in rows] == [True, False, True, True]
    assert rows[1]["default_label"] == "L"


def test_describe_shows_the_edited_label_and_the_default_beside_it():
    rows = {r["id"]: r for r in layout.describe(CATALOG, [{"id": "a", "label": "Mine"}])}
    assert rows["a"]["label"] == "Mine" and rows["a"]["default_label"] == "L"


def test_describe_reports_an_unset_label_as_blank_not_as_the_default():
    """The editor binds its input to `label` and placeholders it with
    `default_label`. Reporting the effective label would refill every blank
    input, and the next save would pin all thirty sections to labels the reader
    never typed -- surviving a release that renames one."""
    rows = {r["id"]: r for r in layout.describe(CATALOG, [{"id": "a", "label": "Mine"}])}
    assert rows["b"]["label"] == "" and rows["b"]["default_label"] == "L"
    assert all(r["label"] == "" for r in layout.describe(CATALOG, []))


def test_describe_survives_a_round_trip_without_pinning_labels():
    """save -> reload -> save must not turn blanks into explicit labels."""
    first = layout.describe(CATALOG, [])
    stored = layout.sanitize([{"id": r["id"], "label": r["label"], "enabled": r["enabled"]}
                              for r in first])
    assert all(e["label"] == "" for e in stored)
    assert layout.describe(CATALOG, stored) == first


def test_describe_lists_every_catalog_section_exactly_once():
    rows = layout.describe(CATALOG, [{"id": "d"}, {"id": "b", "enabled": False}])
    assert sorted(r["id"] for r in rows) == ["a", "b", "c", "d"]


def test_describe_of_an_empty_layout_is_the_catalog():
    rows = layout.describe(CATALOG, [])
    assert [r["id"] for r in rows] == ["a", "b", "c", "d"]
    assert all(r["enabled"] for r in rows)


# ------------------------------------------------------------- the sanitizer

def test_sanitize_normalizes_shape_and_drops_junk():
    out = layout.sanitize([{"id": "a", "label": "  Mine  ", "enabled": False},
                           "junk", {"no_id": 1}, {"id": ""}, {"id": "a"}])
    assert out == [{"id": "a", "label": "Mine", "enabled": False}]


def test_sanitize_keeps_an_id_the_catalog_does_not_know():
    """Not checked against the catalog on purpose: saving from a build one
    version behind must not delete the newer build's sections from the file."""
    assert layout.sanitize([{"id": "from_the_future"}]) == [
        {"id": "from_the_future", "label": "", "enabled": True}]


def test_sanitize_tolerates_none():
    assert layout.sanitize(None) == []


# ------------------------------------------------------ the file and toggle

def test_apply_is_the_catalog_while_the_toggle_is_off(store_home):
    layout.write_layout([{"id": "d"}, {"id": "a"}])
    assert [s.id for s in layout.apply(CATALOG)] == ["a", "b", "c", "d"]


def test_apply_honours_the_layout_once_the_toggle_is_on(store_home):
    layout.write_layout([{"id": "d"}, {"id": "a"}])
    config.write_config(prompt_layout_enabled="on")
    assert [s.id for s in layout.apply(CATALOG)][:2] == ["d", "a"]


def test_the_toggle_bypasses_without_deleting(store_home):
    """Off is a bypass, so a reader can A/B their ordering against the default
    without rebuilding it."""
    layout.write_layout([{"id": "d"}])
    config.write_config(prompt_layout_enabled="on")
    config.write_config(prompt_layout_enabled="off")
    assert layout.read_layout() == [{"id": "d", "label": "", "enabled": True}]


def test_a_truncated_file_reads_as_no_layout(store_home):
    (store_home / "prompt_layout.json").write_text('{"sections": [', encoding="utf-8")
    assert layout.read_layout() == []
    config.write_config(prompt_layout_enabled="on")
    assert [s.id for s in layout.apply(CATALOG)] == ["a", "b", "c", "d"]


def test_a_wrong_shaped_file_reads_as_no_layout(store_home):
    (store_home / "prompt_layout.json").write_text('["a", "b"]', encoding="utf-8")
    assert layout.read_layout() == []


def test_a_file_whose_sections_are_not_a_list_reads_as_no_layout(store_home):
    (store_home / "prompt_layout.json").write_text('{"sections": 7}', encoding="utf-8")
    assert layout.read_layout() == []


def test_a_missing_file_reads_as_no_layout(store_home):
    assert layout.read_layout() == []


def test_write_round_trips_through_sanitize(store_home):
    layout.write_layout([{"id": "a", "label": "  Mine  ", "enabled": False},
                         "junk", {"id": "a"}])
    assert layout.read_layout() == [{"id": "a", "label": "Mine", "enabled": False}]


def test_write_replaces_rather_than_merges(store_home):
    layout.write_layout([{"id": "a"}, {"id": "b"}])
    layout.write_layout([{"id": "c"}])
    assert [e["id"] for e in layout.read_layout()] == ["c"]


def test_an_empty_write_clears_the_layout(store_home):
    """What Reset sends."""
    layout.write_layout([{"id": "d"}])
    layout.write_layout([])
    assert layout.read_layout() == []
    config.write_config(prompt_layout_enabled="on")
    assert [s.id for s in layout.apply(CATALOG)] == ["a", "b", "c", "d"]


def test_describe_and_merge_agree_about_a_repeated_id():
    """Only a hand-edited file can carry one -- `sanitize` dedupes on the way
    in -- which is exactly the file with no other protection. Both functions
    must read the FIRST entry, or the editor shows one label and the prompt
    renders another."""
    stored = [{"id": "a", "label": "First"}, {"id": "a", "label": "Second"}]
    assert {s.id: s.label for s in layout.merge(CATALOG, stored)}["a"] == "First"
    assert {r["id"]: r["label"] for r in layout.describe(CATALOG, stored)}["a"] == "First"


# ---- the message_examples -> voice_examples rename (voice sections) ----
def test_a_malformed_layout_still_merges_as_empty():
    """`_migrate` runs ahead of `_ordered`, so it owns the promise that a
    malformed file merges as empty rather than raising."""
    for bad in (None, 17, "nonsense", {}):
        assert [s.id for s in layout.merge(assemble.SECTIONS, bad)] == \
            [s.id for s in assemble.SECTIONS]


def test_a_disabled_message_examples_stays_disabled_as_voice_examples():
    merged = layout.merge(assemble.SECTIONS, [{"id": "message_examples", "enabled": False}])
    assert not any(s.id == "voice_examples" for s in merged)


def test_the_migrated_entry_keeps_its_position():
    stored = [{"id": "message_examples"}, {"id": "character_descriptions"}]
    order = [s.id for s in layout.merge(assemble.SECTIONS, stored)]
    assert order.index("voice_examples") < order.index("character_descriptions")


def test_the_legacy_label_is_dropped():
    merged = layout.merge(assemble.SECTIONS,
                          [{"id": "message_examples", "label": "My examples"}])
    sec = next(s for s in merged if s.id == "voice_examples")
    assert sec.label == "Voice · example dialogue"


def test_a_newer_voice_examples_entry_wins_over_the_legacy_one():
    stored = [{"id": "message_examples", "enabled": False}, {"id": "voice_examples"}]
    merged = layout.merge(assemble.SECTIONS, stored)
    assert any(s.id == "voice_examples" for s in merged)


def test_describe_and_merge_agree_about_the_migrated_entry():
    """Same migration on both paths, or the editor reverses the disable on save."""
    rows = {r["id"]: r for r in
            layout.describe(assemble.SECTIONS, [{"id": "message_examples", "enabled": False}])}
    assert rows["voice_examples"]["enabled"] is False


def test_the_three_newcomers_land_after_character_descriptions():
    order = [s.id for s in layout.merge(assemble.SECTIONS, [])]
    i = order.index("character_descriptions")
    assert order[i + 1:i + 4] == ["voice_policy", "voice_anchors", "voice_examples"]


def test_the_newcomers_follow_a_disabled_predecessor():
    """`_ordered`'s anchor search reads the disabled entries too, by design."""
    stored = [{"id": "plot_threads"}, {"id": "character_descriptions", "enabled": False}]
    order = [row["id"] for row in layout.describe(assemble.SECTIONS, stored)]
    i = order.index("character_descriptions")
    assert order[i + 1:i + 4] == ["voice_policy", "voice_anchors", "voice_examples"]
