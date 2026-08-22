"""The end-of-scene review outlives the request that asked for it (#396).

Phase 1 detached the five scene-turn producers, so a locked phone no longer
loses a turn. This is the harder half: an absorb is the longest single
generation in the app, its result is a form nobody has written down, and
putting the phone down while it runs is exactly what people do.

Four properties, and each is a way the old shape lost work:

* the absorb lands with nobody listening, and is still there afterwards;
* it is still there after a RESTART, because the registry is memory and a
  review that only lived in it would be gone with the process;
* a review of a transcript that has since moved is refused rather than
  committed -- the commit epoch cannot see play continuing, only another save;
* Cancel and the terminal write cannot lose to each other, in either order.
"""

from __future__ import annotations

import asyncio
import importlib
import time

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from tests import review_runs
from tests.llm_fakes import from_entries

ABSORB_JSON = (
    '{"one_line": "They met.", "summary": "A meeting.", "keywords": ["tea"],'
    ' "timeline_events": [], "character_state_edits": [], "lore_edits": [],'
    ' "plot_movements": [], "relationship_deltas": [], "bond_changes": [],'
    ' "new_lore": [], "weather_edits": []}')

# The real prompts' opening lines, so a reworded system prompt fails here
# rather than silently matching the wrong phase (`llm_fakes.from_entries`).
_EXTRACTION = {"system_contains": "You are absorbing a completed role-play scene"}
_AUDIT = {"system_contains": "You are auditing a completed role-play scene"}
_DOSSIER = {"system_contains": "You are updating a game master's dossier"}
_VOICE = {"system_contains": "You are checking one character's dialogue"}


def _fake():
    return from_entries([{"when": _EXTRACTION, "reply": ABSORB_JSON},
                         {"when": _DOSSIER, "reply": "Aese is steady."},
                         {"when": _VOICE, "reply": '{"verdict": "in_voice", "note": ""}'},
                         {"when": _AUDIT, "reply": '{"warnings": [], "sheet_deltas": []}'}])


