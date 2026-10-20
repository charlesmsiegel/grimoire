"""Replay original indexed frames and terminal outcome across reconnects."""

from __future__ import annotations

import json

import anyio
from fastapi.responses import StreamingResponse

from . import _contracts, _record
from ._router import router as router


def _start_cursor(from_: int, run: _record.Run) -> int:
    """Where a replay should actually begin.

    A cursor past the buffer is clamped to the tail. Held literally on a LIVE
    run it would exclude every frame that arrives next -- their indexes are
    LOWER than it -- so the client would tail to the end of the run and receive
    nothing at all, which is worse than the reconnect it was attempting.

    Its own function because that is the only way to test it: through the route
    the difference is invisible on a terminal run (both answer empty) and needs
    a race to observe on a live one.
    """
    return min(from_, len(run.frames))


def run_payload(run: _record.Run) -> dict:
    """What a client needs to decide what to do, and nothing it does not.

    `next_index` rather than the frames themselves: a poll is for state, and a
    terminal run's buffer can be the whole reply. The client asks for frames
    over the stream route, from the cursor it kept.
    """
    return {
        "id": run.id,
        "state": run.state,
        "kind": run.kind,
        "cls": run.cls,
        "attempt_id": run.attempt_id,
        # Which pending review a `review` run is preparing, so a client that
        # discovered a live absorb through `GET .../run` can address Cancel to
        # it -- `DELETE .../pending-review` names the generation, not the run.
        # `None` for every other class.
        "review_generation": run.review_generation,
        # Which scene this run belongs to, independently of what that scene is
        # called now -- see `lead_frame` for the rename it survives. `None` for
        # the campaign, world and global subjects, which have no scene.
        "scene_identity": run.scene_identity,
        "labels": run.labels,
        "next_index": len(run.frames),
        "error": run.error,
        "result": run.result,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
    }


def tail_response(run: _record.Run, from_: int = 0, lead: str | None = None):
    """A response that replays `run` from `from_` and then follows it.

    Shared by the reconnect route and by every producing route, so a client
    reading a turn as it happens and a client picking one up after a
    disconnection are served by exactly the same code -- there is no "live"
    path that could drift from the "replay" path.

    `lead` is emitted before any buffered frame and is NOT stored in the
    buffer: it carries the run's identity to the caller that just created it,
    and a client reconnecting later already knows the id it is asking about.
    """

    async def event_stream():
        """Replay what is buffered, then TAIL until the run is terminal.

        A negative `from` is rejected by the route signature rather than read as
    zero: silently widening a malformed cursor replays the whole buffer and
    duplicates a reply the client has already rendered.

    A one-shot replay would answer the buffer and reach EOF -- so a client
        attaching to a live run (the ordinary case on reconnect, and always the
        case when `from` is already at the tail) would disconnect before the
        run finished and never see the rest of the reply. That is the whole
        foreground half of this feature.

        Polling rather than a push signal because the frames arrive from a
        different thread and the handshake events are set-once; the interval is
        well under a human's perception of "live", and it costs one list slice
        per tick on an idle run.
        """
        if lead is not None:
            yield lead
        cursor = _start_cursor(from_, run)
        while True:
            # Terminal is read BEFORE draining, and that order is the whole
            # correctness argument. Any frame appended before the run went
            # terminal is necessarily visible to the drain that follows this
            # read -- so the loop cannot exit holding undelivered frames, and
            # the last words of a reply cannot be truncated. Draining first and
            # then checking would leave a window between the two, which a
            # second pass only narrows and no test can pin down.
            done = run.terminal.is_set() or run.state != "running"
            for frame in run.frames_since(cursor):
                cursor = frame["index"] + 1
                yield f"id: {frame['index']}\n{frame['raw']}"
            if done:
                return
            await anyio.sleep(_contracts.STREAM_POLL_SECONDS)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def sse(payload: dict) -> str:
    """One SSE data frame. Local rather than imported from `streaming`, which
    this module is forbidden to depend on (`test_import_guard`)."""
    return f"data: {json.dumps(payload)}\n\n"


def lead_frame(run: _record.Run) -> str:
    """The frame every producing route sends first.

    Before any delta, and before anything can fail: a client whose connection
    dies immediately still has to be able to find its run, or the send is
    unaddressable and "did my turn land?" has no answer -- which is the whole
    ambiguity this feature exists to remove.

    `scene_identity` rides along for the same reason the notification intent
    carries one: the `sid` in the URL the client came in on goes stale the
    moment the scene is RENAMED, and an opener explicitly does not hold its
    scene against a rename (a `draft` takes no exclusion key). So a client
    that loses its connection and comes back at the old id finds no run at all,
    and abandons a generation the server is still buffering. With the identity
    it resolves the scene's current id through `GET /scene-by-identity` first.
    Absent for a run that has none, which is only the non-scene subjects.
    """
    return sse({"run": {"id": run.id, "attempt_id": run.attempt_id,
                        "state": run.state, "next_index": len(run.frames),
                        "scene_identity": run.scene_identity}})
