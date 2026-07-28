import json

from grimoire.store import calendars, campaigns, worlds
from grimoire.store.weather import overrides

GREG = {"provider": "gregorian", "region": "US", "custom_holidays": [], "anchor": None}


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    return cid, calendars.get_provider(GREG)


def ordinal(provider, native):
    fixed = calendars.fixed_of(provider, native)
    return overrides.ordinal_of(list(overrides.blocks.block_of(fixed, calendars.minutes_of(native))))


def write_raw(cid, data):
    overrides.path(cid).write_text(json.dumps(data), encoding="utf-8")


# ---- spans and matching ----

def test_a_span_covers_its_own_range_and_not_beyond(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-16",
                  {"condition": "blizzard"})
    data = overrides.read(cid)
    assert overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-16T09:00"), "condition")
    assert not overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-17T09:00"), "condition")


def test_a_date_only_span_covers_exactly_the_five_blocks_that_date_owns(monkeypatch, tmp_path):
    # from: D is D's dawn, to: D is one past D's night. Stating it in minutes
    # would expand backwards into D-1's night and forwards into D+1.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-14", {"condition": "fog"})
    data = overrides.read(cid)
    covered = [overrides.winner(data, "saltmarch-docks", ordinal(p, t), "condition") is not None
               for t in ("2026-06-13T22:00", "2026-06-14T05:00", "2026-06-14T09:00",
                         "2026-06-14T23:00", "2026-06-15T01:00", "2026-06-15T06:00")]
    # D-1 night excluded; D's five blocks included, and D's night runs past
    # midnight into D+1 01:00; D+1's own dawn excluded.
    assert covered == [False, True, True, True, True, False]


def test_a_night_block_is_covered_identically_either_side_of_midnight(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14T21:00", None, {"condition": "storm"})
    data = overrides.read(cid)
    late = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T23:00"), "condition")
    early = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-15T01:00"), "condition")
    assert late is not None and early is not None


def test_an_open_ended_span_has_no_upper_bound(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "storm"})
    data = overrides.read(cid)
    assert overrides.winner(data, "saltmarch-docks", ordinal(p, "2031-01-01T09:00"), "condition")


# ---- per-axis application ----

def test_an_override_pins_one_axis_and_leaves_the_others(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "rain"})
    data = overrides.read(cid)
    o = ordinal(p, "2026-06-14T09:00")
    assert overrides.winner(data, "saltmarch-docks", o, "condition")[1]["condition"] == "rain"
    assert overrides.winner(data, "saltmarch-docks", o, "wind") is None


def test_suppress_forces_an_axis_back_to_procedural(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "rain"})
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {}, suppress=["condition"])
    data = overrides.read(cid)
    kind, _ = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert kind == "suppress"


# ---- precedence ----

def test_manual_beats_extractor(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "drizzle"},
                  source="extractor")
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "blizzard"},
                  source="manual")
    data = overrides.read(cid)
    got = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "blizzard"


def test_an_older_manual_still_beats_a_newer_extractor(monkeypatch, tmp_path):
    # Rank dominates recency: source order is the outer rule.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "blizzard"},
                  source="manual")
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "drizzle"},
                  source="extractor")
    data = overrides.read(cid)
    got = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "blizzard"


def test_a_location_span_beats_the_campaign_default(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "clear"})
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "fog"})
    data = overrides.read(cid)
    got = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "fog"


