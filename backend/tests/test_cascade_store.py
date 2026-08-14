"""Cascade post-delete with state reversal (#75) — `store/cascade.py`.

Two halves under test, and the second is the interesting one: the cut itself
(`scenes.delete_from`), and what a scene's absorbed write-backs are rolled back
to. The invariants that matter here are the ones a reader cannot see from the
transcript afterwards — a record put back to its pre-absorb value, a refusal
reported instead of a silent overwrite, and the stores this deliberately leaves
standing.
"""

import json

import pytest

from grimoire.store import (alternates, campaigns, cascade, changes, chronicle,
                            commitments, commits, entities, journal, plot,
                            provenance, scenes, turnstate, worlds)
from grimoire.store.scenes import turns as scenes_turns


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


@pytest.fixture
def sid(cid):
    sid = scenes.create_scene(cid, "Saltmarch")
    for i in range(4):
        scenes.append_message(cid, sid, "user", f"post {i}")
    return sid


def _contents(cid, sid):
    return [m["content"] for m in scenes.read_scene(cid, sid)["messages"]]


# --- the cut ---------------------------------------------------------------


def test_cuts_the_post_and_everything_after_it(cid, sid):
    assert cascade.delete_from(cid, sid, 1)["removed"] == 3
    assert _contents(cid, sid) == ["post 0"]


def test_cutting_at_zero_empties_the_transcript(cid, sid):
    assert cascade.delete_from(cid, sid, 0)["removed"] == 4
    assert _contents(cid, sid) == []


def test_an_index_that_would_remove_nothing_is_refused(cid, sid):
    """A cut is a claim about posts that exist. Past the end it removes nothing,
    which means the caller and the store disagree about the transcript — the
    route turns this into a 400 rather than reporting a success that did not
    happen."""
    for index in (4, 99, -1):
        with pytest.raises(IndexError):
            cascade.delete_from(cid, sid, index)
    assert len(_contents(cid, sid)) == 4


def test_unknown_scene_raises(cid):
    with pytest.raises(scenes.SceneNotFound):
        cascade.delete_from(cid, "no-such-scene", 0)


def test_roll_lines_below_the_cut_go_with_it(cid, sid):
    """`edit_message` refuses to touch a dice-roll line and `trim_continuation`
    re-parks one, both protecting a line the player did not ask to lose. Here
    they did — and `rolls.json` keeps the ledger entry either way."""
    from grimoire.store.scenes import serialize as scenes_serialize
    scenes.append_message(cid, sid, "assistant", "3d6 → 11",
                          speaker=scenes_serialize.ROLL_SPEAKER)
    assert cascade.delete_from(cid, sid, 2)["removed"] == 3
    assert _contents(cid, sid) == ["post 0", "post 1"]


def test_turn_sizes_shrink_to_the_generations_that_survive(cid):
    """Boundaries describe model blocks, so a cut that takes blocks has to take
    the boundaries with them or the next reroll deletes into an older
    generation."""
    sid = scenes.create_scene(cid, "Tracked")
    scenes.append_message(cid, sid, "user", "hello")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "one"}])
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "two"},
                                   {"speaker": None, "content": "three"}])
    assert scenes_turns.get_turn_sizes(cid, sid) == [1, 2]

    cascade.delete_from(cid, sid, 2)          # takes the second generation whole
    assert scenes_turns.get_turn_sizes(cid, sid) == [1]


def test_the_transient_state_ledger_is_retired_from_the_cut(cid, sid):
    turnstate.record(cid, sid, 1, {"seraphine": {"mood": "furious"}})
    turnstate.record(cid, sid, 3, {"seraphine": {"mood": "calm"}})
    cascade.delete_from(cid, sid, 2)
    assert [i for i, _ in turnstate.entries(cid, sid)] == [1]


def _scene_with_parked_alternates(cid):
    """A scene whose trailing generation has a variant on file. The reroll
    sidecar pins a set by how many messages sit in front of the generation, so
    what matters below is whether the cut is above or below that anchor."""
    sid = scenes.create_scene(cid, "Rerolled")
    scenes.append_message(cid, sid, "user", "hello")
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "the first take"}])
    alternates.archive(cid, sid, "")
    assert alternates.state(cid, sid)["runs"], "fixture archived nothing"
    return sid