@pytest.fixture
def scene(client):
    """A campaign with a present NPC and one post -- enough that every phase
    of an absorb has something to do."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns",
                      json={"name": "Saltmarch", "world": wid}).json()["id"]
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    client.post(f"/api/worlds/{wid}/characters",
                json={"name": "Aese", "version_name": "main"})
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "The Tearoom"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "aese", "version": "main", "role": "npc"})
    store.scenes.append_message(cid, sid, "user", "We entered.")
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = _fake
    return cid, sid


def _absorb(client, cid, sid):
    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert started.status_code == 202, started.json()
    body = started.json()
    run = review_runs.wait_for_run(client, cid, sid, body["run"]["id"])
    return body["generation"], run


def _pending(client, cid, sid):
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/pending-review").json()


# ---- the absorb survives having nobody to answer ---------------------------

def test_the_absorb_answers_at_once_and_lands_without_a_listener(client, scene):
    """The whole shape of the fix in one test: the POST returns before any
    model call has been made, so there is no socket for a locked phone to take
    down -- and the review is on disk when the client comes back for it."""
    cid, sid = scene
    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert started.status_code == 202
    assert started.json()["run"]["state"] == "running"

    run = review_runs.wait_for_run(client, cid, sid, started.json()["run"]["id"])
    assert run["state"] == "landed", run
    review = _pending(client, cid, sid)["review"]
    assert review["one_line"] == "They met." and review["commit_token"]


def test_the_review_survives_the_process_that_made_it(client, scene, tmp_path):
    """The registry is memory, so a review that lived only in it would be gone
    with the process -- and on Android the process is reclaimed routinely. A
    second app over the same store is what a restart looks like from here."""
    cid, sid = scene
    _absorb(client, cid, sid)

    importlib.reload(store)
    with TestClient(create_app()) as restarted:
        body = restarted.get(
            f"/api/campaigns/{cid}/scenes/{sid}/pending-review").json()
    assert body["review"]["one_line"] == "They met."
    # ...and the run itself is gone with the process, which is exactly why the
    # review could not be left on it.
    assert body["generation"]


def test_a_scene_with_no_review_answers_none_rather_than_404(client, scene):
    """Every mount asks; an error for the ordinary case would make every quiet
    scene look broken."""
    cid, sid = scene
    assert _pending(client, cid, sid) == {"review": None, "generation": None, "stale": None}


# ---- playing on ------------------------------------------------------------

def test_a_review_is_withheld_once_the_scene_has_moved_on(client, scene):
    """Once the absorb lands, its exclusion slot is released and the composer
    re-enables -- so the player can append, cut and retcon while the review
    sits on disk. None of that advances the commit epoch, so nothing else here
    would notice."""
    cid, sid = scene
    _absorb(client, cid, sid)
    assert _pending(client, cid, sid)["review"] is not None

    store.scenes.append_message(cid, sid, "user", "And then we left.")
    body = _pending(client, cid, sid)
    assert body["review"] is None
    assert body["stale"] == {"prepared_posts": 1, "current_posts": 2}


def test_a_moved_scene_is_refused_at_save_with_nothing_written(client, scene):
    """The read-time check is the affordance; this is the guarantee. A review
    that reached the panel before the scene moved is still holding a token that
    passes every other check -- and saving it marks the scene absorbed with a
    summary of posts nobody reviewed."""
    cid, sid = scene
    _absorb(client, cid, sid)
    review = _pending(client, cid, sid)["review"]
    store.scenes.append_message(cid, sid, "user", "And then we left.")

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": review["one_line"], "summary": review["summary"],
                         "keywords": [], "timeline_events": [{"date": "day 1",
                                                              "text": "They met."}],
                         "edits": [], "commit_token": review["commit_token"]})
    assert r.status_code == 409 and r.json()["kind"] == "review_stale"
    assert r.json()["prepared_posts"] == 1 and r.json()["current_posts"] == 2
    # Refused before the first write, so the chronicle is untouched and the
    # token is unspent -- which is what makes re-running the absorb the whole
    # recovery.
    assert store.chronicle.read_chronicle(cid) == {}
    scene_meta = store.scenes.read_scene(cid, sid)["meta"]
    assert str(scene_meta.get("done", "")).lower() != "true"


def test_a_scene_that_did_not_move_saves(client, scene):
    """The counterweight, and the one that matters: a watermark that refused
    everything would pass the test above and make End Scene unusable."""
    cid, sid = scene
    _absorb(client, cid, sid)
    review = _pending(client, cid, sid)["review"]

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": review["one_line"], "summary": review["summary"],
                         "keywords": review["keywords"], "timeline_events": [],
                         "edits": [], "commit_token": review["commit_token"]})
    assert r.status_code == 200, r.json()
    assert store.chronicle.read_chronicle(cid)[sid]["one_line"] == "They met."
    # ...and the saved review is cleared, so the panel cannot reopen a review
    # of a scene that is now absorbed.
    assert _pending(client, cid, sid)["review"] is None


def test_a_replayed_save_clears_the_review_too(client, scene):
    """The commit is idempotent by design (#235), so a save whose response was
    lost returns the recorded result through the replay branch -- and cleanup
    that lived only on the first-execution path would leave an obsolete review
    retrievable forever for a scene that is demonstrably absorbed."""
    cid, sid = scene
    _absorb(client, cid, sid)
    review = _pending(client, cid, sid)["review"]
    body = {"one_line": review["one_line"], "summary": review["summary"],
            "keywords": review["keywords"], "timeline_events": [], "edits": [],
            "commit_token": review["commit_token"]}
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                      json=body).status_code == 200
    # Put a review back, as a process that died between recording the commit
    # and deleting it would have left one.
    store.pending_reviews.publish(cid, sid, "orphan", review, {"count": 1})
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                      json=body).status_code == 200
    assert store.pending_reviews.read(cid, sid) is None


# ---- Cancel ----------------------------------------------------------------

def test_cancel_flags_the_run_before_it_removes_the_record(client, scene):
    """The ordering the whole cancel path turns on. Left unordered the two lose
    to each other: the DELETE lands while an absorb is finishing, and the
    runner then publishes and recreates the review the reviewer just dismissed.
    """
    cid, sid = scene
    generation, _ = _absorb(client, cid, sid)
    subject = ("scene", cid, store.scenes.scene_identity(cid, sid))
    review_runs.cancel(client, cid, sid, generation)

    flagged = client.app.state.runs.reviews_for_generation(subject, generation)
    assert flagged and all(r.review_cancelled for r in flagged)
    assert store.pending_reviews.read(cid, sid) is None


def test_a_cancel_for_another_generation_leaves_the_review_alone(client, scene):
    """Idempotent is not the same as indiscriminate: a stale Discard from a
    panel showing a review that has since been replaced must not take the new
    one with it."""
    cid, sid = scene
    _absorb(client, cid, sid)
    r = review_runs.cancel(client, cid, sid, "some-other-generation")
    assert r.status_code == 200 and r.json()["removed"] is False
    assert _pending(client, cid, sid)["review"] is not None


def test_a_flagged_run_publishes_nothing(client, scene, monkeypatch):
    """The suppression itself, at the one line that decides it.

    Driven directly rather than through a race: the check and the write are one
    campaign-lock hold on purpose, so there is no moment between them for a
    test to aim at -- which is the property, not an obstacle to testing it.
    """
    cid, sid = scene
    written = []

    class Run:
        review_cancelled = False
        cancel_requested = False
        scene_identity = store.scenes.scene_identity(cid, sid)

    # Either intent suppresses it: a Discard of the REVIEW and a Stop on the
    # RUN are different requests that want the same answer, and a Stop that
    # only stopped the generating would publish anyway -- a cancelled scope
    # does not reach into a threadpool worker already inside this write.
    for flag in ("review_cancelled", "cancel_requested"):
        stopped = Run()
        setattr(stopped, flag, True)
        with pytest.raises(routes.scenes._ReviewCancelledError):
            routes.scenes._under_review_lock(cid, sid, stopped, lambda: written.append(1))
    assert written == []

    routes.scenes._under_review_lock(cid, sid, Run(), lambda: written.append(1))
    assert written == [1]


def test_a_run_whose_scene_was_replaced_publishes_nothing(client, scene):
    """An existence check is not enough: `serialize._numbering` derives the
    next scene number from the files on disk, so deleting the highest-numbered
    scene frees its number and the next create takes the identical id."""
    cid, sid = scene
    written = []

    class Run:
        review_cancelled = False
        cancel_requested = False
        scene_identity = "0" * 32       # never minted for this scene

    with pytest.raises(routes.scenes._SceneMovedError):
        routes.scenes._under_review_lock(cid, sid, Run(), lambda: written.append(1))
    assert written == []


# ---- the exclusion key -----------------------------------------------------

def test_a_running_review_holds_the_scene_against_a_turn(client, scene, monkeypatch):
    """`turn` and `review` share one exclusion key, so an absorb and a chat
    cannot both hold a scene -- which is what stops an append from moving the
    transcript out from under a ten-minute absorb."""
    cid, sid = scene
    monkeypatch.setattr(routes.scenes, "ABANDON_POLL", 0.02)
    held = _Wedged()
    client.app.dependency_overrides[routes.get_llm] = lambda: _facade(held)

    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert started.status_code == 202
    _wait_for(held.frames_seen)

    sent = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "Another line."})
    assert sent.status_code == 409 and sent.json()["kind"] == "run_in_flight"
    edited = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0",
                        json={"content": "Rewritten."})
    assert edited.status_code == 409 and edited.json()["kind"] == "scene_busy"

    review_runs.cancel(client, cid, sid, started.json()["generation"])
    review_runs.wait_for_run(client, cid, sid, started.json()["run"]["id"])


def test_a_refused_absorb_does_not_leave_the_scene_held(client, scene):
    """The reservation is taken BEFORE the snapshot, so every pre-flight
    refusal after it has to give the scene back -- a run left `running` with
    nothing driving it is never reaped, and the scene answers `run_in_flight`
    for the life of the process."""
    cid, sid = scene
    store.scenes.mark_absorbed(cid, sid, "o", "s")

    refused = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert refused.status_code == 409 and refused.json()["kind"] == "already_absorbed"

    again = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb?force=true")
    assert again.status_code == 202, again.json()
    review_runs.wait_for_run(client, cid, sid, again.json()["run"]["id"])


# ---- retries fold in -------------------------------------------------------

def test_a_dossier_retry_merges_into_the_stored_review(client, scene):
    """A retry answers a PART of a review. Written whole it would destroy the
    absorb's prose, its staged edits and its commit token."""
    cid, sid = scene
    _absorb(client, cid, sid)
    before = _pending(client, cid, sid)["review"]

    client.app.dependency_overrides[routes.get_llm] = \
        lambda: from_entries([{"when": _DOSSIER, "reply": "Aese, warier now."}])
    retried = client.post(f"/api/campaigns/{cid}/scenes/{sid}/dossiers")
    assert retried.status_code == 202, retried.json()
    review_runs.wait_for_run(client, cid, sid, retried.json()["run"]["id"])

    after = _pending(client, cid, sid)["review"]
    assert after["commit_token"] == before["commit_token"]
    assert after["one_line"] == before["one_line"]
    assert [e["after"] for e in after["edits"] if e["kind"] == "dossier"] == \
        ["Aese, warier now."]


def test_a_retry_carries_the_generation_of_the_review_it_is_retrying(client, scene):
    """So Cancel on that review stops it, rather than it belonging to a
    generation of its own that nothing addresses."""
    cid, sid = scene
    generation, _ = _absorb(client, cid, sid)
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: from_entries([{"when": _DOSSIER, "reply": "Aese, warier now."}])
    retried = client.post(f"/api/campaigns/{cid}/scenes/{sid}/dossiers")
    assert retried.json()["generation"] == generation
    review_runs.wait_for_run(client, cid, sid, retried.json()["run"]["id"])


def test_a_retry_of_a_scene_that_moved_is_refused_before_a_token_is_spent(client, scene):
    """Folding a phase into a review the save is going to refuse anyway is the
    most expensive way to reach the same answer."""
    cid, sid = scene
    _absorb(client, cid, sid)
    store.scenes.append_message(cid, sid, "user", "And then we left.")
    sent = []

    class Counting:
        async def stream(self, m, cfg, usage=None):
            sent.append(m)
            yield "{}"

        async def complete(self, m, cfg, usage=None):
            sent.append(m)
            return "{}"

    counting = Counting()
    client.app.dependency_overrides[routes.get_llm] = lambda: counting
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/dossiers")
    assert r.status_code == 409 and r.json()["kind"] == "review_stale"
    assert sent == []


# ---- a rename in between ---------------------------------------------------

def test_renaming_a_scene_carries_its_review_to_the_new_id(client, scene):
    """Once the review has landed the scene is no longer held, so renaming
    before saving it is ordinary use."""
    cid, sid = scene
    _absorb(client, cid, sid)
    new_sid = client.put(f"/api/campaigns/{cid}/scenes/{sid}",
                         json={"title": "The Back Room"}).json()["id"]
    assert new_sid != sid
    assert _pending(client, cid, new_sid)["review"]["one_line"] == "They met."


# ---- helpers ---------------------------------------------------------------

class _Wedged:
    """Dribbles inside the idle bound: healthy by every clock the facade keeps,
    and finished by none of them. Bounded at ~2s rather than endlessly, so a
    regression that drops the abandonment check fails the suite in seconds
    instead of hanging it."""

    def __init__(self, gap=0.01, frames=200):
        self.gap, self.frames = gap, frames
        self.finished = False
        self.frames_seen: list[bool] = []

    async def stream(self, messages, *args, **kwargs):
        for _ in range(self.frames):
            import anyio
            await anyio.sleep(self.gap)
            self.frames_seen.append(True)
            yield ""
        self.finished = True


def _facade(provider):
    from grimoire.llm import LLMClient
    return LLMClient(openrouter=provider, claude=provider,
                     openai_compatible=provider, timeout=120)


def _wait_for(seen, at=1, timeout=10.0):
    deadline = time.monotonic() + timeout
    while len(seen) < at:
        assert time.monotonic() < deadline, "the provider was never reached"
        time.sleep(0.01)


def test_cancel_answers_only_once_the_run_it_stopped_has_stopped(client, scene,
                                                                 monkeypatch):
    """Cancel means stopped, the way it does for a turn.

    The caller's next act is usually "end the scene again", and `review` holds
    the scene's exclusion key exactly as a turn does -- so a DELETE that
    answered while its run was still unwinding would have that fresh absorb
    refused with `run_in_flight` by a review it had just discarded.
    """
    cid, sid = scene
    monkeypatch.setattr(routes.scenes, "ABANDON_POLL", 0.02)
    held = _Wedged()
    client.app.dependency_overrides[routes.get_llm] = lambda: _facade(held)

    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    generation = started.json()["generation"]
    _wait_for(held.frames_seen)

    cancelled = review_runs.cancel(client, cid, sid, generation)
    assert cancelled.json()["stopped"] == 1
    # Terminal ALREADY, with no polling in between: that is the whole claim.
    run = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/"
                     f"{started.json()['run']['id']}").json()["run"]
    assert run["state"] == "cancelled", run

    # ...and the scene is free, so the absorb the reviewer reaches for next is
    # accepted rather than refused by the review they just discarded.
    client.app.dependency_overrides[routes.get_llm] = _fake
    again = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert again.status_code == 202, again.json()
    review_runs.wait_for_run(client, cid, sid, again.json()["run"]["id"])
    assert _pending(client, cid, sid)["review"]["one_line"] == "They met."


