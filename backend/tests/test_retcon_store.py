"""Retcon: rewrite a past post, and say what the rewrite contradicts (#78) —
`store/retcon.py`.

Two things under test and they fail differently. The rewrite is a state change
— the post, plus everything the scene's absorb wrote coming back out — and is
checked by reading the store afterwards. The contradiction pass produces no
state at all: it is a claim about who wrote a value, and the tests that matter
are the ones where it declines to make one.
"""

import pytest

from grimoire.store import (campaigns, changes, chronicle, commitments, entities,
                            plot, provenance, retcon, scenes, turnstate, worlds)
from grimoire.store.scenes import serialize as scenes_serialize


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


def _lore_edit(before, after, eid="pact"):
    """A lore row as `materialize` stages it: `after` is `before` plus an
    appended paragraph, which is why this kind is never flagged."""
    return {"id": f"lore:{eid}", "kind": "lore", "target": {"kind": "lore", "id": eid},
            "label": "The Pact — lore", "field": "body",
            "before": before, "after": after, "authored": False}


def _state_edit(before, after, char="seraphine"):
    """A character-state row: the kind whose apply REPLACES the stored body, so
    `before` and `after` are two answers to one question."""
    return {"id": f"character_state:{char}", "kind": "character_state",
            "target": {"kind": "characters", "id": char},
            "label": "Seraphine — current state", "field": "current_state",
            "before": before, "after": after, "authored": False}


# --- the rewrite -----------------------------------------------------------


def test_the_post_is_rewritten(cid, sid):
    retcon.retcon(cid, sid, 1, "she never said it")
    assert _contents(cid, sid) == ["post 0", "she never said it", "post 2", "post 3"]


def test_an_index_outside_the_transcript_is_refused(cid, sid):
    for index in (4, 99, -1):
        with pytest.raises(IndexError):
            retcon.retcon(cid, sid, index, "nope")
    assert _contents(cid, sid) == ["post 0", "post 1", "post 2", "post 3"]


def test_unknown_scene_raises(cid):
    with pytest.raises(scenes.SceneNotFound):
        retcon.retcon(cid, "no-such-scene", 0, "text")


def test_a_dice_roll_line_is_still_immutable(cid, sid):
    """The transcript line has to stay in lockstep with an immutable
    `rolls.json` entry, and a retcon is an edit like any other."""
    scenes.append_message(cid, sid, "assistant", "3d6 → 11",
                          speaker=scenes_serialize.ROLL_SPEAKER)
    with pytest.raises(scenes.RollMessageImmutable):
        retcon.retcon(cid, sid, 4, "3d6 → 18")


def test_the_transient_state_ledger_is_retired_from_the_edit(cid, sid):
    """Rewriting a furious exchange as a calm one leaves the recorded mood at a
    perfectly valid index, so nothing but this would ever drop it."""
    turnstate.record(cid, sid, 1, {"seraphine": {"mood": "furious"}})
    turnstate.record(cid, sid, 3, {"seraphine": {"mood": "calm"}})
    retcon.retcon(cid, sid, 2, "calmer words")
    assert [i for i, _ in turnstate.entries(cid, sid)] == [1]


@pytest.fixture
def absorbed(cid, sid):
    """A scene that has been absorbed: one lore write-back, a chronicle record,
    a plot beat, and the panels that describe them. The same shape
    `test_cascade_store.py` builds, because a retcon reverses exactly what a cut
    reverses."""
    from grimoire.store import absorb
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "new body")], sid)
    chronicle.absorb(cid, {"id": sid, "one_line": "They swore.", "summary": "A long night.",
                           "keywords": [], "cast": [], "location": "", "date": ""})
    plot.set_movement(cid, "the-oath", "The Oath", "open", "They swore it.", sid)
    scenes.mark_absorbed(cid, sid, "They swore.", "A long night.")
    return sid