def test_parked_alternates_are_dropped_when_the_cut_takes_their_generation(cid):
    sid = _scene_with_parked_alternates(cid)
    cascade.delete_from(cid, sid, 1)          # takes the generation itself
    assert alternates.state(cid, sid)["runs"] == []
    assert not (campaigns.campaign_root(cid) / "scenes" / f"{sid}.alts.json").exists()


def test_parked_alternates_survive_a_cut_above_them(cid):
    """A cut that reaches only what sits ABOVE the archived generation leaves the
    set exactly as valid as it was — the anchor steps over trailing transition
    lines, so it has not moved and there is nothing to invalidate."""
    from grimoire.store.scenes import serialize as scenes_serialize
    sid = _scene_with_parked_alternates(cid)
    scenes.append_message(cid, sid, "assistant", "— they move to the wharf —",
                          speaker=scenes_serialize.TRANSITION_SPEAKER)
    assert alternates.state(cid, sid)["active"] == 0

    cascade.delete_from(cid, sid, 2)          # takes only the transition line
    assert alternates.state(cid, sid)["active"] == 0


def test_an_open_review_cannot_save_over_a_cut_scene(cid, sid):
    """A review prepared from the pre-cut transcript still holds a valid commit
    token. Retiring the scene's epoch is what fences it — the same call
    `delete_scene` makes, for the same reason."""
    before = commits.scene_epoch(cid, sid)
    cascade.delete_from(cid, sid, 1)
    assert commits.scene_epoch(cid, sid) > before


# --- the reversal ----------------------------------------------------------


def _absorb_lore_edit(cid, sid, before, after):
    from grimoire.store import absorb
    return absorb.apply_edits(cid, [{
        "id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
        "label": "The Pact — lore", "field": "body",
        "before": before, "after": after, "authored": False,
        "review": {"quote": "she named the pact", "speaker": "Seraphine",
                   "certainty": 0.9, "authority": "witness", "band": "likely"},
    }], sid)


def _lore_body(cid):
    return entities.read_entity(campaigns.campaign_root(cid), "lore", "pact")["body"]


@pytest.fixture
def absorbed(cid, sid):
    """A scene that has been absorbed: one lore write-back, a chronicle record,
    a plot beat, a commitment beat, and the panels that describe them."""
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    _absorb_lore_edit(cid, sid, "old body", "new body")
    chronicle.absorb(cid, {"id": sid, "one_line": "They swore.", "summary": "A long night.",
                           "keywords": [], "cast": [], "location": "", "date": ""})
    plot.set_movement(cid, "the-oath", "The Oath", "open", "They swore it.", sid)
    commitments.set_movement(cid, "the-debt", "The Debt", "promise", "open", None,
                             "She owes a favour.", sid)
    scenes.mark_absorbed(cid, sid, "They swore.", "A long night.")
    return sid


def test_the_lore_body_is_put_back_to_what_it_was(cid, absorbed):
    assert _lore_body(cid) == "new body"
    report = cascade.delete_from(cid, absorbed, 1)
    assert report["records"] == 1 and report["refused"] == []
    assert _lore_body(cid) == "old body"


def test_the_scene_tagged_records_go(cid, absorbed):
    report = cascade.delete_from(cid, absorbed, 1)
    assert report["chronicle"] is True
    assert chronicle.read_chronicle(cid) == {}
    assert report["plot_beats"] == 1 and plot.get(cid, "the-oath")["beats"] == []
    assert report["commitment_beats"] == 1
    assert commitments.get(cid, "the-debt")["beats"] == []
    # The reversal re-records a `changes` row on its way past (it is a rolling
    # log of "what last moved this record") and drops the citation, so the sweep
    # finds one of the two still standing. Both are gone by the end, which is
    # what the counts are reporting on.
    assert report["changes"] == 1 and changes.read(cid) == {}
    assert report["citations"] == 0 and provenance.read(cid) == {}