def test_a_cancel_over_an_unreadable_record_still_stops_the_work(client, scene,
                                                                 monkeypatch):
    """Half of Cancel cannot be retried into existence later.

    Stopping the run is that half: a record that outlives its runs is refused
    by its own watermark at save, but a provider left generating for a review
    nobody wants goes on spending until it finishes on its own. So the flag
    lands first and stays landed, and the refusal is reported as the transient
    thing it is rather than as a crash for a button that half worked.
    """
    cid, sid = scene
    monkeypatch.setattr(routes.scenes, "ABANDON_POLL", 0.02)
    held = _Wedged()
    client.app.dependency_overrides[routes.get_llm] = lambda: _facade(held)
    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    generation = started.json()["generation"]
    _wait_for(held.frames_seen)

    # A directory where the sidecar goes: the portable way to make the read
    # fail without depending on what the process may do as its own owner.
    store.scenes._review_path(cid, sid).mkdir()

    refused = review_runs.cancel(client, cid, sid, generation)
    assert refused.status_code == 409 and refused.json()["kind"] == "busy"
    run = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/"
                     f"{started.json()['run']['id']}").json()["run"]
    assert run["state"] == "cancelled", run


def test_an_unsafe_id_on_a_review_route_is_a_refusal_and_not_a_crash(client, scene):
    """Every id-carrying route in this app is swept for this.

    These join the scene id onto a filename (`scenes/<sid>.review.json`), so
    they answer for it twice over: `_require_scene` refuses an id the store
    cannot address before the path is ever built, and an unhandled store error
    would be a 500 rather than the 404 every sibling route gives.
    """
    cid, sid = scene
    unsafe = [f"/api/campaigns/C:evil/scenes/{sid}/pending-review",
              f"/api/campaigns/{cid}/scenes/C:evil/pending-review",
              f"/api/campaigns/{cid}/scenes/nope/pending-review"]
    for path in unsafe:
        assert client.get(path).status_code == 404, path
        assert client.delete(path + "?generation=g").status_code == 404, path
    for route in ("absorb", "audit", "dossiers"):
        assert client.post(
            f"/api/campaigns/C:evil/scenes/{sid}/{route}").status_code == 404, route


