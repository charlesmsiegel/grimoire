"""Hand off one reserved run and release every unstarted reservation."""

from __future__ import annotations

import contextlib

from fastapi import HTTPException

from ... import runner
from . import _contracts, _events, _record
from ._router import router as router


def start_detached(app, run: _record.Run, producer, outcome=None,
                   on_unstarted=None) -> None:
    """Run `producer` for `run`, decoupled from the request that asked for it.

    `producer` is a zero-arg callable returning an async iterator of raw SSE
    frames -- the existing stream generators, unchanged. Every frame it yields
    is buffered on the run, which is what makes the work survive the socket:
    the response tails that buffer, and so does a reconnect ten minutes later.

    `outcome` is a zero-arg callable read AFTER the producer is exhausted; it
    returns what the producer decided about itself, or `None` to let the runner
    infer success. It exists because "did not raise" is not "succeeded" here:
    the stream generators handle an upstream `LLMError`, a contended finalize,
    and a scene that changed underneath them by emitting an error frame and
    returning NORMALLY. Without this the runner marked every one of those
    `landed`, so polling -- and the notification that will read it -- reported
    a delivered reply for a turn that failed. Passed as a callable rather than
    a value because it is only decided while the producer runs, and as a
    callable rather than the producer's own object because `runs` may not
    import `streaming` (`test_import_guard`).

    `on_unstarted` undoes destructive setup the ROUTE did on the one path where
    the producer is never entered -- a Stop that landed while the route was
    still in synchronous setup. Regenerate is the caller that needs it; see
    `runner._guarded`.
    """

    async def pump():
        async for frame in producer():
            run.append_frame(frame)
        return None if outcome is None else outcome()

    # AFTER the handoff, not before. `runner.start` can raise -- the lifespan
    # portal is gone, or closing as the request hands off -- and `reservation`
    # deliberately skips a started run, so marking it first left the record
    # permanently `running` with no task, no terminal event, and its scene's
    # exclusion key held for the rest of the process. Same thread, and nothing
    # reads the flag until this route returns, so there is no window here.
    runner.start(app, run, pump, on_unstarted)
    run.started = True


def answer_without_running(app, run: _record.Run, frames: list[str],
                           state: _contracts.RunState = "landed"):
    """End a reserved run that answered on its own, and hand back its answer.

    For the exits that produce a complete response without generating anything
    -- an already-narrated proposal, a check that would not resolve. Returning
    those frames directly would leave the run holding an empty buffer, and a
    duplicate request adopting it by attempt id (which is the whole point of
    the id) would then be handed a stream containing nothing at all. Buffering
    them first makes the run the single record of what this request answered,
    however it answered.

    `state` is what that answer WAS, and defaulting it to `landed` for every
    caller was wrong: a check that would not resolve buffers an `error` frame
    and generated nothing, so a client polling the record read success while
    the stream said failure -- and on Android the completion notification said
    the turn had replied. The frames and the run's state have to agree, because
    they are read by different clients and neither knows about the other.
    """
    for frame in frames:
        run.append_frame(frame)
    release_before_start(app, run, state)
    return _events.tail_response(run, 0, lead=_events.lead_frame(run))


