"""The durable end-of-scene review: the record, the watermark, the merges (#396).

The store half of Phase 2. What the routes do with it -- the 202, the cancel
ordering, the refusal at save -- is `test_review_detach.py`; this is about the
file and the four decisions baked into it: what the watermark notices, what a
retry is allowed to overwrite, what a targeted clear will and will not remove,
and what a rename carries.
"""

from __future__ import annotations

import importlib
import json

import pytest

import grimoire.store as store


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return tmp_path


@pytest.fixture
def scene(home):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "The Tearoom")
    return cid, sid


def _review(token="1-" + "a" * 32, **extra):
    return {"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
            "edits": [], "mechanics": {}, "dossiers": {}, "voice": {},
            "phases": [], "commit_token": token, **extra}


# ---- the watermark ---------------------------------------------------------

def test_the_watermark_moves_when_the_transcript_does(scene):
    """Every mutation a scene has -- an append, an edit, a cut, a promoted
    alternate -- changes role, speaker or content, and each has to move it."""
    cid, sid = scene
    store.scenes.append_message(cid, sid, "user", "We entered.")
    first = store.pending_reviews.watermark(store.scenes.read_scene(cid, sid)["messages"])

    store.scenes.append_message(cid, sid, "assistant", "The door shut.")
    appended = store.pending_reviews.watermark(store.scenes.read_scene(cid, sid)["messages"])
    assert appended != first and appended["count"] == first["count"] + 1

    store.scenes.edit_message(cid, sid, 0, "We crept in.")
    edited = store.pending_reviews.watermark(store.scenes.read_scene(cid, sid)["messages"])
    # The COUNT is the same, which is the case a length-only guard would miss.
    assert edited["count"] == appended["count"] and edited != appended

    store.scenes.delete_from(cid, sid, 1)
    cut = store.pending_reviews.watermark(store.scenes.read_scene(cid, sid)["messages"])
    assert cut != edited and cut["count"] == 1


def test_the_watermark_does_not_move_for_a_scene_that_did_not(scene):
    """The counterweight, and it is the half that matters: a watermark that
    refused everything would pass every test above and make the feature a
    permanent 'the scene changed, re-run'."""
    cid, sid = scene
    store.scenes.append_message(cid, sid, "user", "We entered.")
    messages = store.scenes.read_scene(cid, sid)["messages"]
    assert store.pending_reviews.watermark(messages) == \
        store.pending_reviews.watermark(store.scenes.read_scene(cid, sid)["messages"])
    # ...and a rename, which moves the scene's ID and nothing about its text.
    new_sid = store.scenes.rename_scene(cid, sid, "The Back Room")
    assert store.pending_reviews.watermark(
        store.scenes.read_scene(cid, new_sid)["messages"]) == \
        store.pending_reviews.watermark(messages)


def test_the_watermark_tells_two_speakers_apart(scene):
    """Speaker is part of the transcript the model read, so it is part of the
    digest -- a promoted alternate that only changes who said a line is a
    different transcript."""
    a = store.pending_reviews.watermark([{"role": "assistant", "speaker": "Mara",
                                          "content": "Quiet tonight."}])
    b = store.pending_reviews.watermark([{"role": "assistant", "speaker": "Winifred",
                                          "content": "Quiet tonight."}])
    assert a != b


# ---- the record ------------------------------------------------------------

def test_a_published_review_reads_back_whole(scene):
    cid, sid = scene
    mark = {"count": 2, "digest": "abc"}
    store.pending_reviews.publish(cid, sid, "gen1", _review(), mark)
    record = store.pending_reviews.read(cid, sid)
    assert record["generation"] == "gen1" and record["watermark"] == mark
    assert record["review"]["commit_token"].endswith("a" * 32)


def test_a_fresh_publish_replaces_rather_than_merges(scene):
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen1", _review(one_line="first"), {})
    store.pending_reviews.publish(cid, sid, "gen2", _review(one_line="second"), {})
    record = store.pending_reviews.read(cid, sid)
    assert record["generation"] == "gen2" and record["review"]["one_line"] == "second"


def test_a_file_that_will_not_parse_is_not_read_as_no_review(scene):
    """"Corrupt" and "absent" are different answers and only one of them is
    recoverable by asking again. Conflated, the panel opens empty -- which
    reads as "the absorb never happened" and invites paying for it twice."""
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen1", _review(), {})
    store.scenes._review_path(cid, sid).write_text("{not json", encoding="utf-8")
    with pytest.raises(store.pending_reviews.CorruptReviewError):
        store.pending_reviews.read(cid, sid)


def test_a_record_that_is_not_a_review_is_corrupt_rather_than_empty(scene):
    cid, sid = scene
    store.scenes._review_path(cid, sid).write_text(
        json.dumps({"v": 1, "generation": "g"}), encoding="utf-8")
    with pytest.raises(store.pending_reviews.CorruptReviewError):
        store.pending_reviews.read(cid, sid)