# ---- what a client is told when the record moves under a retry --------------
#
# A scoped retry is a read-modify-write of a review it does not own outright:
# a cut can clear that record and a fresh absorb can replace it while the
# retry's own call is still in flight. Neither is a write it may make anyway --
# `{mechanics, edits}` written whole destroys the absorb's prose, its staged
# edits and its commit token -- so each comes back as its own refusal, and
# these pin which.

def _wedged_retry(client, scene, monkeypatch, route="dossiers"):
    """Start a scoped retry and stop with its provider mid-call."""
    cid, sid = scene
    _absorb(client, cid, sid)
    monkeypatch.setattr(routes.scenes, "ABANDON_POLL", 0.02)
    held = _Wedged()
    client.app.dependency_overrides[routes.get_llm] = lambda: _facade(held)
    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/{route}")
    assert started.status_code == 202, started.json()
    _wait_for(held.frames_seen)          # the call is really in flight
    return started


def test_a_retry_whose_review_was_cleared_reports_that_it_had_nowhere_to_land(
        client, scene, monkeypatch):
    """A cut clears the record (`cascade`), and a retry already in flight then
    has nothing to fold into. Inventing one would put a phase report and a set
    of staged edits on a scene with no review behind them."""
    cid, sid = scene
    started = _wedged_retry(client, scene, monkeypatch)

    store.pending_reviews.clear(cid, sid)
    # `_Wedged` runs itself out in about two seconds, so the merge below is
    # reached without cancelling anything -- which is the point: this is the
    # record moving under a retry that is otherwise perfectly healthy.
    run = review_runs.wait_for_run(client, cid, sid, started.json()["run"]["id"])
    assert run["state"] == "failed", run
    assert run["error"]["kind"] == "review_missing"


