"""The run routes: discovery, replay, poll, cancel, and the reverse lookup."""

from __future__ import annotations

import json

import pytest

import grimoire.store as store


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