def test_the_sweep_covers_what_the_journal_no_longer_can(cid, absorbed):
    """The journal is bounded — `RETENTION` rows and `MAX_BYTES` — so an old
    scene's reversals may simply have fallen off the end. The value cannot be
    put back then, and this is honest about that: the scene-tagged rows still go
    (they name the scene), the record keeps what the absorb gave it, and the
    report says nothing was reversed."""
    (campaigns.campaign_root(cid) / "journal.json").write_text(
        json.dumps({"seq": 9, "entries": []}), encoding="utf-8")

    report = cascade.delete_from(cid, absorbed, 1)
    assert report["records"] == 0 and report["refused"] == []
    assert _lore_body(cid) == "new body"
    assert report["changes"] == 1 and report["citations"] == 1
    assert changes.read(cid) == {} and provenance.read(cid) == {}


def test_the_scene_is_unabsorbed_so_it_can_be_run_again(cid, absorbed):
    report = cascade.delete_from(cid, absorbed, 1)
    assert report["was_absorbed"] is True
    meta = scenes.read_scene_meta(cid, absorbed)
    assert "done" not in meta and "one_line" not in meta and "summary" not in meta
    assert scenes.list_scenes(cid)[0]["done"] is False


def test_a_record_that_moved_since_is_refused_not_overwritten(cid, absorbed):
    """The compare-and-swap `store/undo.py` applies on the way back. A later
    hand edit means the reader asked to undo one change, not to discard
    everything since — so the value stands and the refusal is reported."""
    entities.update_entity(campaigns.campaign_root(cid), "lore", "pact",
                           body="edited by hand")
    report = cascade.delete_from(cid, absorbed, 1)
    assert report["records"] == 0
    assert [r["label"] for r in report["refused"]] == ["The Pact — lore"]
    assert _lore_body(cid) == "edited by hand"
    # The cut still happened, and the rest of the sweep still ran.
    assert _contents(cid, absorbed) == ["post 0"]
    assert chronicle.read_chronicle(cid) == {}


def test_a_record_the_scene_created_is_reported_not_deleted(cid, sid):
    """`store/undo.py` declines a creation and points at this issue for the
    cascade — and the cascade declines it too. A created lore entry may already
    be named by later scenes, and deleting it here would take those with it. So
    it stands, and the report says so in the store's own words."""
    from grimoire.store import absorb
    absorb.apply_edits(cid, [{
        "id": "new_lore:tithe", "kind": "new_lore",
        "target": {"kind": "lore", "id": ""}, "label": "The Tithe — new lore",
        "field": "body", "before": "", "after": "A yearly levy.",
        "payload": {"name": "The Tithe", "body": "A yearly levy."}, "authored": False}], sid)
    scenes.mark_absorbed(cid, sid, "They swore.", "s")
    created = list(entities.list_entities(campaigns.campaign_root(cid), "lore"))
    assert created, "fixture created no lore entry"

    report = cascade.delete_from(cid, sid, 1)
    assert report["records"] == 0 and len(report["refused"]) == 1
    assert "deleting it" in report["refused"][0]["reason"]
    assert list(entities.list_entities(campaigns.campaign_root(cid), "lore")) == created


def test_the_reversal_does_not_undo_its_own_reversals(cid, absorbed):
    """`undo.undo` appends a row for the reversal it performs, tagged with the
    same scene. Iterating live would find that row and redo the edit."""
    cascade.delete_from(cid, absorbed, 1)
    assert _lore_body(cid) == "old body"
    rows = [e for e in journal.read(cid) if e.get("source") == "undo"]
    assert len(rows) == 1 and rows[0]["scene"] == absorbed


def test_a_second_cut_reverses_nothing_twice(cid, absorbed):
    scenes.append_message(cid, absorbed, "user", "post 4")
    cascade.delete_from(cid, absorbed, 1)
    assert _lore_body(cid) == "old body"
    scenes.append_message(cid, absorbed, "user", "again")
    report = cascade.delete_from(cid, absorbed, 0)
    assert report["records"] == 0 and report["refused"] == []
    assert _lore_body(cid) == "old body"


def test_an_unabsorbed_scene_reverts_nothing(cid, sid):
    """The sweep is driven by what carries the scene's id, so a scene that never
    wrote anything needs no flag to be left alone."""
    report = cascade.delete_from(cid, sid, 1)
    assert report["was_absorbed"] is False
    assert (report["records"], report["plot_beats"], report["changes"]) == (0, 0, 0)
    assert report["chronicle"] is False