def test_a_retry_whose_review_was_replaced_does_not_fold_into_the_new_one(
        client, scene, monkeypatch):
    """A retry owns one phase of ONE review. Folded into the review that
    replaced it, it would report a step that ran against the old one.

    Unreachable through this app's own routes -- the exclusion key stops a
    second review run on a scene -- and kept anyway, because the store is a
    directory of plain files that a synced folder or a second process can
    write. Driven here at the seam where that would show up.
    """
    cid, sid = scene
    started = _wedged_retry(client, scene, monkeypatch)

    store.pending_reviews.publish(cid, sid, "somebody-elses-generation",
                                  {"one_line": "fresh", "edits": []}, {})
    run = review_runs.wait_for_run(client, cid, sid, started.json()["run"]["id"])
    assert run["state"] == "failed", run
    assert run["error"]["kind"] == "review_replaced"
    assert store.pending_reviews.read(cid, sid)["review"]["one_line"] == "fresh"


def test_a_record_that_will_not_parse_is_reported_rather_than_read_as_absent(
        client, scene):
    """On both doors that open a stored review. A file that will not parse
    cannot be repaired by asking again, and an empty panel reads as "the absorb
    never happened" -- which invites paying for it a second time."""
    cid, sid = scene
    _absorb(client, cid, sid)
    store.scenes._review_path(cid, sid).write_text("{not json", encoding="utf-8")

    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/pending-review")
    assert got.status_code == 409 and got.json()["kind"] == "review_unreadable"
    retried = client.post(f"/api/campaigns/{cid}/scenes/{sid}/dossiers")
    assert retried.status_code == 409 and retried.json()["kind"] == "review_unreadable"