def _lore_body(cid):
    return entities.read_entity(campaigns.campaign_root(cid), "lore", "pact")["body"]


def test_an_absorbed_scene_gives_back_what_it_wrote(cid, absorbed):
    assert _lore_body(cid) == "new body"
    report = retcon.retcon(cid, absorbed, 1, "she never said it")
    assert report["was_absorbed"] is True
    assert report["records"] == 1 and report["refused"] == []
    assert _lore_body(cid) == "old body"
    assert chronicle.read_chronicle(cid) == {}
    assert plot.get(cid, "the-oath")["beats"] == []


def test_the_scene_can_be_absorbed_again(cid, absorbed):
    """The whole point of reversing: `done` is what refuses a second absorb, and
    a re-extraction over the edited transcript is the next step of the flow."""
    retcon.retcon(cid, absorbed, 1, "she never said it")
    meta = scenes.read_scene(cid, absorbed)["meta"]
    assert "done" not in meta and "summary" not in meta


def test_the_transcript_keeps_every_other_post(cid, absorbed):
    """A retcon is not a cut. Everything after the edited post stands — which is
    exactly why a re-extraction can contradict a later scene."""
    retcon.retcon(cid, absorbed, 1, "she never said it")
    assert len(_contents(cid, absorbed)) == 4


def test_an_unabsorbed_scene_reverts_nothing(cid, sid):
    report = retcon.retcon(cid, sid, 1, "different words")
    assert report["was_absorbed"] is False
    assert (report["records"], report["plot_beats"], report["changes"]) == (0, 0, 0)
    assert report["failed"] == []


# --- play order ------------------------------------------------------------


def test_later_scenes_are_the_ones_played_after_this_one(cid, sid):
    second = scenes.create_scene(cid, "The Long Quay")
    third = scenes.create_scene(cid, "Low Water")
    assert retcon.later_scenes(cid, sid) == {second, third}
    assert retcon.later_scenes(cid, third) == set()


def test_a_scene_outside_the_id_grammar_is_not_ordered_by_number(cid, sid):
    """A legacy id has no number to compare, so the pair falls back to `created`
    — and a scene with neither is left unordered rather than guessed at."""
    root = campaigns.campaign_root(cid)
    legacy = root / "scenes" / "2026-01-02-the-long-quay.md"
    legacy.write_text("---\ntitle: The Long Quay\n---\n", encoding="utf-8")
    assert retcon.later_scenes(cid, sid) == set()
    assert retcon.later_scenes(cid, "2026-01-02-the-long-quay") == set()


# --- the contradiction pass ------------------------------------------------


@pytest.fixture
def two_scenes(cid):
    first = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, first, "user", "post 0")
    second = scenes.create_scene(cid, "The Long Quay")
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Pact", body="stored body")
    return first, second


def test_a_row_a_later_scene_already_answered_is_flagged(cid, two_scenes):
    first, second = two_scenes
    # The row `absorb.apply` files for a write-back, keyed the way it keys it.
    changes.record(cid, second, {"characters/seraphine": [
        {"field": "current_state", "label": "Seraphine — current state",
         "before": "Wary.", "after": "Loyal now."}]})

    rows = retcon.contradictions(cid, first, [_state_edit("Loyal now.", "Still wary.")])
    assert [(r["id"], r["scene"], r["source"]) for r in rows] == [
        ("character_state:seraphine", second, "changes")]


def test_a_lore_row_is_never_flagged(cid, two_scenes):
    """A lore edit APPENDS: its `after` is its `before` plus a paragraph, so it
    differs from what is stored every time. Appending is not disagreeing, and no
    string comparison can tell whether the paragraph an older scene wants to add
    contradicts one a later scene already added."""
    first, second = two_scenes
    changes.record(cid, second, {"lore/pact": [
        {"field": "body", "label": "The Pact — lore",
         "before": "stored body", "after": "stored body\n\nthe later reading"}]})
    assert retcon.contradictions(
        cid, first, [_lore_edit("stored body", "stored body\n\nan older reading")]) == []