def test_another_scenes_records_are_untouched(cid, absorbed):
    """Every step matches on the scene id, so a thread two scenes both moved
    keeps the other's beat."""
    other = scenes.create_scene(cid, "Elsewhere")
    plot.set_movement(cid, "the-oath", "The Oath", "advanced", "It held.", other)
    chronicle.absorb(cid, {"id": other, "one_line": "Later.", "summary": "",
                           "keywords": [], "cast": [], "location": "", "date": ""})
    changes.record(cid, other, {"lore/tithe": [
        {"field": "body", "label": "The Tithe — lore", "before": "", "after": "levied"}]})
    provenance.record(cid, {"lore/tithe#body": {"quote": "levied yearly", "scene": other}})

    cascade.delete_from(cid, absorbed, 1)
    thread = plot.get(cid, "the-oath")
    assert [b["scene"] for b in thread["beats"]] == [other]
    # `last_scene` orders the ledger, so it may not name a scene with no beats.
    assert thread["last_scene"] == other
    assert list(chronicle.read_chronicle(cid)) == [other]
    # The two rolling display logs are swept by scene id too, and the id is what
    # scopes them — another scene's row is not this cut's to remove.
    assert list(changes.read(cid)) == ["lore/tithe"]
    assert list(provenance.read(cid)) == ["lore/tithe#body"]


def test_last_scene_falls_back_when_the_scene_it_named_is_cut(cid, absorbed):
    earlier = scenes.create_scene(cid, "Earlier")
    plot.set_movement(cid, "the-vow", "The Vow", "open", "First.", earlier)
    plot.set_movement(cid, "the-vow", "", "open", "Second.", absorbed)
    assert plot.get(cid, "the-vow")["last_scene"] == absorbed

    cascade.delete_from(cid, absorbed, 1)
    assert plot.get(cid, "the-vow")["last_scene"] == earlier


def test_a_status_only_movement_still_repairs_last_scene(cid, absorbed):
    """`set_movement` stamps `last_scene` on every call but appends a beat only
    when it was given text, so a scene that moved a thread's status without
    narrating one leaves its id there with nothing else to show for it. A repair
    gated on losing a beat walks straight past that thread — and `open_threads`
    sorts the ledger by this field."""
    earlier = scenes.create_scene(cid, "Earlier")
    plot.set_movement(cid, "the-vow", "The Vow", "open", "First.", earlier)
    plot.set_movement(cid, "the-vow", "", "advanced", "", absorbed)   # no beat text
    assert plot.get(cid, "the-vow")["last_scene"] == absorbed
    assert [b["scene"] for b in plot.get(cid, "the-vow")["beats"]] == [earlier]

    report = cascade.delete_from(cid, absorbed, 1)
    thread = plot.get(cid, "the-vow")
    assert thread["last_scene"] == earlier
    # And it contributes nothing to the beat count, which is what that number means.
    assert report["plot_beats"] == 1          # only the fixture's own beat


def test_a_thread_this_scene_only_opened_falls_back_to_no_scene(cid, absorbed):
    plot.set_movement(cid, "the-rumour", "The Rumour", "open", "", absorbed)
    cascade.delete_from(cid, absorbed, 1)
    assert plot.get(cid, "the-rumour")["last_scene"] == ""


def test_the_roll_ledger_and_the_timeline_survive_the_cut(cid, absorbed):
    """Two append-only records this deliberately does not touch: `rolls.json`
    never drops an entry, and `timeline.md` has no scene attribution to match
    on."""
    from grimoire.store import rolls
    rolls.append(cid, absorbed, "Athletics", {"notation": "3d6", "total": 11})
    chronicle.append_timeline(cid, [{"date": "day one", "text": "They swore."}])

    cascade.delete_from(cid, absorbed, 1)
    assert len(rolls.read(cid)) == 1
    timeline = (campaigns.campaign_root(cid) / "timeline.md").read_text(encoding="utf-8")
    assert "They swore." in timeline


