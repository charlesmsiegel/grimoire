"""A dropped subscriber detaches; it does not cancel the run.

The inverse of today's behaviour, and the single most important property in
this feature. These tests take `live_server` rather than `client` because
`TestClient` buffers a streaming response to completion -- leaving its context
manager injects a disconnect only after the stream already finished, so the
most important test in the plan would pass against an implementation that still
cancels on a real socket close.
"""

from __future__ import annotations

import json
import time

import httpx

import grimoire.store as store
from tests.llm_fakes import FakeOpenRouter, StallingOpenRouter


def _subject(cid, sid):
    """A scene run's subject: the campaign and the scene's IDENTITY, not its
    `sid` -- see `runs.Subject` for why the id cannot name a detached run."""
    return ("scene", cid, store.scenes.scene_identity(cid, sid))


def _lines(r):
    """The response's line iterator, created once and reused.

    `httpx` refuses a second `iter_lines()` on the same response --
    `StreamConsumed` -- so a test that reads the leading frame and then drains
    the rest has to keep the first iterator rather than asking for another. It
    surfaced as a warning rather than a failure because the drain ran in a
    worker thread, which meant the drain those tests perform was not happening
    at all.
    """
    it = getattr(r, "_grimoire_lines", None)
    if it is None:
        it = r.iter_lines()
        r._grimoire_lines = it
    return it


def _frames(r) -> list[dict]:
    """Decoded `data:` payloads read from a live response, as they arrive."""
    return [json.loads(line[6:]) for line in _lines(r)
            if line.startswith("data: ")]


def _first_run_frame(r, timeout: float = 5.0) -> dict:
    """The leading `run` frame, which every producing route emits first so the
    client can address the run even if the connection dies immediately after."""
    deadline = time.monotonic() + timeout
    for line in _lines(r):
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "run" in payload:
                return payload
        if time.monotonic() > deadline:
            break
    raise AssertionError("no leading run frame arrived")


def _wait_terminal(app, run_id, subject, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = app.state.runs.get(run_id, subject)
        if run is not None and run.state != "running":
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never became terminal")


def test_a_dropped_subscriber_does_not_cancel_the_run(live_server):
    """Disconnect used to mean cancel. Now it means one subscriber walked away.

    The held provider is what makes "mid-generation" a defined moment: it has
    emitted one delta and will not emit the next until released, so the close
    below lands squarely inside the stream rather than after it.
    """
    cid, sid = live_server.campaign_scene
    held = live_server.hold_provider("Mist over the dock.")

    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                      json={"content": "Mara steps onto the dock."},
                      timeout=10) as r:
        run_id = _first_run_frame(r)["run"]["id"]
        held.await_first_delta()
        r.close()                       # a real socket close, mid-generation

    held.release()
    run = _wait_terminal(live_server.app, run_id, _subject(cid, sid))
    assert run.state == "landed", f"the run was {run.state}, not landed"
    reply = store.scenes.read_scene(cid, sid)["messages"][-1]["content"]
    assert "dock" in reply.lower(), "the reply never reached the transcript"


def test_the_leading_frame_names_the_run_before_any_delta(live_server):
    """A client whose connection dies immediately still has to be able to find
    its run. If the id only arrived with the first delta, a failure before then
    would leave the send unaddressable -- which is #95 all over again."""
    cid, sid = live_server.campaign_scene
    held = live_server.hold_provider()
    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                      json={"content": "Mara waits."}, timeout=10) as r:
        frame = _first_run_frame(r)
        assert frame["run"]["id"]
        assert frame["run"]["attempt_id"]
        r.close()
    held.release()


def test_two_scenes_generate_at_once_without_cross_contamination(live_server):
    """Sequential requests through a buffering client would pass this against a
    shared mutable producer. Both are in flight here before either is released.
    """
    import threading
    cid, (a, b) = live_server.two_scenes
    # DISTINCT replies, keyed on each request's own prompt. Giving both scenes
    # the same text made this unfalsifiable: a producer that swapped or
    # duplicated buffered output between them would have passed, which is the
    # only thing the test is for.
    held = live_server.hold_provider({"Seraphine?": "Seraphine waits.",
                                      "Winifred?": "Winifred does not."})
    results = {}

    def post(sid, text, key):
        with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                          json={"content": text}, timeout=15) as r:
            results[key] = _first_run_frame(r)["run"]["id"]
            for _ in _lines(r):
                pass

    ta = threading.Thread(target=post, args=(a, "Seraphine?", "a"))
    tb = threading.Thread(target=post, args=(b, "Winifred?", "b"))
    ta.start(); tb.start()
    held.await_first_delta()
    held.release()
    ta.join(timeout=20); tb.join(timeout=20)

    assert results["a"] != results["b"], "both scenes shared one run"
    _wait_terminal(live_server.app, results["a"], _subject(cid, a))
    _wait_terminal(live_server.app, results["b"], _subject(cid, b))

    reply_a = store.scenes.read_scene(cid, a)["messages"][-1]["content"].lower()
    reply_b = store.scenes.read_scene(cid, b)["messages"][-1]["content"].lower()
    assert "seraphine" in reply_a and "winifred" not in reply_a
    assert "winifred" in reply_b and "seraphine" not in reply_b