def test_a_citation_names_the_scene_where_a_change_log_row_cannot(cid, two_scenes):
    """Provenance is per FIELD and reaches kinds `changes.json` never held — a
    relationship, a fact, a plot row — so it is consulted first."""
    first, second = two_scenes
    provenance.record(cid, {"characters/seraphine#current_state": {
        "quote": "she named it", "speaker": "Mara", "certainty": 0.9,
        "authority": "witness", "band": "likely", "scene": second, "recorded": ""}})
    rows = retcon.contradictions(cid, first, [_state_edit("Loyal now.", "Still wary.")])
    assert [(r["scene"], r["source"]) for r in rows] == [(second, "citation")]


def _plot_edit(status, beat="Nobody swore it.", pid="the-oath"):
    """One plot row in the shape `materialize` stages it: `before` is a RENDERING
    (status plus last beat) and `after` is the new beat alone, so the two are
    never in the same format — which is why the disagreement this pass looks for
    is the status in the payload."""
    return {"id": f"plot:{pid}", "kind": "plot", "target": {"kind": "plot", "id": pid},
            "field": "beat", "before": "open — They swore it.", "after": beat,
            "payload": {"id": pid, "title": "The Oath", "status": status, "scene": ""}}


def test_a_plot_thread_is_attributed_by_its_last_scene(cid, two_scenes):
    first, second = two_scenes
    plot.set_movement(cid, "the-oath", "The Oath", "open", "They swore it.", second)
    rows = retcon.contradictions(cid, first, [_plot_edit("closed")])
    assert [(r["scene"], r["source"]) for r in rows] == [(second, "thread")]


def test_a_plot_row_that_only_adds_a_beat_is_not_a_contradiction(cid, two_scenes):
    """A plot row's `before` is a rendering and its `after` is a bare beat, so a
    text comparison calls every row changed — and after a retcon of an old scene
    that is most of them. Beats accumulate; a thread left where it was is not a
    later scene disagreeing."""
    first, second = two_scenes
    plot.set_movement(cid, "the-oath", "The Oath", "open", "They swore it.", second)
    assert retcon.contradictions(cid, first, [_plot_edit("open")]) == []


def test_a_row_that_agrees_with_the_record_contradicts_nobody(cid, two_scenes):
    first, second = two_scenes
    changes.record(cid, second, {"characters/seraphine": [
        {"field": "current_state", "label": "Seraphine — current state",
         "before": "Wary.", "after": "Loyal now."}]})
    assert retcon.contradictions(cid, first, [_state_edit("Loyal now.", "Loyal now.")]) == []


def test_an_earlier_scenes_write_is_not_a_contradiction(cid, two_scenes):
    """The badge means "a scene played AFTER this one already answered
    differently". A value this scene or an earlier one wrote is just the record."""
    first, second = two_scenes
    changes.record(cid, first, {"characters/seraphine": [
        {"field": "current_state", "label": "Seraphine — current state",
         "before": "Wary.", "after": "An earlier reading."}]})
    assert retcon.contradictions(
        cid, second, [_state_edit("An earlier reading.", "Something else.")]) == []


def test_a_row_with_no_attribution_at_all_is_left_alone(cid, two_scenes):
    """Nothing on file says who wrote the stored value, so nothing here will
    guess. Not evidence of agreement — evidence of silence."""
    first, _ = two_scenes
    assert retcon.contradictions(cid, first, [_state_edit("Loyal now.", "Still wary.")]) == []


def test_the_newest_scene_has_nothing_to_contradict(cid, two_scenes):
    first, second = two_scenes
    changes.record(cid, second, {"characters/seraphine": [
        {"field": "current_state", "label": "Seraphine — current state",
         "before": "Wary.", "after": "Loyal now."}]})
    assert retcon.contradictions(cid, second, [_state_edit("x", "y")]) == []