def test_a_garbled_plot_file_costs_the_sweep_nothing_else(cid, absorbed):
    """Every store here is a hand-editable file. A record that is not a mapping
    holds nothing to match on and is stepped over, rather than raising out of a
    cut whose transcript half has already landed."""
    path = campaigns.campaign_root(cid) / "plot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["junk"] = ["not a thread"]
    path.write_text(json.dumps(data), encoding="utf-8")

    report = cascade.delete_from(cid, absorbed, 1)
    assert report["plot_beats"] == 1 and report["failed"] == []
    assert json.loads(path.read_text(encoding="utf-8"))["junk"] == ["not a thread"]


# --- the scene's own setting and clock -------------------------------------


def _two_locations(cid):
    root = campaigns.campaign_root(cid)
    entities.create_entity(root, "locations", "The Wharf", body="")
    entities.create_entity(root, "locations", "The Chapel", body="")


def test_the_scene_is_rewound_to_where_the_surviving_transcript_leaves_it(cid):
    """The last entry of `location_history` IS the scene's current setting, and
    `chronicle.scene_facts` feeds it to the absorb prompt while the context
    builder puts it in front of the model. Cutting back past the move and
    leaving the scene at the wharf prompts it somewhere its transcript never
    goes."""
    _two_locations(cid)
    sid = scenes.create_scene(cid, "Moving")
    scenes.set_location(cid, sid, "the-wharf")          # first is silent
    scenes.append_message(cid, sid, "user", "we go inland")
    scenes.set_location(cid, sid, "the-chapel")         # appends a transition line
    scenes.append_message(cid, sid, "user", "at the chapel")
    assert scenes.get_location_history(cid, sid) == ["the-wharf", "the-chapel"]

    # Cut the move itself away: index 1 is the "*The scene moves to*" line.
    cascade.delete_from(cid, sid, 1)
    assert scenes.get_location_history(cid, sid) == ["the-wharf"]


def test_a_move_the_cut_leaves_standing_stays(cid):
    _two_locations(cid)
    sid = scenes.create_scene(cid, "Moving")
    scenes.set_location(cid, sid, "the-wharf")
    scenes.append_message(cid, sid, "user", "we go inland")
    scenes.set_location(cid, sid, "the-chapel")
    scenes.append_message(cid, sid, "user", "at the chapel")

    cascade.delete_from(cid, sid, 2)          # only the post after the move
    assert scenes.get_location_history(cid, sid) == ["the-wharf", "the-chapel"]


def test_the_clock_is_rewound_the_same_way(cid):
    sid = scenes.create_scene(cid, "Waiting")
    scenes.append_message(cid, sid, "user", "we wait")
    # The FIRST date is silent and stamps itself into the filename, so the scene
    # id changes — take it back from the call rather than guessing.
    sid = scenes.set_datetime(cid, sid, "2026-08-14")["id"]
    scenes.set_datetime(cid, sid, "2026-08-15")           # appends a transition line
    assert len(scenes.get_time_history(cid, sid)) == 2

    cascade.delete_from(cid, sid, 1)                      # the advance line
    assert len(scenes.get_time_history(cid, sid)) == 1


def test_a_history_the_transcript_cannot_account_for_is_left_alone(cid):
    """The classifier reads a line's prose, so an older build's wording or a
    hand-placed transition will not tally. Validate first and let data that does
    not add up authorize nothing — trimming on a miscount would move a scene's
    setting somewhere the player never left, which is worse than the stale value
    this is fixing. Same discipline as `turns._tracked_suffix_fits`."""
    _two_locations(cid)
    sid = scenes.create_scene(cid, "Handwritten")
    scenes.set_location(cid, sid, "the-wharf")
    scenes.append_message(cid, sid, "user", "we go inland")
    scenes.set_location(cid, sid, "the-chapel")
    # Someone edits the transition line into something the classifier cannot read.
    scenes.edit_message(cid, sid, 1, "*They walk inland.*")
    assert scenes.get_location_history(cid, sid) == ["the-wharf", "the-chapel"]

    cascade.delete_from(cid, sid, 1)
    assert scenes.get_location_history(cid, sid) == ["the-wharf", "the-chapel"]


