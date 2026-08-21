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
    run = _wait_terminal(live_server.app, run_id, ("scene", cid, sid))
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
    _wait_terminal(live_server.app, results["a"], ("scene", cid, a))
    _wait_terminal(live_server.app, results["b"], ("scene", cid, b))

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
    run = _wait_terminal(live_server.app, run_id, ("scene", cid, sid))

    assert run.state == "failed", f"the run was {run.state}, not failed"
    assert run.error and run.error["kind"] == "scene_replaced"
    assert not store.scenes.read_scene(cid, recycled)["messages"], \
        "the dead scene's reply was appended to its replacement"