def test_a_second_send_while_a_turn_holds_the_scene_is_refused(live_server):
    """One run per scene, which is what lets the composer be disabled rather
    than hopefully-ignored."""
    cid, sid = live_server.campaign_scene
    held = live_server.hold_provider()
    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                      json={"content": "Mara waits."}, timeout=10) as r:
        _first_run_frame(r)
        held.await_first_delta()
        second = httpx.post(f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                            json={"content": "again"}, timeout=10)
        assert second.status_code == 409
        assert second.json()["kind"] == "run_in_flight"
        r.close()
    held.release()


def test_a_reply_never_lands_on_a_scene_that_recycled_the_id(live_server):
    """The publish fence, which detachment is what makes necessary.

    Scene ids are recycled -- `serialize._numbering` derives the next number
    from the files on disk with no stored counter -- so deleting a scene frees
    its id and a same-titled replacement lands on exactly the same one. A turn
    used to die with its socket, which kept that window narrow; now it can be
    held open for minutes while the player does anything at all, including
    deleting the scene.

    `_owns_turn` cannot catch this: the claim is keyed by `sid`, so the
    replacement has not claimed anything and the old turn still reads as the
    scene's owner. Only the identity token distinguishes them.
    """
    cid, _ = live_server.campaign_scene
    # Made here, and made LAST: numbering derives from the files on disk, so
    # only the highest-numbered scene frees its id by being deleted. The
    # fixture's own scenes sit below it.
    sid = store.scenes.create_scene(cid, "Winifred")
    # Captured BEFORE the delete: the subject is the scene's identity, and the
    # replacement below mints its own. Reading it afterwards would ask about the
    # replacement's run rather than this one's -- which is the very distinction
    # under test.
    subject = _subject(cid, sid)
    held = live_server.hold_provider("Mist over the dock.")

    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                      json={"content": "Mara steps onto the dock."},
                      timeout=10) as r:
        run_id = _first_run_frame(r)["run"]["id"]
        held.await_first_delta()
        store.scenes.delete_scene(cid, sid)
        recycled = store.scenes.create_scene(cid, "Winifred")
        assert recycled == sid, "the premise failed: the id was not recycled"
        r.close()

    held.release()
    run = _wait_terminal(live_server.app, run_id, subject)

    assert run.state == "failed", f"the run was {run.state}, not failed"
    assert run.error and run.error["kind"] == "scene_replaced"
    assert not store.scenes.read_scene(cid, recycled)["messages"], \
        "the dead scene's reply was appended to its replacement"


