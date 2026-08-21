"""Scheduling for detached runs: the thread bridge, the per-run boundary, the reaper.

Every streaming route in this app is ``def``, not ``async def`` -- FastAPI runs
each one in a threadpool worker. The work they start has to outlive the request,
which means it has to be handed to the lifespan's event loop from a thread that
is not the loop. That handoff is the whole reason this module exists, and
getting it wrong fails at the first real request rather than in a test.

Three things live here:

* ``install`` -- attaches the machinery that needs a running loop (the portal,
  the task group, the reaper) and swaps the registry's event factory for one
  that builds events *on* the loop. The registry itself is created in
  ``create_app``, because a bare ``TestClient`` never runs a lifespan.
* ``start`` / ``cancel`` / ``release_before_start`` -- the handles a producing
  route uses.
* ``_guarded`` -- the per-run failure boundary. Without it, one run raising
  takes down every sibling and the backup ticker with it.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import anyio
from anyio.from_thread import BlockingPortal

_log = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 5.0
"""How long `cancel` waits for a run to install its cancel scope.

Only ever a scheduling gap -- the task is already queued on the loop -- so this
is generous, not a real wait.
"""

REAP_INTERVAL_SECONDS = 60.0
"""How often to sweep terminal runs. Well under ``REAP_SECONDS`` so a run is
dropped promptly after its window, and rare enough that the sweep itself is
never the reason a phone stays awake."""


class _PortalEvent:
    """An ``anyio.Event`` created and mutated only on the lifespan loop.

    Both halves matter, and the second is the subtle one:

    * ``anyio`` is unpinned here -- it arrives through ``fastapi>=0.110``, whose
      own floor is anyio 3.7.1, where ``Event()`` goes straight through
      ``sniffio`` and raises when constructed off the loop. On 4.2+ it returns a
      lazily-binding adapter instead and appears to work, so a bare call passes
      locally and breaks on a resolution nobody looked at.
    * that adapter's lazy binding is unsynchronized: it tests
      ``_internal_event is None`` and then assigns, with no lock, so a ``set()``
      from a handler thread racing a ``wait()`` on the loop can bind twice and
      leave the waiter parked on an object the setter never touches.

    Building and setting through the portal removes both questions. Do not
    "simplify" this back to a bare ``anyio.Event()`` because the tests pass.
    """

    __slots__ = ("_async", "_loop_thread", "_portal", "_sync")

    def __init__(self, portal: BlockingPortal, loop_thread: int) -> None:
        self._portal = portal
        self._loop_thread = loop_thread
        self._async = portal.call(anyio.Event)
        # A mirror for synchronous waiters. Without it every `wait()` would have
        # to spawn a task on the loop just to block a thread, which is a lot of
        # machinery for "has this happened yet".
        self._sync = threading.Event()

    def set(self) -> None:
        # `portal.call` FROM the loop thread deadlocks -- it submits work and
        # then blocks that same loop waiting for it. Compare thread identity
        # rather than asking anyio whether a task is running: any anyio task in
        # any thread would answer yes, and only this one is the portal's.
        if threading.get_ident() == self._loop_thread:
            self._async.set()
        else:
            self._portal.call(self._async.set)
        self._sync.set()

    def is_set(self) -> bool:
        return self._sync.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block a NON-loop thread until the event is set."""
        return self._sync.wait(timeout)

    @property
    def async_event(self) -> Any:
        """The underlying ``anyio.Event``, for code already on the loop."""
        return self._async


def install(app, tg) -> None:
    """Attach the loop-bound machinery. Called from ``_lifespan``, inside the
    task group, once the portal is up.

    Does NOT create the registry: that happens in ``create_app`` so a lifespan-
    less ``TestClient`` still has one. What it does is upgrade the registry's
    event factory, so every run published from here on gets events built on the
    loop rather than the default off-loop ones.
    """
    portal = app.state.run_portal
    loop_thread = threading.get_ident()      # `install` runs ON the loop
    app.state.run_task_group = tg
    app.state.runs.set_event_factory(lambda: _PortalEvent(portal, loop_thread))
    tg.start_soon(_reaper, app)


