"""The run routes: discovery, replay, poll, cancel, and the reverse lookup."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import grimoire.store as store
from grimoire import routes
from grimoire.routes import runs as runs_mod
from grimoire.routes import scenes as routes_scenes
from tests.llm_fakes import FailingOpenRouter


@pytest.fixture
def campaign_scene(client):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    return cid, sid


def _sse(payload: dict) -> str:
    """One SSE data frame, the way the producer emits it."""
    return f"data: {json.dumps(payload)}\n\n"


def _subject(cid, sid):
    return ("scene", cid, sid)


def _reserve(client, cid, sid, attempt="a1", cls="turn"):
    ident = store.scenes.scene_identity(cid, sid)
    run, _ = client.app.state.runs.start_or_existing(
        _subject(cid, sid), cls, "chat", attempt, ident,
        {"campaign": "Saltmarch", "scene": "Mara"})
    return run


def _events(body: str) -> list[dict]:
    """Decoded `data:` payloads, ignoring comments -- exactly what the browser's
    parser surfaces, which is why the index cannot be inferred from this."""
    return [json.loads(line[6:])
            for block in body.split("\n\n")
            for line in block.splitlines()
            if line.startswith("data: ")]


def _ids(body: str) -> list[int]:
    return [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]


# --- replay -----------------------------------------------------------------

def test_from_is_inclusive_so_a_reconnect_reproduces_the_reply_once(client, campaign_scene):
    """`from=N` sends frame N itself, so a client that consumed through N asks
    for N+1. Backwards, this duplicates a delta mid-reply -- invisible until
    someone reads the text."""
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "Wind off the "}))
    run.append_frame(_sse({"delta": "water."}))
    run.finish("landed")

    whole = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=0")
    assert "".join(e["delta"] for e in _events(whole.text)) == "Wind off the water."

    resumed = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=1")
    assert "".join(e["delta"] for e in _events(resumed.text)) == "water."


def test_every_frame_carries_its_absolute_index_on_the_wire(client, campaign_scene):
    """The client cannot derive the cursor by counting decoded events, because
    `parseSSEChunk` discards comment frames -- so after a heartbeat its count
    lags the server's position and `consumed + 1` replays rendered text. The
    index has to be in the protocol."""
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "Wind off the "}))
    run.append_frame(": heartbeat\n\n")
    run.append_frame(_sse({"delta": "water."}))
    run.finish("landed")

    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=0").text
    assert _ids(body) == [0, 1, 2]
    # Only two of the three are visible to a data-event parser, which is the
    # whole point: counting them would put the cursor at 1, not 2.
    assert len(_events(body)) == 2


def test_a_resume_across_a_heartbeat_loses_and_repeats_nothing(client, campaign_scene):
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "Wind off the "}))
    run.append_frame(": heartbeat\n\n")
    run.append_frame(_sse({"delta": "water."}))
    run.finish("landed")

    body = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=2").text
    assert "".join(e["delta"] for e in _events(body)) == "water."
    assert _ids(body) == [2]


def test_from_past_the_end_of_a_terminal_run_is_empty(client, campaign_scene):
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "only"}))
    run.finish("landed")
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=99")
    # Status first: a MISSING route also yields no data frames, so asserting
    # only emptiness passes before the feature exists.
    assert r.status_code == 200
    assert _events(r.text) == []


# --- isolation --------------------------------------------------------------

def test_an_unknown_run_id_is_run_gone(client, campaign_scene):
    cid, sid = campaign_scene
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/nope")
    assert r.status_code == 404 and r.json()["kind"] == "run_gone"


def test_a_run_id_from_another_scene_is_run_gone(client, campaign_scene):
    cid, sid = campaign_scene
    other = store.scenes.create_scene(cid, "Winifred")
    run = _reserve(client, cid, sid)
    r = client.get(f"/api/campaigns/{cid}/scenes/{other}/runs/{run.id}")
    assert r.status_code == 404 and r.json()["kind"] == "run_gone"


def test_a_recycled_sid_cannot_reach_the_dead_scenes_run(client, campaign_scene):
    """Ids are recycled by design, and a terminal run stays readable for the
    whole retention window -- so without the identity check the replacement
    scene would be handed the deleted scene's run, and could cancel it."""
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    store.scenes.delete_scene(cid, sid)
    again = store.scenes.create_scene(cid, "Mara")
    assert again == sid, "precondition: the id was recycled"

    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}")
    assert r.status_code == 404 and r.json()["kind"] == "run_gone"