def test_the_badge_carries_the_later_scenes_one_line(cid, two_scenes):
    first, second = two_scenes
    changes.record(cid, second, {"characters/seraphine": [
        {"field": "current_state", "label": "Seraphine — current state",
         "before": "Wary.", "after": "Loyal now."}]})
    chronicle.absorb(cid, {"id": second, "one_line": "The quay burned.", "summary": "",
                           "keywords": [], "cast": [], "location": "", "date": ""})
    rows = retcon.contradictions(cid, first, [_state_edit("Loyal now.", "Still wary.")])
    assert rows[0]["label"] == "The quay burned."


def test_a_fact_row_is_never_flagged(cid, two_scenes):
    """`fact_line` renders a fingerprint into `before` that `after` was never in
    the format of. Comparing those manufactures a disagreement out of a
    formatting difference, which is the one thing a badge must not do."""
    first, second = two_scenes
    provenance.record(cid, {"facts/f1#text": {"quote": "she said so", "speaker": "Mara",
                                             "certainty": 0.9, "authority": "witness",
                                             "band": "likely", "scene": second,
                                             "recorded": ""}})
    edit = {"id": "fact:f1", "kind": "fact", "target": {"kind": "facts", "id": "f1"},
            "field": "text", "before": "active — the tide turned", "after": "the tide held"}
    assert retcon.contradictions(cid, first, [edit]) == []


def test_a_malformed_row_is_skipped_rather_than_raising(cid, two_scenes):
    """These come off a client PUT body, which validates each edit only as "a
    dict" — the same boundary `absorb/conflicts.py` keeps."""
    first, _ = two_scenes
    assert retcon.contradictions(cid, first, ["not a dict", {}, {"target": "nope"}]) == []


def test_a_commitment_is_attributed_like_a_plot_thread(cid, two_scenes):
    first, second = two_scenes
    commitments.set_movement(cid, "the-debt", "The Debt", "promise", "open", None,
                             "She owes a favour.", second)
    edit = {"id": "commitment:the-debt", "kind": "commitment",
            "target": {"kind": "commitments", "id": "the-debt"}, "field": "beat",
            "before": "promise · open — She owes a favour.", "after": "The debt was never owed.",
            "payload": {"id": "the-debt", "title": "The Debt", "kind": "promise",
                        "status": "broken", "scene": ""}}
    rows = retcon.contradictions(cid, first, [edit])
    assert [(r["scene"], r["source"]) for r in rows] == [(second, "thread")]


def test_the_retcon_report_names_the_scenes_a_re_extraction_can_contradict(cid, sid):
    second = scenes.create_scene(cid, "The Long Quay")
    assert retcon.retcon(cid, sid, 0, "different")["later"] == [second]


def test_a_hand_mangled_attribution_file_costs_the_badges_not_the_review(cid, two_scenes):
    """Every store the pass reads is a file the user owns. One bad row must
    mean "no attribution" — the review it would otherwise take down is several
    model calls old by the time this runs."""
    first, _ = two_scenes
    (campaigns.campaign_root(cid) / "changes.json").write_text(
        '{"characters/seraphine": "not a row"}', encoding="utf-8")
    (campaigns.campaign_root(cid) / "provenance.json").write_text(
        '{"characters/seraphine#current_state": ["also not a row"]}', encoding="utf-8")
    assert retcon.contradictions(cid, first, [_state_edit("Loyal now.", "Still wary.")]) == []


def test_an_open_review_cannot_save_over_a_retcon(cid, sid):
    """A review prepared from the pre-retcon transcript is still holding a valid
    commit token, and saving it would write that transcript's summary and edits
    straight back over the reversal. The same fence a cut raises, for the same
    reason."""
    from grimoire.store import commits
    before = commits.scene_epoch(cid, sid)
    retcon.retcon(cid, sid, 1, "she never said it")
    assert commits.scene_epoch(cid, sid) > before