def test_a_record_that_will_not_open_raises_rather_than_answering_none(scene):
    """`Path.exists()`-shaped failure, at the level that matters: "I could not
    look" must not answer "there is nothing there". A directory standing where
    the file goes is the portable way to make the read fail."""
    cid, sid = scene
    store.scenes._review_path(cid, sid).mkdir()
    with pytest.raises(OSError):
        store.pending_reviews.read(cid, sid)


# ---- the merges ------------------------------------------------------------

def _rows():
    return [{"id": "prose:1", "kind": "prose"},
            {"id": "sheet:mara", "kind": "sheet"},
            {"id": "dossier:mara", "kind": "dossier", "target": {"id": "mara"}},
            {"id": "dossier:winifred", "kind": "dossier", "target": {"id": "winifred"}}]


def _phases():
    return [{"name": "extraction", "status": "ok", "reason": None,
             "attempted": True, "budget_exhausted": False},
            {"name": "dossiers", "status": "failed", "reason": "out of time",
             "attempted": True, "budget_exhausted": True},
            {"name": "audit", "status": "failed", "reason": "out of time",
             "attempted": True, "budget_exhausted": True}]


def test_an_audit_retry_replaces_only_the_sheet_rows():
    review = _review(edits=_rows(), phases=_phases(), mechanics={"status": "failed"})
    merged = store.pending_reviews.merge_audit(review, {
        "mechanics": {"status": "ok", "reason": None,
                      "attempted": True, "budget_exhausted": False},
        "edits": [{"id": "sheet:winifred", "kind": "sheet"}]})
    assert [e["id"] for e in merged["edits"]] == [
        "prose:1", "dossier:mara", "dossier:winifred", "sheet:winifred"]
    assert merged["mechanics"]["status"] == "ok"
    # The phase row is a PROJECTION of the block, so it moves with it -- left
    # alone the panel goes on reporting a budget that ran out for a step this
    # retry has since run.
    audit_row = next(p for p in merged["phases"] if p["name"] == "audit")
    assert audit_row["status"] == "ok" and audit_row["budget_exhausted"] is False
    # ...and nothing else moved.
    assert merged["commit_token"] == review["commit_token"]
    assert next(p for p in merged["phases"] if p["name"] == "dossiers")["status"] == "failed"


def test_a_dossier_retry_replaces_only_the_npcs_it_re_proposed():
    """The rule that makes a partly-failed dossier phase recoverable at all: an
    unconditional rebuild would let a retry that failed for Winifred delete her
    perfectly good proposal from the first pass and put nothing in its place."""
    review = _review(edits=_rows(), phases=_phases())
    merged = store.pending_reviews.merge_dossiers(review, {
        "dossiers": {"status": "ok", "reason": None, "proposed": ["mara"],
                     "failed": [], "skipped": [],
                     "attempted": True, "budget_exhausted": False},
        "edits": [{"id": "dossier:mara", "kind": "dossier", "target": {"id": "mara"},
                   "after": "Mara, steadier."}]})
    ids = [e["id"] for e in merged["edits"]]
    assert ids == ["prose:1", "sheet:mara", "dossier:winifred", "dossier:mara"]
    assert merged["edits"][-1]["after"] == "Mara, steadier."
    assert next(p for p in merged["phases"] if p["name"] == "dossiers")["status"] == "ok"
    assert next(p for p in merged["phases"] if p["name"] == "audit")["status"] == "failed"


def test_a_merge_refuses_rather_than_inventing_a_review(scene):
    cid, sid = scene
    with pytest.raises(store.pending_reviews.NoPendingReviewError):
        store.pending_reviews.merge(cid, sid, "gen1", lambda r: r)


def test_a_merge_refuses_a_review_that_was_replaced(scene):
    """A retry owns one phase of ONE review. Folded into the review that
    replaced it, it would report a step that ran against the old one."""
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen2", _review(one_line="fresh"), {})
    with pytest.raises(store.pending_reviews.ReviewReplacedError):
        store.pending_reviews.merge(cid, sid, "gen1",
                                    lambda r: {**r, "one_line": "stale"})
    assert store.pending_reviews.read(cid, sid)["review"]["one_line"] == "fresh"


def test_a_merge_that_matches_is_written(scene):
    """The counterweight to the two refusals above."""
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen1", _review(one_line="before"), {"count": 3})
    store.pending_reviews.merge(cid, sid, "gen1", lambda r: {**r, "one_line": "after"})
    record = store.pending_reviews.read(cid, sid)
    assert record["review"]["one_line"] == "after"
    # The merge is a fold over the review, so everything AROUND it survives.
    assert record["generation"] == "gen1" and record["watermark"] == {"count": 3}


# ---- clearing --------------------------------------------------------------