# --- discovery --------------------------------------------------------------

def test_discovery_returns_the_newest_run_not_an_older_terminal_one(client, campaign_scene):
    """`_by_subject` routinely holds several: a terminal run stays readable for
    the whole window while a new one is already live. Returning the older one
    makes the client settle and miss the live reply entirely -- the exact
    failure this endpoint exists to prevent."""
    cid, sid = campaign_scene
    old = _reserve(client, cid, sid, attempt="a1")
    old.finish("landed")
    live = _reserve(client, cid, sid, attempt="a2")

    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run")
    assert r.status_code == 200
    assert r.json()["run"]["id"] == live.id


def test_discovery_by_attempt_is_an_exact_match(client, campaign_scene):
    """#95: 'a run exists recently' is not proof that MY send landed -- an
    unrelated run finishing on this scene would satisfy it."""
    cid, sid = campaign_scene
    mine = _reserve(client, cid, sid, attempt="mine")
    mine.finish("landed")
    _reserve(client, cid, sid, attempt="theirs")

    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run?attempt=mine")
    assert r.json()["run"]["id"] == mine.id

    missing = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run?attempt=never-sent")
    assert missing.status_code == 200 and missing.json()["run"] is None


def test_discovery_on_a_quiet_scene_answers_none_rather_than_404(client, campaign_scene):
    """A scene with no runs is the normal case on every mount."""
    cid, sid = campaign_scene
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run")
    assert r.status_code == 200 and r.json()["run"] is None


def test_the_run_payload_carries_what_a_client_has_to_decide_on(client, campaign_scene):
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "x"}))

    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}").json()["run"]
    assert got["id"] == run.id
    assert got["state"] == "running"
    assert got["kind"] == "chat"
    assert got["attempt_id"] == "a1"
    assert got["next_index"] == 1


# --- cancel -----------------------------------------------------------------

def test_cancel_on_an_unknown_run_is_run_gone(client, campaign_scene):
    cid, sid = campaign_scene
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/runs/nope/cancel")
    assert r.status_code == 404 and r.json()["kind"] == "run_gone"


def test_cancel_on_an_already_terminal_run_is_not_an_error(client, campaign_scene):
    """The Stop button races the reply landing. Reporting an error for a run
    that finished a moment earlier would show a failure for a turn that
    succeeded."""
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.finish("landed")
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/cancel")
    assert r.status_code == 200 and r.json()["run"]["state"] == "landed"


# --- the reverse lookup -----------------------------------------------------

def test_by_identity_returns_the_current_sid_after_a_rename(client, campaign_scene):
    cid, sid = campaign_scene
    ident = store.scenes.scene_identity(cid, sid)
    new_sid = store.scenes.rename_scene(cid, sid, "Winifred")

    r = client.get(f"/api/campaigns/{cid}/scene-by-identity?identity={ident}")
    assert r.status_code == 200 and r.json()["id"] == new_sid


def test_by_identity_is_404_once_the_scene_is_gone(client, campaign_scene):
    cid, sid = campaign_scene
    ident = store.scenes.scene_identity(cid, sid)
    # The same identity resolves while the scene is alive -- so the 404 below
    # is the lookup answering, not the route being absent.
    assert client.get(f"/api/campaigns/{cid}/scene-by-identity?identity={ident}").status_code == 200
    store.scenes.delete_scene(cid, sid)
    r = client.get(f"/api/campaigns/{cid}/scene-by-identity?identity={ident}")
    assert r.status_code == 404 and r.json()["kind"] == "scene_gone"