def _mech_scene(url: str) -> tuple[str, str]:
    """A module-bound campaign with one sheeted, cast character, built over
    HTTP so it works against the live server exactly as a client would."""
    httpx.put(f"{url}/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = httpx.post(f"{url}/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = httpx.post(f"{url}/api/campaigns",
                     json={"name": "Saltmarch", "world": wid}).json()["id"]
    httpx.put(f"{url}/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = httpx.post(f"{url}/api/worlds/{wid}/characters",
                      json={"name": "Mara"}).json()["character"]
    sid = httpx.post(f"{url}/api/campaigns/{cid}/scenes",
                     json={"title": "Dock"}).json()["id"]
    httpx.post(f"{url}/api/campaigns/{cid}/scenes/{sid}/cast",
               json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    store.sheets.write(cid, "characters", chid, "medium",
                       {"vigor": 3, "brawl": 2, "wits": 2, "occult": 1}, expected=None)
    return cid, sid


def test_a_dropped_subscriber_does_not_cancel_a_roll_continuation(live_server):
    """The same guarantee, through the OTHER kind of producer.

    `post_roll_proposal` streams `_continuation_stream`, not `_chat_stream`,
    and it lives in a different module -- so an implementation that migrated
    the chat path and stopped would pass every other detach test here while
    locking the phone during an accepted roll still cancelled it and dropped
    the narration. A roll is exactly when a player looks away.
    """
    url = live_server.url
    cid, sid = _mech_scene(url)
    # A fence in the reply mints the pending proposal, the way play does it.
    live_server.set_provider(FakeOpenRouter(
        ["She lunges—\n", "```roll\n",
         '{"check": "brawl", "actor": "characters:mara", "difficulty": 6}',
         "\n```", "trailing"]))
    httpx.post(f"{url}/api/campaigns/{cid}/scenes/{sid}/chat",
               json={"content": "go"}, timeout=15)
    rec = httpx.get(f"{url}/api/campaigns/{cid}/scenes/{sid}/roll-proposal"
                    ).json()["record"]
    assert rec and rec["status"] == "pending", "the premise failed: no proposal"

    held = live_server.hold_provider("The lamps gutter, then hold.")
    body = {"proposal": rec["id"], "action": "accept", "check": "brawl",
            "actor": "characters:mara", "difficulty": 6, "modifier": 0}
    with httpx.stream("POST", f"{url}/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                      json=body, timeout=15) as r:
        run_id = _first_run_frame(r)["run"]["id"]
        held.await_first_delta()
        r.close()                       # the phone locks, mid-narration

    held.release()
    run = _wait_terminal(live_server.app, run_id, _subject(cid, sid))
    assert run.state == "landed", f"the continuation was {run.state}, not landed"
    reply = store.scenes.read_scene(cid, sid)["messages"][-1]["content"]
    assert "gutter" in reply.lower(), "the accepted roll's narration was dropped"


def test_a_stopped_turn_that_flushed_a_partial_still_asks_for_its_follow_ups(
        live_server):
    """A Stop is not a reason to leave both gates behind (#397).

    `on_abort` persists whatever narration arrived, so the transcript grew --
    and the client's own `askAfterPost` went with the move to server-side
    scheduling, so nothing else is left to ask. The cancellation is re-raised
    straight past the success path's scheduling call, which is why the abort
    path fires one of its own, shielded.

    A STALLING provider rather than a held one, and the difference is the whole
    setup: `HeldOpenRouter` parks in `anyio.to_thread.run_sync`, which is not
    abandoned on cancellation, so a Stop against it is only delivered once the
    test releases -- by which time the stream loop has ended normally and the
    cancellation lands between the loop and `finalize`, on a turn that flushed
    nothing. Stalling parks on a cancellable sleep with the deltas already fed
    to the watcher, which is the shape a real Stop mid-generation has.
    """
    import threading

    cid, sid = live_server.campaign_scene
    live_server.set_provider(StallingOpenRouter(["Mist over the dock."]))

    with httpx.stream("POST", f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/chat",
                      json={"content": "Mara steps onto the dock."},
                      timeout=20) as r:
        run_id = _first_run_frame(r)["run"]["id"]
        _await_delta(r)
        stop = threading.Thread(target=lambda: httpx.post(
            f"{live_server.url}/api/campaigns/{cid}/scenes/{sid}/runs/{run_id}/cancel",
            timeout=40), daemon=True)
        stop.start()
        stop.join(timeout=40)
        r.close()

    run = _wait_terminal(live_server.app, run_id, _subject(cid, sid))
    assert run.state == "cancelled", f"the run was {run.state}, not cancelled"
    # The premise, checked rather than assumed: the player's own post is
    # appended before the stream starts, so a length alone would pass over a
    # flush that never happened. It is the trailing ASSISTANT post that says
    # the abort wrote something -- and therefore that both gates moved.
    tail = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert tail["role"] == "assistant" and "mist" in tail["content"].lower(), \
        f"nothing was flushed ({tail!r}), so this would prove nothing"

    found = _background_runs(live_server.app, cid, sid)
    assert sorted(run.kind for run in found) == ["rolling-summary", "scene-break"], \
        f"a stopped turn that grew the transcript scheduled {[r.kind for r in found]}"


def _await_delta(r, timeout: float = 10.0) -> None:
    """Read frames until one carries narration, so the watcher has text to flush."""
    deadline = time.monotonic() + timeout
    for line in _lines(r):
        if line.startswith("data: ") and "delta" in json.loads(line[6:]):
            return
        if time.monotonic() > deadline:
            break
    raise AssertionError("no delta arrived")


def _background_runs(app, cid, sid, timeout: float = 15.0) -> list:
    """This scene's `background` runs, once both have been reserved.

    Polled rather than read once: the abort path schedules them while the
    cancellation is still unwinding, which is after the cancel route -- which
    waits only on the run's terminal event -- has already answered.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [r for r in app.state.runs.for_subject(_subject(cid, sid))
                 if r.cls == "background"]
        if len(found) == 2:
            return found
        time.sleep(0.05)
    return [r for r in app.state.runs.for_subject(_subject(cid, sid))
            if r.cls == "background"]