@contextlib.contextmanager
def reservation(app, run: _record.Run):
    """Guarantee that a reserved run reaches a terminal state.

    `reserve_turn` publishes the run before the route does any work, on purpose:
    that is what makes a second send refusable rather than merely discouraged.
    The cost is that every path out of the route between the reservation and
    `start_detached` now owns a live, discoverable run -- and a route that
    returns or raises in that window used to leave it `running` forever. Such a
    run is never eligible for reaping (only terminal runs are), so the scene it
    holds answers `run_in_flight` for the life of the process: one bad request
    and the scene is unusable until restart.

    Wrapping the window instead of auditing each exit is deliberate. The
    branches are the thing that changes as routes are migrated, and a guard that
    has to be remembered at each new `return` is a guard that will be missed.
    """
    try:
        yield
    except HTTPException as exc:
        # A refusal the route chose -- 400 for an empty retry, 404 for a scene
        # that vanished. The client is being told; the run has to agree, or the
        # composer stays locked against a turn that never began.
        #
        # WITH ITS STATUS AND ITS KIND, because the response carrying them can
        # be lost. A draft route that reserves before its slow preflight (both
        # scenario parses do) can refuse afterwards, and a client that adopts
        # the run by attempt id reads the refusal off the record instead --
        # so without these it is handed a bare 409 where the request said 400
        # or 404, and a retry adopts that same wrong shape.
        _release_unstarted(app, run, "failed", {
            "kind": _detail_kind(exc), "detail": _detail_text(exc),
            "status": exc.status_code})
        raise
    except BaseException as exc:
        # `status` 500, for the reason the arm above carries its own: this
        # record is read INSTEAD of the response when that response was lost,
        # and an absent status falls back to 409 on the client -- so an upload
        # that blew up while being read came back as a conflict the reader
        # could do nothing about. An exception nobody handled is a 500, and
        # this is the last of the three arms that was not saying so.
        _release_unstarted(app, run, "failed", {
            "kind": "run_failed", "detail": str(exc) or type(exc).__name__,
            "status": 500})
        raise
    # No exception, no producer: the route answered on its own. Nothing will
    # ever set this run's events, so anyone already polling or cancelling it
    # would wait forever.
    _release_unstarted(app, run, "landed")


def _release_unstarted(app, run: _record.Run, state: _contracts.RunState, error: dict | None = None) -> None:
    """Terminate `run` -- but ONLY if nothing is driving it.

    The `started` guard is on the failure arms too, not just the clean exit, and
    that is not defensive noise: `start_detached` succeeds and the route then
    builds its response, so a throw between the two would otherwise mark a run
    `failed` and set `terminal` while its producer is still writing frames into
    it. Every subscriber would stop reading mid-reply and be told the turn
    failed, while the turn went on to persist perfectly well -- the worst of the
    outcomes this whole guard exists to prevent, arrived at by the guard itself.
    """
    if run.started or run.state != "running":
        return
    release_before_start(app, run, state, error)


def _detail_kind(exc: HTTPException) -> str:
    """An HTTPException's `kind`, or `refused` when it named none.

    The structured refusals in this tree (`already_absorbed`,
    `scene_id_too_long`, `missing_key`) carry one and the client acts on it;
    the plain-string ones do not, and `refused` is what they have always been
    recorded as."""
    detail = exc.detail
    if isinstance(detail, dict) and detail.get("kind"):
        return str(detail["kind"])
    return "refused"


def _detail_text(exc: HTTPException) -> str:
    """An HTTPException's message as a string, however it was built. Routes
    raise both plain strings and the dict shape the 409 handler unwraps."""
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("detail") or detail.get("kind") or detail)
    return str(detail)


def release_before_start(app, run: _record.Run, state: _contracts.RunState, error: dict | None = None) -> None:
    """`runner.release_before_start`, re-exported so routes need one import."""
    runner.release_before_start(app, run, state, error)


def start_computing(app, run: _record.Run, work) -> None:
    """Run `work` detached, for a class whose value is a payload and not frames.

    `work` is a zero-arg callable returning a coroutine that returns the run's
    outcome -- `{"state": ..., "result"?: ..., "error"?: ...}` -- exactly what
    `runner._guarded` already understands from a producer's `outcome`.

    No frame buffer, and that is the whole difference from `start_detached`:
    End Scene is not a streaming view, so there is nothing to tail and a client
    polls `GET .../runs/{id}` instead. Buffering an empty stream for it would
    give a reconnecting client an SSE response that says nothing.

    `run.started` is set AFTER the handoff for `start_detached`'s reason:
    `runner.start` can raise, and `reservation` deliberately skips a started
    run, so marking first would leave the record permanently `running` with no
    task and its scene's exclusion key held for the rest of the process.
    """
    runner.start(app, run, work)
    run.started = True