def test_a_scene_that_never_moved_keeps_its_empty_history(cid, sid):
    cascade.delete_from(cid, sid, 1)
    assert scenes.get_location_history(cid, sid) == []
    assert "location_history" not in scenes.read_scene_meta(cid, sid)


def test_an_actor_join_line_is_not_mistaken_for_a_move(cid):
    """Join and leave lines carry the same speaker and are deliberately NOT the
    scene's own moves — counting one as a move would rewind the setting by a
    step that never changed it."""
    from grimoire.store.scenes import serialize as scenes_serialize
    _two_locations(cid)
    sid = scenes.create_scene(cid, "Arrivals")
    scenes.set_location(cid, sid, "the-wharf")
    scenes.append_message(cid, sid, "user", "we wait")
    scenes.append_message(cid, sid, "assistant", "*Mara joins the scene.*",
                          speaker=scenes_serialize.TRANSITION_SPEAKER)
    scenes.set_location(cid, sid, "the-chapel")
    scenes.append_message(cid, sid, "user", "inside")

    # [0] we wait, [1] Mara joins, [2] the move, [3] inside. Cut at 2: the join
    # survives and the move does not, so the history rewinds by exactly one.
    cascade.delete_from(cid, sid, 2)
    # Counting the join as a move instead would make the lines and the entries
    # fail to account for each other, and the history would be left at the
    # chapel — which is why this asserts the trimmed value, not merely that
    # something happened.
    assert scenes.get_location_history(cid, sid) == ["the-wharf"]


# --- nothing after the cut may raise --------------------------------------


def test_unparseable_json_in_one_store_does_not_sink_the_rest(cid, absorbed):
    """`plot.read` parses with a bare `json.loads`, and plot.json is a file the
    user owns. Before this was guarded, one stray byte meant a 500 landing on
    top of an already-truncated transcript — leaving the chronicle record
    describing posts that had gone, which is precisely the state a cascade
    delete exists to prevent."""
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")

    report = cascade.delete_from(cid, absorbed, 1)
    assert _contents(cid, absorbed) == ["post 0"]
    # Named, not swallowed: the count is zero because the step could not run,
    # and only `failed` tells those two zeros apart.
    assert report["failed"] == ["plot_beats"] and report["plot_beats"] == 0
    # Everything either side of it still ran.
    assert report["chronicle"] is True and chronicle.read_chronicle(cid) == {}
    assert report["records"] == 1 and _lore_body(cid) == "old body"
    assert "done" not in scenes.read_scene_meta(cid, absorbed)


def test_a_garbled_journal_still_leaves_the_sweep_to_clean_up(cid, absorbed):
    (campaigns.campaign_root(cid) / "journal.json").write_text("[[[", encoding="utf-8")
    report = cascade.delete_from(cid, absorbed, 1)
    # `journal._load` is tolerant, so this is not even a failure — it reads as an
    # empty history, and the scene-tagged sweep is what covers the writes then.
    assert report["records"] == 0 and report["failed"] == []
    assert report["chronicle"] is True and report["changes"] == 1


def test_a_bad_index_costs_nothing_at_all_including_the_commit_epoch(cid, sid):
    """Validated before the fence, not after. Retiring the scene's epoch on a
    request that then 400s would hand an open review of an untouched scene a
    409 it has to re-absorb out of."""
    before = commits.scene_epoch(cid, sid)
    with pytest.raises(IndexError):
        cascade.delete_from(cid, sid, 99)
    assert commits.scene_epoch(cid, sid) == before
    assert len(_contents(cid, sid)) == 4


def test_a_reversal_that_blows_up_is_reported_in_the_readers_words(cid, absorbed, monkeypatch):
    """An arbitrary exception's text is a stack-level detail — a bare KeyError
    renders as a quoted field name — and this string goes into a banner. The
    traceback belongs in the log."""
    monkeypatch.setattr(cascade.undo, "undo",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("expect")))
    report = cascade.delete_from(cid, absorbed, 1)
    assert report["records"] == 0 and len(report["refused"]) == 1
    assert report["refused"][0]["reason"] == cascade.UNREVERSIBLE
    assert "KeyError" not in report["refused"][0]["reason"]