def test_a_record_that_will_not_open_is_retryable_rather_than_absent(client, scene):
    """Unreadable is not "no review" either, and it is not corrupt: a sync
    client holding the file for a moment is transient, and the client already
    retries a `busy`."""
    cid, sid = scene
    store.scenes._review_path(cid, sid).mkdir()

    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/pending-review")
    assert got.status_code == 409 and got.json()["kind"] == "busy"
    retried = client.post(f"/api/campaigns/{cid}/scenes/{sid}/audit")
    assert retried.status_code == 409 and retried.json()["kind"] == "busy"


def test_a_save_is_not_wedged_by_a_review_it_cannot_read(client, scene):
    """The watermark check is evidence, not a gate. A garbled sidecar must not
    be able to stop a scene's review ever being saved -- the epoch check still
    stands, and that is the one #271 put there."""
    cid, sid = scene
    _absorb(client, cid, sid)
    review = _pending(client, cid, sid)["review"]
    store.scenes._review_path(cid, sid).write_text("{not json", encoding="utf-8")

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle",
                   json={"one_line": review["one_line"], "summary": review["summary"],
                         "keywords": [], "timeline_events": [], "edits": [],
                         "commit_token": review["commit_token"]})
    assert r.status_code == 200, r.json()


# ---- what the terminal write turns each failure into ------------------------
#
# `_persist_review` is a mapping and nothing else: every way the write can go
# wrong becomes a `state` and an `error` a polling client renders. Driven
# directly, because several of these arms are reachable only from a second
# writer or a filesystem that refuses -- and a mapping nobody has exercised is
# a client showing "undefined" at the moment it most needs words.

class _FakeRun:
    review_cancelled = False
    cancel_requested = False

    def __init__(self, identity):
        self.scene_identity = identity


