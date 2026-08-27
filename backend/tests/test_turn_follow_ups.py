"""A landed turn asks for its own follow-ups (#397).

The rolling summary and the scene-break question used to be fired by
`CampaignView.askAfterPost`, once the streaming promise settled -- which is
exactly what does not happen in the case detached runs exist for: the phone
locks, the JavaScript is suspended, the turn lands server-side, and nobody is
left to POST either one. What these pin is that the *turn runner* schedules
them now, that it does so with a boundary the client used to supply, and that
neither can cost the turn anything -- not its outcome, and not the scene.
"""

from __future__ import annotations

import importlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.routes import runs as runs_routes
from grimoire.routes import scenes as scenes_routes
from grimoire.routes import streaming as streaming_routes

from .llm_fakes import FakeLLM, from_entries

#: A scene-break verdict the parser accepts. `break: false` deliberately, so a
#: fold and a question can both run without the suggestion changing what the
#: rolling-summary assertions are looking at.
NO_BREAK = '{"break": false, "reason": "They are still mid-argument.", "title": ""}'

#: How long a test waits for the two background runs a turn schedules. They are
#: driven by the lifespan loop and the POST does not await them, so every
#: assertion about what they did has to wait for them first. Generous, because
#: it is only ever a scheduling gap.
FOLLOW_UP_TIMEOUT = 10.0


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    # `with`, so the lifespan runs: the turn hands its work to a runner that
    # lives on it, and so do the follow-ups it schedules.
    with TestClient(app) as c:
        c.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
        yield c


def _use(client, llm):
    client.app.dependency_overrides[routes.get_llm] = lambda: llm
    return llm


def _provider(reply: str = "The lamps are already lit.", summary: str = "A summary.",
              verdict: str = NO_BREAK) -> FakeLLM:
    """One fake answering all three kinds of call this suite drives.

    A cassette rather than a script, for `llm_fakes`' documented reason: the
    turn, the fold and the question are issued by different code paths and the
    follow-ups run concurrently with each other, so "call 2" names nothing.
    Matching on the system prompt that owns each call keeps every assertion
    about which reply the code got.
    """
    return from_entries([
        {"when": {"system_contains": "keeping a running summary"}, "reply": summary},
        {"when": {"system_contains": "has the scene reached a natural place to stop?"},
         "reply": verdict},
        {"when": {}, "reply": reply},
    ], "turn-follow-ups")


def _scene(client, posts=0):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Saltmarch"}).json()["id"]
    for n in range(posts):
        store.scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                                    f"Post {n}.")
    return cid, sid


def _send(client, cid, sid, text="What now?"):
    return client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": text})


def _subject(cid, sid):
    return ("scene", cid, store.scenes.scene_identity(cid, sid))


def _background(client, cid, sid) -> list:
    """Every `background` run this scene has, in reservation order."""
    runs = client.app.state.runs.for_subject(_subject(cid, sid))
    return [r for r in runs if r.cls == "background"]


def _settled(client, cid, sid) -> list:
    """The scene's background runs, once each has reached a terminal state.

    Waits on the runs rather than sleeping: they are scheduled synchronously by
    the turn (before its response returns) and then driven on the lifespan
    loop, so they exist by the time the POST answers and only their completion
    is in question.
    """
    found = _background(client, cid, sid)
    assert found, "the turn scheduled no follow-ups at all"
    for run in found:
        assert run.terminal.wait(timeout=FOLLOW_UP_TIMEOUT), \
            f"the {run.kind} follow-up never finished"
    return found


# ---- the turn schedules them, and the client no longer has to --------------
def test_a_landed_turn_schedules_both_follow_ups(client):
    """The whole point: nobody asked for either of these over HTTP."""
    _use(client, _provider())
    cid, sid = _scene(client, posts=4)
    _send(client, cid, sid)
    assert sorted(r.kind for r in _settled(client, cid, sid)) \
        == ["rolling-summary", "scene-break"]