async def _reaper(app) -> None:
    """Drop terminal runs past their window, forever.

    Nothing else calls ``reap``, so without this the registry is a memory leak
    that grows with every turn the user takes.
    """
    while True:
        await anyio.sleep(REAP_INTERVAL_SECONDS)
        try:
            # No clock passed: the registry measures retention on its own
            # monotonic clock. Handing it `anyio.current_time()` (monotonic
            # since loop start) or `time.time()` (wall) both mixed clocks with
            # whatever `finish` recorded -- the first made every sweep a no-op
            # and the registry grew for the life of the process.
            dropped = app.state.runs.reap()
        except Exception:                                  # noqa: BLE001
            # Never let a bookkeeping slip kill the sweep for the life of the
            # process -- that turns one bad record into an unbounded leak.
            _log.exception("run reaper pass failed")
            continue
        if dropped:
            _log.debug("reaped %d terminal run(s)", dropped)


def _announce_terminal(app, run) -> None:
    """Tell the shell a run ended, and let the registry retire it.

    Both AFTER the bookkeeping and each in its own fail-soft boundary. Inside
    `_guarded`'s try, a notification the OS refused would flip a successfully
    persisted run from `landed` to `failed`; outside any boundary, it would
    escape into the lifespan task group and cancel every sibling run. A
    notification is the least important thing a terminal run does.
    """
    registry = getattr(app.state, "runs", None)
    if registry is not None:
        try:
            registry.retire(run.id)
        except Exception:                                    # noqa: BLE001
            _log.exception("retiring run %s failed", run.id)
    sink = getattr(app.state, "on_run_terminal", None)
    if sink is None:
        return
    try:
        sink(run.id, run.state, run.labels.get("campaign", ""),
             run.labels.get("scene", ""), run.subject[1] if len(run.subject) > 1 else "",
             run.scene_identity or "")
    except Exception:                                        # noqa: BLE001
        _log.exception("terminal-run callback failed for %s", run.id)


def start(app, run, factory: Callable[[], Any]) -> None:
    """Schedule ``factory()`` as a detached run. Thread-safe.

    ``factory`` is a zero-arg callable returning the coroutine, not the
    coroutine itself: a coroutine created on the calling thread and then never
    awaited (because the portal went away mid-shutdown) emits a
    "coroutine was never awaited" warning and does no work, which is a confusing
    way to discover the portal is gone.
    """
    portal = getattr(app.state, "run_portal", None)
    if portal is None:
        raise RuntimeError(
            "no run portal; the app's lifespan is not running. Tests that need "
            "a run to execute take a lifespan-entered client.")
    portal.start_task_soon(_guarded, app, run, factory)


def cancel(app, run) -> None:
    """Ask a live run to stop. A request, not a guarantee.

    The scope is cancelled from the loop; the run ends when its provider call
    unwinds and its abort hook has finished. Callers that need to know it is
    really over wait on ``run.terminal``, which is set only after that.
    """
    # RECORDED FIRST, before anything is waited on. The wait below is bounded,
    # and review caught what happens when it expires: a route whose synchronous
    # setup is slow -- blocked on the campaign lock, say -- has not reached
    # `runner.start` yet, so there is no scope to cancel and the old code
    # returned having done nothing. The provider then started normally, after
    # the user had already pressed Stop. `_guarded` reads this the moment it
    # installs its scope, so a cancel that arrives at any point before that is
    # honoured rather than raced for.
    run.cancel_requested = True
    # Wait for the task to install its scope. Stop routinely arrives before
    # `_guarded` has run -- `start` only schedules -- and reading a missing
    # scope and returning would drop the cancellation: the provider runs to
    # completion while the cancel handler waits on `terminal`. Bounded, because
    # a run that never becomes ready is already terminal or gone; the flag above
    # is what makes an expired wait safe rather than silent.
    if run.state == "running":
        run.ready.wait(timeout=READY_TIMEOUT_SECONDS)
    scope = getattr(run, "cancel_scope", None)
    if scope is None:
        return
    portal = getattr(app.state, "run_portal", None)
    if portal is None:
        scope.cancel()
    else:
        portal.call(scope.cancel)