def _persisted(cid, sid, run, write):
    return asyncio.run(routes.scenes._persist_review(cid, sid, run, "gen1", write))


def test_the_terminal_write_names_what_stopped_it(client, scene):
    cid, sid = scene
    identity = store.scenes.scene_identity(cid, sid)

    def boom(exc):
        def write():
            raise exc
        return write

    cancelled = _FakeRun(identity)
    cancelled.review_cancelled = True
    assert _persisted(cid, sid, cancelled, lambda: None)["state"] == "cancelled"

    moved = _FakeRun("0" * 32)
    assert _persisted(cid, sid, moved, lambda: None) == {
        "state": "failed", "error": {
            "kind": "scene_gone", "status": 404,
            "detail": "the scene this review was prepared for is gone"}}

    for exc, kind in ((store.pending_reviews.NoPendingReviewError(sid), "review_missing"),
                      (store.pending_reviews.ReviewReplacedError(sid), "review_replaced"),
                      (OSError("disk went away"), "busy"),
                      (store.pending_reviews.CorruptReviewError("garbled"), "busy")):
        out = _persisted(cid, sid, _FakeRun(identity), boom(exc))
        assert out["state"] == "failed" and out["error"]["kind"] == kind, exc

    landed = _persisted(cid, sid, _FakeRun(identity), lambda: None)
    assert landed == {"state": "landed", "result": {"generation": "gen1", "sid": sid}}


def test_a_contended_campaign_is_waited_out_rather_than_costing_the_review(
        client, scene, monkeypatch):
    """The one place in this file that retries a busy campaign, and the reason
    is that this caller cannot be told to try again: what it is writing is ten
    minutes of generation and there is no copy of it anywhere."""
    cid, sid = scene
    run = _FakeRun(store.scenes.scene_identity(cid, sid))
    real = store.locks.campaign_lock
    tries = []

    def contended(target):
        def lock(c):
            tries.append(c)
            if len(tries) <= target:
                raise store.locks.CampaignBusy(c)
            return real(c)
        return lock

    monkeypatch.setattr(routes.scenes, "_PERSIST_BACKOFF", 0)
    written = []
    monkeypatch.setattr(store.locks, "campaign_lock", contended(2))
    routes.scenes._under_review_lock(cid, sid, run, lambda: written.append(1))
    assert written == [1] and len(tries) == 3

    # ...and it gives up rather than retrying forever, because a campaign held
    # for three full `LOCK_TIMEOUT` waits is not going to free up on a fourth.
    tries.clear()
    monkeypatch.setattr(store.locks, "campaign_lock", contended(99))
    with pytest.raises(store.locks.StoreBusy):
        routes.scenes._under_review_lock(cid, sid, run, lambda: written.append(2))
    assert len(tries) == routes.scenes._PERSIST_ATTEMPTS
    assert written == [1]


def test_a_refusal_with_no_kind_still_reaches_the_client_as_words(client, scene):
    """`HTTPException` carries either a dict or a bare string in this tree, and
    a run error built from the second shape must still say something: a client
    reads `detail` straight onto the banner."""
    assert routes.scenes._run_error(
        routes.scenes.HTTPException(status_code=400, detail="nothing to absorb")) == {
            "kind": "refused", "detail": "nothing to absorb", "status": 400}


def test_a_cancel_whose_scene_cannot_be_resolved_still_removes_the_record(
        client, scene, monkeypatch):
    """A Cancel that cannot find a run still has a record to remove.

    Raising here instead would make the transient case -- a scene header a sync
    client is holding for a moment -- the one in which the reviewer cannot
    dismiss a review at all, which is the opposite of what a Discard is for.
    """
    cid, sid = scene
    _absorb(client, cid, sid)
    generation = _pending(client, cid, sid)["generation"]

    def unreadable(*_a, **_kw):
        raise OSError("the scene header is locked just now")

    monkeypatch.setattr(routes.runs.scenes, "scene_identity_strict", unreadable)
    gone = review_runs.cancel(client, cid, sid, generation)
    assert gone.status_code == 200 and gone.json() == {"removed": True, "stopped": 0}
    assert store.pending_reviews.read(cid, sid) is None