def test_the_summary_is_folded_without_the_client_asking(client):
    """A scene sitting on the threshold: the turn's own reply crosses it, and
    the fold lands with no second request from anybody."""
    _use(client, _provider(summary="Mara reaches the salt gate."))
    cid, sid = _scene(client, posts=9)      # + the post and the reply = 11
    _send(client, cid, sid)
    _settled(client, cid, sid)
    assert store.scenes.get_rolling_summary(cid, sid)["summary"] \
        == "Mara reaches the salt gate."


def test_a_turn_short_of_the_threshold_still_spends_nothing(client):
    """Scheduling after every turn is only affordable because the routes
    themselves decide whether anything is due. The follow-ups must run and
    reach no provider at all."""
    llm = _use(client, _provider())
    cid, sid = _scene(client, posts=2)
    _send(client, cid, sid)
    _settled(client, cid, sid)
    assert llm.calls == 1                   # the turn, and nothing else
    assert store.scenes.get_rolling_summary(cid, sid)["summary"] == ""


def test_the_scene_break_question_is_asked_when_the_heuristic_crosses(client):
    store.write_config(scene_break_every="2")
    _use(client, _provider(verdict='{"break": true, "reason": "The ledger changed '
                                   'hands.", "title": "The Long Walk Back"}'))
    cid, sid = _scene(client, posts=8)
    _send(client, cid, sid)
    _settled(client, cid, sid)
    stored = store.scenes.get_scene_break(cid, sid)
    assert stored["verdict"] == "yes" and stored["title"] == "The Long Walk Back"


def test_the_boundary_is_the_transcript_the_turn_left(client):
    """`upto`, which is what `askAfterPost` used to read on the client.

    A post appended after the turn finished -- the next send, racing the fold --
    must stay outside what the fold claims to cover, or the reply that answers
    it is an APPEND and stays out of the "current" summary until another whole
    threshold goes by.
    """
    _use(client, _provider(summary="Covered."))
    cid, sid = _scene(client, posts=9)
    _send(client, cid, sid)
    _settled(client, cid, sid)
    covered = store.scenes.get_rolling_summary(cid, sid)["at"]
    total = len(store.scenes.read_scene(cid, sid)["messages"])
    assert covered == total, "the fold's boundary was not the turn's own tail"
    # ...and the prompt saw the reply, not a transcript cut short of it.
    assert covered >= 11


def test_a_turn_that_persists_nothing_schedules_nothing(client):
    """An empty completion moves neither gate, so asking would spend two
    reservations to be told nothing is due."""
    _use(client, _provider(reply=""))
    cid, sid = _scene(client, posts=9)
    _send(client, cid, sid)
    assert _background(client, cid, sid) == []


# ---- and they cannot cost the turn anything --------------------------------
def test_a_failing_follow_up_does_not_fail_its_turn(client, monkeypatch):
    """`_guarded` isolates each run, and the scheduling call sits outside the
    turn's own success path. A rolling summary that blows up is a failed
    background run and a landed turn."""
    def boom(*a, **k):
        raise RuntimeError("the fold could not even start")
    monkeypatch.setattr(scenes_routes, "_rolling_once", boom)
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    body = _send(client, cid, sid)
    assert body.status_code == 200
    assert "The lamps are already lit." in body.text
    settled = {r.kind: r.state for r in _settled(client, cid, sid)}
    assert settled == {"rolling-summary": "failed", "scene-break": "landed"}
    # the reply is on disk, which is what "the turn landed" has to mean
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] \
        == "The lamps are already lit."


def test_a_follow_up_that_cannot_be_scheduled_does_not_fail_its_turn(client,
                                                                     monkeypatch):
    """The scheduling call itself is fail-soft: there is nobody to tell, and a
    summary that could not even be reserved is not a failed turn."""
    def boom(*a, **k):
        raise RuntimeError("no runner here")
    monkeypatch.setattr(scenes_routes, "schedule_follow_ups", boom)
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    assert _send(client, cid, sid).status_code == 200
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] \
        == "The lamps are already lit."