def test_a_targeted_clear_removes_only_its_own_generation(scene):
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen2", _review(), {})
    assert store.pending_reviews.clear(cid, sid, "gen1") is False
    assert store.pending_reviews.read(cid, sid) is not None
    assert store.pending_reviews.clear(cid, sid, "gen2") is True
    assert store.pending_reviews.read(cid, sid) is None


def test_clearing_is_idempotent(scene):
    """A DELETE naming a generation that has already gone removes nothing and
    is not a failure: the reviewer's intent is satisfied either way."""
    cid, sid = scene
    assert store.pending_reviews.clear(cid, sid, "gen1") is False
    assert store.pending_reviews.clear(cid, sid) is False


def test_an_unconditional_clear_removes_a_record_it_cannot_parse(scene):
    """The owner of the scene -- a save, a cut, a delete -- takes whatever is
    there. A targeted clear cannot: a record that names no generation cannot be
    shown to be the one the reviewer asked to discard."""
    cid, sid = scene
    store.scenes._review_path(cid, sid).write_text("{not json", encoding="utf-8")
    assert store.pending_reviews.clear(cid, sid, "gen1") is False
    assert store.pending_reviews.clear(cid, sid) is True


# ---- following a rename ----------------------------------------------------

def test_a_rename_carries_the_review(scene):
    """Renaming before saving the review is ordinary use, not an exotic race:
    once the run has landed the scene is no longer held. Left behind, the
    review sits orphaned under an id nothing asks about again."""
    cid, sid = scene
    store.scenes.append_message(cid, sid, "user", "We entered.")
    store.pending_reviews.publish(cid, sid, "gen1", _review(one_line="carried"), {})
    new_sid = store.scenes.rename_scene(cid, sid, "The Back Room")
    assert new_sid != sid
    assert store.pending_reviews.read(cid, sid) is None
    assert store.pending_reviews.read(cid, new_sid)["review"]["one_line"] == "carried"


def test_a_repoint_clears_a_destination_it_does_not_fill(scene):
    """A destination id is by definition changing hands, so a review sitting
    there belonged to some other scene -- and inheriting one means saving a
    dead scene's summary onto this transcript."""
    cid, sid = scene
    store.pending_reviews.publish(cid, "0002--elsewhere", "gen1", _review(), {})
    store.pending_reviews.repoint_scenes(cid, {sid: "0002--elsewhere"})
    assert store.pending_reviews.read(cid, "0002--elsewhere") is None


def test_a_repoint_does_not_lose_one_review_to_another(scene):
    """Every source is read before any target is written, so a mapping that
    swaps two ids cannot land one review on top of the other."""
    cid = scene[0]
    store.pending_reviews.publish(cid, "0001--a", "gen-a", _review(one_line="A"), {})
    store.pending_reviews.publish(cid, "0002--b", "gen-b", _review(one_line="B"), {})
    store.pending_reviews.repoint_scenes(cid, {"0001--a": "0002--b", "0002--b": "0001--a"})
    assert store.pending_reviews.read(cid, "0002--b")["review"]["one_line"] == "A"
    assert store.pending_reviews.read(cid, "0001--a")["review"]["one_line"] == "B"


# ---- the scene's own lifecycle ---------------------------------------------

def test_deleting_a_scene_takes_its_review_with_it(scene):
    """Ids are recycled -- `serialize._numbering` derives the next number from
    the files on disk -- so a review left behind would be adopted by the next
    scene to take this id, complete with a commit token that would save the
    dead scene's summary onto the new one."""
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen1", _review(), {})
    path = store.scenes._review_path(cid, sid)
    store.scenes.delete_scene(cid, sid)
    assert not path.exists()


def test_an_orphaned_review_still_holds_its_id(scene):
    """The crash window between the two unlinks, from the other side: a sidecar
    whose transcript is gone must not let the next create adopt its id."""
    cid, sid = scene
    store.pending_reviews.publish(cid, sid, "gen1", _review(), {})
    store.scenes.paths._scene_path(cid, sid).unlink()
    assert store.scenes.paths._sid_taken(cid, sid) is True


def test_a_cut_drops_the_review_it_has_just_invalidated(scene):
    """The review summarises posts the cut has removed, and its commit token
    names the epoch `commits.retire_scene` is retiring in the same breath."""
    cid, sid = scene
    store.scenes.append_message(cid, sid, "user", "We entered.")
    store.scenes.append_message(cid, sid, "assistant", "The door shut.")
    store.pending_reviews.publish(cid, sid, "gen1", _review(), {})
    store.cascade.delete_from(cid, sid, 1)
    assert store.pending_reviews.read(cid, sid) is None


def test_a_retcon_drops_the_review_too(scene):
    cid, sid = scene
    store.scenes.append_message(cid, sid, "user", "We entered.")
    store.pending_reviews.publish(cid, sid, "gen1", _review(), {})
    store.cascade.revert_scene(cid, sid)
    assert store.pending_reviews.read(cid, sid) is None