def release_before_start(app, run, state: str, error: dict | None = None) -> None:
    """End a run that was reserved but never scheduled.

    A producing route may reserve and then return early -- a validation failure,
    a check that would not resolve -- without ever calling ``start``. The run is
    already published and discoverable at that point, so a cancel or a poll
    arriving in that window finds a real record and would wait forever on events
    no task will ever set. Both are set here.

    The error is also written into the frame buffer, not only onto the record.
    ``start_or_existing`` hands back an existing run even when terminal, so a
    client whose response was lost re-POSTs with the same attempt id and adopts
    this one; with an empty buffer it would stream nothing at all, and a turn
    that reported a specific problem would look like a turn that silently did
    nothing.
    """
    if error is not None:
        run.error = error
        run.append_frame(_error_frame(error))
    run.finish(state)
    run.ready.set()
    run.terminal.set()
    # The pre-start release announces too. A run reserved by a route that then
    # refuses is never entered by the runner, so a demotion hung off `_guarded`
    # alone would leave the service pinned by a run that no longer exists.
    _announce_terminal(app, run)


def _error_frame(error: dict) -> str:
    """One SSE error frame, in the shape the stream already uses."""
    return f"data: {json.dumps({'error': error})}\n\n"


async def _guarded(app, run, factory: Callable[[], Any]) -> None:
    """One run, inside its own failure boundary and cancel scope.

    The boundary is load-bearing. These run on the lifespan's task group, and
    anyio cancels every sibling when one task raises, then propagates out of
    ``_lifespan`` -- so without catching here, one malformed scene would abort
    every other live run in the process and stop the backup ticker with them.
    """
    with anyio.CancelScope() as scope:
        run.cancel_scope = scope
        run.ready.set()
        # A Stop that arrived while the route was still doing synchronous setup
        # found no scope to cancel and could only leave this flag. Read here,
        # under the scope, so the cancellation lands exactly as a later one
        # would -- and the `except` below records it as `cancelled` rather than
        # as a failure.
        if run.cancel_requested:
            scope.cancel()
        outcome = None
        try:
            if run.cancel_requested:
                # A CHECKPOINT before the factory, not just a cancelled scope.
                # Cancellation only fires at an await, and a producer's first
                # statements run before its first one -- for `event_stream` that
                # is far enough to open the meter and reach the provider call.
                # `checkpoint()` rather than `sleep(0)`: same effect, and it
                # says what it is for (ASYNC115 flags the sleep as a disguised
                # one, which is exactly what it was).
                # Relying on the scope alone would let a request go out on a
                # turn the user had already stopped, which is the entire thing
                # the flag exists to prevent.
                await anyio.lowlevel.checkpoint()
            outcome = await factory()
        except anyio.get_cancelled_exc_class():
            # A deliberate Stop, or shutdown. The producer's own `finally` has
            # already run by the time this is caught, which is what makes
            # "the partial is persisted before the slot frees" true.
            run.finish("cancelled")
            raise
        except Exception as exc:                            # noqa: BLE001
            _log.exception("run %s failed", run.id)
            error = {"kind": "run_failed", "detail": str(exc)}
            run.error = error
            # BUFFERED, not just recorded. `tail_response` sees the terminal
            # state, drains what is there and closes -- so a failure recorded
            # only on the record reaches every subscriber, live and replaying,
            # as an unexplained EOF. The client cannot tell that from a stream
            # someone cut, and shows an interrupted turn instead of the reason.
            # A handled `LLMError` emits its own frame; this is the path that
            # had none.
            run.append_frame(_error_frame(error))
            run.finish("failed")
        else:
            # A producer that handles its own failure RETURNS an outcome rather
            # than raising -- `_fence_stream` emits an error frame for an
            # LLMError, a StoreBusy during finalize, or an identity-fence
            # refusal, and then returns normally. Inferring success from "did
            # not raise" marks those landed and fires a success notification for
            # a reply that was never persisted.
            if isinstance(outcome, dict) and outcome.get("state"):
                run.error = outcome.get("error")
                run.result = outcome.get("result")
                run.finish(outcome["state"])
            elif run.state == "running":
                run.finish("landed")
    # OUTSIDE the scope, and after the abort hook: a cancelled scope suppresses
    # everything inside it, so setting this within would never run on the path
    # that needs it most.
    run.terminal.set()
    _announce_terminal(app, run)