def test_a_live_follow_up_cannot_refuse_the_next_turn(client):
    """`background` declares no exclusion key, so neither of these can hold the
    scene. Asserted on the registry rather than by racing a real one: a run
    that holds no key is refusable by nothing, and that is the property."""
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    _send(client, cid, sid)
    for run in _settled(client, cid, sid):
        assert routes.runs.exclusion_key(run.subject, run.cls) is None
    # ...and the next send is accepted, which is the same claim end to end.
    assert _send(client, cid, sid, "And then?").status_code == 200


def test_a_live_follow_up_cannot_freeze_the_scene(client):
    """The other half of the same rule: a scene holding one is still editable,
    cuttable and absorbable -- `scene_busy` is keyed off the exclusion key."""
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    _send(client, cid, sid)
    _settled(client, cid, sid)
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/messages/0",
                   json={"content": "Post 0, revised."})
    assert r.status_code == 200


def test_discovery_never_answers_with_a_follow_up(client):
    """`GET .../run` with no attempt is "the newest thing I could be waiting
    for". A background run is not that: it has no frame buffer, and a client
    handed one attaches to a stream that never ends."""
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    _send(client, cid, sid)
    _settled(client, cid, sid)
    assert _background(client, cid, sid), "the fixture proved nothing"
    found = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run").json()["run"]
    assert found is not None and found["cls"] == "turn"


def test_a_follow_up_is_never_notified(client):
    """The class table's `Notify` column. Told about as a turn, the Android
    shell posts "New Post" for a scene whose transcript did not grow."""
    seen = []
    client.app.state.on_run_terminal = lambda *args: seen.append(args)
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    _send(client, cid, sid)
    _settled(client, cid, sid)
    assert [args[2] for args in seen] == ["turn"]


# ---- and none of it may cost the turn its own outcome ----------------------
def test_an_unreadable_boundary_is_a_skipped_follow_up_not_a_failed_turn(client):
    """`_tail_length` runs inside `finalize`, AFTER the reply is on disk, and
    `_fence_stream` converts only `StoreBusy` there. A scene momentarily
    unreadable -- a sync client mid-replace, a Windows sharing violation --
    would otherwise escape to the runner and mark a landed turn `failed`, over
    a reply that is perfectly persisted, leaving a retry free to append a
    second one."""
    cid, _sid = _scene(client, posts=2)
    # The real function, against a scene that is not there: `read_scene` raises
    # and this must answer None rather than propagate.
    assert streaming_routes._tail_length(cid, "no-such-scene") is None
    # ...and None recorded on the box is simply no boundary.
    box = streaming_routes.StreamOutcome()
    box.persisted(None)
    assert box.at is None


def test_a_turn_whose_boundary_cannot_be_read_still_lands(client, monkeypatch):
    """The other half of the same rule, end to end: no boundary means no
    follow-up, and a turn that reported one is untouched."""
    monkeypatch.setattr(streaming_routes, "_tail_length", lambda *a: None)
    _use(client, _provider())
    cid, sid = _scene(client, posts=9)
    body = _send(client, cid, sid)
    assert body.status_code == 200 and "The lamps are already lit." in body.text
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["content"] \
        == "The lamps are already lit."
    assert _background(client, cid, sid) == []


def test_reserving_a_follow_up_does_not_wait_on_the_campaign_lock(client):
    """The reservation is awaited by the turn's own generator, so a blocking
    one keeps the SSE body open and the composer locked long after the reply
    landed. `background` holds no exclusion key and its store-move refusal is
    taken under the REGISTRY lock, so it has no business waiting on this one.
    """
    cid, sid = _scene(client, posts=2)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with store.locks.campaign_lock(cid):
            holding.set()
            release.wait(10)

    keeper = threading.Thread(target=hold, daemon=True)
    keeper.start()
    try:
        assert holding.wait(5), "the lock was never taken"
        started = time.monotonic()
        run = runs_routes.reserve_background(client.app, cid, sid, "rolling-summary")
        waited = time.monotonic() - started
    finally:
        release.set()
        keeper.join(timeout=10)
    assert run is not None, "the reservation was refused"
    # Well under `LOCK_TIMEOUT`, which is what a lock-taking reservation would
    # have spent; loose enough not to be a stopwatch on a shared runner.
    assert waited < 5, f"the reservation waited {waited:.1f}s on the campaign lock"