def test_the_default_still_applies_where_no_location_span_does(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "clear"})
    data = overrides.read(cid)
    got = overrides.winner(data, "elsewhere", ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "clear"


def test_recency_uses_seq_not_set_at(monkeypatch, tmp_path):
    # now_iso() formats to whole seconds, so two writes in one second tie on
    # set_at and would fall through to the id backstop — which can hand the
    # argument to the earlier instruction.
    cid, p = setup(monkeypatch, tmp_path)
    write_raw(cid, {"saltmarch-docks": [
        {"id": "zzz-older", "from": "2026-06-14", "to": None,
         "from_fixed": [calendars.fixed_of(p, "2026-06-14"), 0], "to_fixed": None,
         "condition": "old", "source": "manual", "seq": 1, "set_at": "2026-07-27T18:00:00Z"},
        {"id": "aaa-newer", "from": "2026-06-14", "to": None,
         "from_fixed": [calendars.fixed_of(p, "2026-06-14"), 0], "to_fixed": None,
         "condition": "new", "source": "manual", "seq": 2, "set_at": "2026-07-27T18:00:00Z"},
    ]})
    data = overrides.read(cid)
    got = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "new"


def test_array_order_is_never_the_tiebreak(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    base = {"from": "2026-06-14", "to": None,
            "from_fixed": [calendars.fixed_of(p, "2026-06-14"), 0], "to_fixed": None,
            "source": "manual", "seq": 0, "set_at": "2026-07-27T18:00:00Z"}
    write_raw(cid, {"saltmarch-docks": [
        {**base, "id": "bbb", "tiebreak": "bbb", "condition": "b"},
        {**base, "id": "aaa", "tiebreak": "aaa", "condition": "a"},
    ]})
    first = overrides.winner(overrides.read(cid), "saltmarch-docks",
                             ordinal(p, "2026-06-14T09:00"), "condition")[1]["condition"]
    write_raw(cid, {"saltmarch-docks": [
        {**base, "id": "aaa", "tiebreak": "aaa", "condition": "a"},
        {**base, "id": "bbb", "tiebreak": "bbb", "condition": "b"},
    ]})
    second = overrides.winner(overrides.read(cid), "saltmarch-docks",
                              ordinal(p, "2026-06-14T09:00"), "condition")[1]["condition"]
    assert first == second == "b"  # lexicographically greatest tiebreak


# ---- load repair ----

def test_a_missing_id_is_generated_and_written_back(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    write_raw(cid, {"saltmarch-docks": [
        {"from": "2026-06-14", "to": None, "from_fixed": [1, 0], "to_fixed": None,
         "condition": "fog", "source": "manual"}]})
    got = overrides.read(cid)["saltmarch-docks"][0]
    assert got["id"] and got["tiebreak"] == got["id"] and got["seq"] == 0
    # Derived exactly once: the id is now a stored fact.
    on_disk = json.loads(overrides.path(cid).read_text())["saltmarch-docks"][0]
    assert on_disk["id"] == got["id"]


def test_an_unaddressable_id_is_replaced(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    for bad in ("a/b", "..", ".", "has space"):
        write_raw(cid, {"saltmarch-docks": [
            {"id": bad, "from": "2026-06-14", "to": None, "from_fixed": [1, 0],
             "to_fixed": None, "condition": "fog", "source": "manual"}]})
        assert overrides.read(cid)["saltmarch-docks"][0]["id"] != bad, bad


def test_colliding_explicit_ids_are_both_re_derived(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    write_raw(cid, {"saltmarch-docks": [
        {"id": "same", "from": "2026-06-14", "to": None, "from_fixed": [1, 0],
         "to_fixed": None, "condition": "fog", "source": "manual"},
        {"id": "same", "from": "2026-06-15", "to": None, "from_fixed": [2, 0],
         "to_fixed": None, "condition": "rain", "source": "manual"},
    ]})
    ids = [r["id"] for r in overrides.read(cid)["saltmarch-docks"]]
    assert len(set(ids)) == 2 and "same" not in ids


def test_records_identical_in_every_field_are_deduplicated(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    row = {"from": "2026-06-14", "to": None, "from_fixed": [1, 0], "to_fixed": None,
           "condition": "fog", "source": "manual", "seq": 0, "set_at": "2026-07-27T18:00:00Z"}
    write_raw(cid, {"saltmarch-docks": [dict(row), dict(row)]})
    assert len(overrides.read(cid)["saltmarch-docks"]) == 1


def test_a_broken_file_reads_as_empty_rather_than_raising(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    for junk in ("{not json", "[]", "7", '{"saltmarch-docks": 3}'):
        overrides.path(cid).write_text(junk, encoding="utf-8")
        assert isinstance(overrides.read(cid), dict)


def test_a_legacy_record_without_seq_loses_to_one_written_afterwards(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    write_raw(cid, {"saltmarch-docks": [
        {"id": "legacy", "from": "2026-06-14", "to": None,
         "from_fixed": [calendars.fixed_of(p, "2026-06-14"), 0], "to_fixed": None,
         "condition": "old", "source": "manual"}]})
    overrides.read(cid)  # repair pass
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "new"})
    got = overrides.winner(overrides.read(cid), "saltmarch-docks",
                           ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "new"


# ---- clear and delete ----

def test_clearing_the_middle_of_a_bounded_span_leaves_both_ends(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-10", "2026-06-20", {"condition": "storm"})
    assert overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-15") == 1
    data = overrides.read(cid)
    at = lambda t: overrides.winner(data, "saltmarch-docks", ordinal(p, t), "condition")
    assert at("2026-06-12T09:00") is not None
    assert at("2026-06-14T09:00") is None
    assert at("2026-06-18T09:00") is not None


def test_clearing_an_open_ended_span_truncates_and_does_not_resume(monkeypatch, tmp_path):
    # Splitting would leave a fresh open-ended fragment starting a block later,
    # so the storm would resume immediately and run forever.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-10", None, {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-15", "2026-06-15")
    data = overrides.read(cid)
    at = lambda t: overrides.winner(data, "saltmarch-docks", ordinal(p, t), "condition")
    assert at("2026-06-12T09:00") is not None   # history is not retracted
    assert at("2026-06-15T09:00") is None
    assert at("2026-06-16T09:00") is None       # never resumes
    assert at("2027-01-01T09:00") is None


def test_clearing_preserves_the_tiebreak_of_the_span_it_cuts(monkeypatch, tmp_path):
    # Fragments are one instruction cut in two. A fresh tiebreak would let
    # clearing a range flip which override wins in a range untouched by it.
    cid, p = setup(monkeypatch, tmp_path)
    made = overrides.put(cid, p, "saltmarch-docks", "2026-06-10", "2026-06-20",
                         {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-15")
    for record in overrides.read(cid)["saltmarch-docks"]:
        assert record["tiebreak"] == made["tiebreak"]
        assert record["seq"] == made["seq"]


def test_split_fragments_get_distinct_ids(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-10", "2026-06-20", {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-15")
    ids = [r["id"] for r in overrides.read(cid)["saltmarch-docks"]]
    assert len(ids) == len(set(ids)) == 2


def test_delete_retracts_a_span_entirely(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    made = overrides.put(cid, p, "saltmarch-docks", "2026-06-10", None, {"condition": "storm"})
    assert overrides.delete(cid, made["id"]) is True
    data = overrides.read(cid)
    assert overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-12T09:00"),
                            "condition") is None


def test_deleting_an_unknown_id_reports_it(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    assert overrides.delete(cid, "nope") is False


def test_the_stack_lists_covering_spans_strongest_first(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "clear"},
                  source="extractor")
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "fog"})
    rows = overrides.stack(overrides.read(cid), "saltmarch-docks",
                           ordinal(p, "2026-06-14T09:00"))
    assert [r["condition"] for r in rows] == ["fog", "clear"]


# ---- axis-aware clear and resume ----

def test_clearing_one_axis_leaves_the_others_on_the_same_span(monkeypatch, tmp_path):
    # Removing one axis from a span that sets several must mutate that record
    # while preserving its source, note and range — not delete and recreate it.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None,
                  {"condition": "storm", "wind": "gale"}, note="the Wintertide storm")
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    data = overrides.read(cid)
    o = ordinal(p, "2026-06-14T09:00")
    assert overrides.winner(data, "saltmarch-docks", o, "condition") is None
    got = overrides.winner(data, "saltmarch-docks", o, "wind")
    assert got[1]["wind"] == "gale"
    assert got[1]["note"] == "the Wintertide storm"


def test_a_span_left_setting_nothing_is_dropped(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    assert overrides.read(cid).get("saltmarch-docks", []) == []


def test_clearing_at_a_location_suppresses_an_inherited_default(monkeypatch, tmp_path):
    # Truncating the _default span would clear every other location too;
    # skipping it would leave the docks overridden and the button ineffective.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    data = overrides.read(cid)
    o = ordinal(p, "2026-06-14T09:00")
    kind, _ = overrides.winner(data, "saltmarch-docks", o, "condition")
    assert kind == "suppress"
    # Everywhere else still inherits it.
    assert overrides.winner(data, "lighthouse", o, "condition")[1]["condition"] == "storm"


def test_suppression_terminates_resolution_downward(monkeypatch, tmp_path):
    # Otherwise a local suppression merely shadows the manual _default and
    # exposes the extractor one beneath it — shadow-promotion one rank down.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "drizzle"},
                  source="extractor")
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    kind, _ = overrides.winner(overrides.read(cid), "saltmarch-docks",
                               ordinal(p, "2026-06-14T09:00"), "condition")
    assert kind == "suppress"


def test_a_newer_local_override_beats_an_earlier_suppression(monkeypatch, tmp_path):
    # A GM who clears an inherited storm and later pins a local drizzle expects
    # the drizzle; an unconditional "suppressed means procedural" would make
    # the second instruction vanish.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "drizzle"})
    got = overrides.winner(overrides.read(cid), "saltmarch-docks",
                           ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[0] == "set" and got[1]["condition"] == "drizzle"


def test_resume_restores_one_axis_and_leaves_the_rest_suppressed(monkeypatch, tmp_path):
    # A single span routinely names several axes, since clearing all three at
    # once produces exactly that; deleting it would restore inheritance for
    # every axis the user meant to keep suppressed.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None,
                  {"condition": "storm", "wind": "gale"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None)
    overrides.resume(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["wind"])
    data = overrides.read(cid)
    o = ordinal(p, "2026-06-14T09:00")
    assert overrides.winner(data, "saltmarch-docks", o, "wind")[1]["wind"] == "gale"
    assert overrides.winner(data, "saltmarch-docks", o, "condition")[0] == "suppress"


def test_a_suppression_naming_nothing_is_dropped(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    overrides.resume(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["condition"])
    assert overrides.read(cid).get("saltmarch-docks", []) == []
    assert overrides.winner(overrides.read(cid), "saltmarch-docks",
                            ordinal(p, "2026-06-14T09:00"), "condition")[1]["condition"] == "storm"


def test_a_split_keeps_the_original_id_on_the_earlier_fragment(monkeypatch, tmp_path):
    # The client holds ids from the response it is acting on, so regenerating
    # both would invalidate one it was just handed.
    cid, p = setup(monkeypatch, tmp_path)
    made = overrides.put(cid, p, "saltmarch-docks", "2026-06-10", "2026-06-20",
                         {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-15")
    records = sorted(overrides.read(cid)["saltmarch-docks"],
                     key=lambda r: overrides.ordinal_of(r["from_fixed"]))
    assert records[0]["id"] == made["id"]
    assert records[1]["id"] != made["id"]


# ---- from Codex review of #232 ----

def test_a_timed_end_inside_a_block_rounds_up(monkeypatch, tmp_path):
    # Both endpoints resolving to the containing block makes 09:00-10:00 an
    # empty span: an override that saves cleanly and never applies.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14T09:00", "2026-06-14T10:00",
                  {"condition": "squall"})
    data = overrides.read(cid)
    assert overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert not overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T13:00"), "condition")


def test_a_timed_end_already_on_a_boundary_is_not_extended(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14T08:00", "2026-06-14T12:00",
                  {"condition": "squall"})
    data = overrides.read(cid)
    assert overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T09:00"), "condition")
    assert not overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-14T13:00"), "condition")


def test_a_non_string_source_does_not_raise_into_resolution(monkeypatch, tmp_path):
    # A hand-edited `"source": []` is valid JSON and unhashable as a dict key.
    cid, p = setup(monkeypatch, tmp_path)
    write_raw(cid, {"saltmarch-docks": [
        {"id": "odd", "from": "2026-06-14", "to": None,
         "from_fixed": [calendars.fixed_of(p, "2026-06-14"), 0], "to_fixed": None,
         "condition": "fog", "source": []}]})
    got = overrides.winner(overrides.read(cid), "saltmarch-docks",
                           ordinal(p, "2026-06-14T09:00"), "condition")
    assert got[1]["condition"] == "fog"


def test_an_unknown_axis_cannot_strip_span_metadata(monkeypatch, tmp_path):
    # `_cut` deletes each named key, so `to_fixed` would turn a bounded
    # override into an open-ended one.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-16", {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", None, axes=["to_fixed"])
    record = overrides.read(cid)["saltmarch-docks"][0]
    assert record["to_fixed"] is not None
    assert not overrides.winner(overrides.read(cid), "saltmarch-docks",
                                ordinal(p, "2026-06-20T09:00"), "condition")


def test_an_inherited_span_starting_later_in_the_range_is_suppressed(monkeypatch, tmp_path):
    # Checking coverage only at the first ordinal misses a campaign-wide storm
    # that begins tomorrow, leaving the later part of the cleared range stormy.
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-15", "2026-06-17", {"condition": "storm"})
    overrides.clear(cid, p, "saltmarch-docks", "2026-06-14", "2026-06-17", axes=["condition"])
    data = overrides.read(cid)
    kind, _ = overrides.winner(data, "saltmarch-docks", ordinal(p, "2026-06-16T09:00"), "condition")
    assert kind == "suppress"


def test_a_failed_authoring_write_raises_rather_than_reporting_success(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    import pytest
    def boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(overrides.Path, "write_text", boom)
    with pytest.raises(overrides.OverrideWriteError):
        overrides.put(cid, p, "saltmarch-docks", "2026-06-14", None, {"condition": "fog"})


def test_load_repair_still_tolerates_an_unwritable_store(monkeypatch, tmp_path):
    # The repair pass is best-effort: resolution works from what was parsed.
    cid, p = setup(monkeypatch, tmp_path)
    write_raw(cid, {"saltmarch-docks": [
        {"from": "2026-06-14", "to": None,
         "from_fixed": [calendars.fixed_of(p, "2026-06-14"), 0], "to_fixed": None,
         "condition": "fog", "source": "manual"}]})
    real = overrides.Path.write_text
    def boom(self, *a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(overrides.Path, "write_text", boom)
    got = overrides.read(cid)
    assert got["saltmarch-docks"][0]["condition"] == "fog"
    monkeypatch.setattr(overrides.Path, "write_text", real)


def test_put_ordinals_carries_a_suppression(monkeypatch, tmp_path):
    cid, p = setup(monkeypatch, tmp_path)
    overrides.put(cid, p, overrides.DEFAULT_KEY, "2026-06-14", None, {"condition": "storm"})
    start = ordinal(p, "2026-06-14T09:00")
    overrides.put_ordinals(cid, "saltmarch-docks", "2026-06-14T09:00", start, start + 1,
                           {}, suppress=["condition"])
    kind, _ = overrides.winner(overrides.read(cid), "saltmarch-docks", start, "condition")
    assert kind == "suppress"