def test_by_identity_is_reachable_and_not_shadowed(client, campaign_scene):
    """It resolves a live scene, which a route claimed by another pattern
    could not."""
    cid, sid = campaign_scene
    ident = store.scenes.scene_identity(cid, sid)
    r = client.get(f"/api/campaigns/{cid}/scene-by-identity?identity={ident}")
    assert r.status_code == 200 and r.json()["id"] == sid


def test_a_live_stream_delivers_frames_appended_after_the_client_attached(client, campaign_scene):
    """The foreground half of the feature. A client that attaches while the run
    is still generating -- the ordinary case on reconnect, and the case where
    `from` is already at the tail -- must keep receiving. Snapshotting the
    buffer once and reaching EOF means the client disconnects before the run is
    terminal and never sees the rest of the reply."""
    import threading
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "Wind off the "}))

    def finish_later():
        import time as _t
        _t.sleep(0.2)
        run.append_frame(_sse({"delta": "water."}))
        run.finish("landed")
        run.terminal.set()

    threading.Thread(target=finish_later).start()
    body = client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=0").text
    assert "".join(e["delta"] for e in _events(body)) == "Wind off the water."


def test_attaching_at_the_tail_of_a_live_run_still_receives(client, campaign_scene):
    """`from` equal to `next_index` is what an adopting client sends when it is
    already caught up. A one-shot replay answers empty and closes."""
    import threading
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)

    def produce():
        import time as _t
        _t.sleep(0.2)
        run.append_frame(_sse({"delta": "later"}))
        run.finish("landed")
        run.terminal.set()

    threading.Thread(target=produce).start()
    body = client.get(
        f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=0").text
    assert [e["delta"] for e in _events(body)] == ["later"]


def test_discovery_orders_by_reservation_not_the_wall_clock(client, campaign_scene):
    """A backward clock correction between two runs can give the newer one a
    LOWER `started_at`, so a max() over that field answers with the older,
    terminal run -- the client settles and misses the live reply."""
    cid, sid = campaign_scene
    old = _reserve(client, cid, sid, attempt="a1")
    old.finish("landed")
    live = _reserve(client, cid, sid, attempt="a2")
    live.started_at = old.started_at - 3600      # the clock stepped backwards

    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/run")
    assert r.json()["run"]["id"] == live.id


def test_an_unsafe_campaign_id_is_404_not_500(client):
    """Every id-carrying route in this app is swept for this. `scene_identity`
    reaches `campaign_root`, which raises `CampaignNotFound` for an id the
    store cannot address -- and an unhandled store error is a 500."""
    for path in ("/api/campaigns/C:evil/scenes/s1/run",
                 "/api/campaigns/C:evil/scenes/s1/runs/r1",
                 "/api/campaigns/C:evil/scenes/s1/runs/r1/stream",
                 "/api/campaigns/C:evil/scene-by-identity?identity=" + "0" * 32):
        r = client.get(path)
        assert r.status_code == 404, f"{path} answered {r.status_code}"
    r = client.post("/api/campaigns/C:evil/scenes/s1/runs/r1/cancel")
    assert r.status_code == 404


def test_a_negative_replay_cursor_is_rejected(client, campaign_scene):
    """Silently reading `from=-1` as 0 replays the whole buffer, duplicating a
    reply the client has already rendered. A malformed resume is a bug in the
    caller and should say so."""
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "x"}))
    run.finish("landed")
    r = client.get(f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream?from=-1")
    assert r.status_code == 400


def test_an_oversized_cursor_is_clamped_to_the_tail(client, campaign_scene):
    """A cursor past the buffer must not be held literally.

    Frames that arrive next have LOWER indexes than an oversized `from`, so
    keeping it would exclude every one of them and the client would tail to the
    end of the run and receive nothing -- worse than the reconnect it was
    attempting. Asserted as an equivalence rather than by racing a producer
    thread: an earlier version of this test slept in a thread and passed or
    failed depending on whether that thread beat the request, which is exactly
    the flake this repo warns about.
    """
    cid, sid = campaign_scene
    run = _reserve(client, cid, sid)
    run.append_frame(_sse({"delta": "one"}))
    run.append_frame(_sse({"delta": "two"}))
    run.finish("landed")

    # The cursor itself, because through the route the difference is invisible
    # on a terminal run -- both answer empty -- and observing it on a live one
    # needs a producer thread to lose a race with the request, which is the
    # flake this repo warns about.
    assert runs_mod._start_cursor(99, run) == 2
    assert runs_mod._start_cursor(1, run) == 1
    assert runs_mod._start_cursor(0, run) == 0

    base = f"/api/campaigns/{cid}/scenes/{sid}/runs/{run.id}/stream"
    assert _events(client.get(f"{base}?from=99").text) == []
    assert len(_events(client.get(f"{base}?from=0").text)) == 2


# --- a reservation always reaches a terminal state ---------------------------
# The run is published before the route does any work, which is what makes a
# second send refusable. The cost is that every exit between the reservation and
# `start_detached` owns a live run -- and a run left `running` is never reaped,
# so the scene it holds refuses every later turn for the life of the process.

def _chat(client, cid, sid, content="Mara steps onto the dock.", **kw):
    return client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": content}, **kw)


@pytest.fixture
def sending_scene(client, campaign_scene):
    """`campaign_scene` plus the connection a send needs, so a refusal in these
    tests is the one the test injected rather than `_require_connection`'s."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    return campaign_scene


def _latest(client, cid, sid):
    """The newest run on this scene, reservation order."""
    found = client.app.state.runs.for_subject(_subject(cid, sid))
    return found[-1] if found else None


def test_a_send_that_fails_before_starting_frees_the_scene(client, sending_scene,
                                                           monkeypatch):
    """A refusal raised after the reservation used to strand it.

    `_chat_stream` claims the turn synchronously and raises `StoreBusy` on a
    contended campaign, which the global handler turns into a 409 -- the one
    failure in this window that already had a test, and the one that made the
    scene permanently unusable. What the player then saw was every subsequent
    send refused with `run_in_flight` naming a turn that never began.
    """
    cid, sid = sending_scene

    def busy(*_a, **_k):
        raise store.locks.StoreBusy("campaign is busy")

    real = routes_scenes._chat_stream
    monkeypatch.setattr(routes_scenes, "_chat_stream", busy)
    assert _chat(client, cid, sid).status_code == 409
    monkeypatch.setattr(routes_scenes, "_chat_stream", real)

    run = _latest(client, cid, sid)
    assert run is not None and run.state == "failed", \
        "the reservation was left running with nothing driving it"
    assert run.terminal.is_set() and run.ready.is_set(), \
        "a poll or a cancel on this run would wait forever"
    # and the scene is usable again, which is what the player actually notices
    assert _chat(client, cid, sid).status_code == 200


def test_a_send_refused_outright_frees_the_scene(client, sending_scene, monkeypatch):
    """The same window, entered by an `HTTPException` rather than a store
    error -- a different `except` arm, and the one migrated routes will use
    most (a retry with nothing to retry, a scene that vanished mid-request)."""
    cid, sid = sending_scene

    def refuse(*_a, **_k):
        raise HTTPException(status_code=400, detail="nothing to send")

    real = routes_scenes._chat_stream
    monkeypatch.setattr(routes_scenes, "_chat_stream", refuse)
    assert _chat(client, cid, sid).status_code == 400
    monkeypatch.setattr(routes_scenes, "_chat_stream", real)

    run = _latest(client, cid, sid)
    assert run is not None and run.state == "failed"
    assert _chat(client, cid, sid).status_code == 200


def test_an_ephemeral_turn_detaches_like_any_other(client, sending_scene):
    """An empty send means "next NPC round". It stores no player message, but it
    persists a reply exactly like a normal turn -- so it detaches like one.

    It used to return the producer's response straight to the client instead:
    the generation still died with the socket, AND its reservation stayed
    `running` forever, so the scene refused every later turn.
    """
    cid, sid = sending_scene
    body = _chat(client, cid, sid, content="   ").text

    assert "run" in _events(body)[0], "no leading run frame: the turn is unaddressable"
    run = _latest(client, cid, sid)
    assert run.state == "landed", f"the ephemeral turn's run was {run.state}"
    assert _chat(client, cid, sid, content="   ").status_code == 200


def test_a_provider_failure_leaves_the_run_failed_not_landed(client, sending_scene):
    """"Did not raise" is not "succeeded".

    The stream generators handle an upstream `LLMError` by emitting an error
    frame and returning NORMALLY, so a runner that inferred success from the
    producer finishing recorded a failed turn as `landed` -- and the poll a
    reconnecting phone makes, and the notification that will read it, both
    reported a reply that was never persisted.
    """
    cid, sid = sending_scene
    client.app.dependency_overrides[routes.get_llm] = lambda: FailingOpenRouter(
        kind="rate_limit", message="slow down")

    body = _chat(client, cid, sid).text

    assert any("error" in e for e in _events(body)), "the client was told nothing"
    run = _latest(client, cid, sid)
    assert run.state == "failed", f"a failed turn was recorded {run.state}"
    assert run.error and run.error["kind"] == "rate_limit"


def test_a_throw_after_the_producer_starts_does_not_kill_the_live_run(client, sending_scene,
                                                                      monkeypatch):
    """The guard must not become the bug.

    `start_detached` hands the producer to the runner and the route then builds
    its response. A throw in that window used to be indistinguishable from a
    throw before the producer existed, so the reservation guard would mark a
    RUNNING turn `failed` and set `terminal` -- every subscriber stops reading
    mid-reply and is told the turn failed, while the turn goes on to persist
    perfectly well. Worse than the leak it was written to prevent, because it
    corrupts a turn that was working.
    """
    cid, sid = sending_scene
    real_lead = runs_mod.lead_frame
    boom = {"n": 0}

    def explode_once(run):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("response construction failed")
        return real_lead(run)

    monkeypatch.setattr(routes_scenes.runs, "lead_frame", explode_once)
    with pytest.raises(RuntimeError):
        _chat(client, cid, sid)
    monkeypatch.setattr(routes_scenes.runs, "lead_frame", real_lead)

    run = _latest(client, cid, sid)
    assert run.started, "the premise failed: the producer was never handed over"
    assert run.state != "failed", \
        "the guard terminated a run whose producer was already driving it"
    # and the reply the detached producer was in the middle of still lands
    run.terminal.wait(timeout=10)
    assert run.state == "landed"
    assert store.scenes.read_scene(cid, sid)["messages"][-1]["role"] == "assistant"


def test_a_scene_that_will_not_open_is_busy_not_a_500(client, sending_scene, monkeypatch):
    """`reserve_turn` mints the scene's identity before anything else, and that
    read can fail transiently -- a sync client mid-write, a Windows sharing
    violation. `_require_scene` read the same file a moment earlier, so this is
    contention, not a bad request; unhandled it was a 500, which tells the
    player their library is broken when the right answer is "try again"."""
    cid, sid = sending_scene

    def blocked(*_a, **_k):
        raise store.scenes.identity.UnreadableError("held by another process")

    monkeypatch.setattr(store.scenes, "ensure_identity", blocked)
    monkeypatch.setattr(runs_mod.scenes, "ensure_identity", blocked)
    r = _chat(client, cid, sid)
    monkeypatch.undo()

    assert r.status_code == 409, f"a transient read failure answered {r.status_code}"
    assert r.json()["kind"] == "busy"
