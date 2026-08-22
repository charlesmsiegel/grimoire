"""Starting a run: the thread bridge, the per-run boundary, the reaper.

The mechanism the whole feature rests on. Every streaming route in this app is
`def`, not `async def`, so FastAPI runs it in a threadpool worker -- and
`start_soon` is not thread-safe from there.
"""

from __future__ import annotations

import contextlib
import threading
import time

import anyio
import pytest
from fastapi.testclient import TestClient

from grimoire import runner
from grimoire.main import create_app
from grimoire.routes import runs as runs_mod

SCENE = ("scene", "saltmarch", "0001--mara")
OTHER = ("scene", "saltmarch", "0002--winifred")
WORLD = ("world", "realm")
LABELS = {"campaign": "Saltmarch", "scene": "Mara"}


@pytest.fixture
def app_with_lifespan_factory(monkeypatch, tmp_path):
    """The lifespan as a context manager, so a test can EXIT it and watch
    shutdown cancel what is still live."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    @contextlib.contextmanager
    def factory():
        app = create_app()
        with TestClient(app):          # `with`, so startup and shutdown run
            yield app

    return factory


@pytest.fixture
def app_with_lifespan(app_with_lifespan_factory):
    with app_with_lifespan_factory() as app:
        yield app


def _wait_terminal(app, run_id, timeout=5.0):
    """Poll the registry until the run leaves `running`; fail rather than hang."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for run in app.state.runs._runs.values():
            if run.id == run_id and run.state != "running":
                return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never became terminal")


def test_the_registry_exists_without_a_lifespan(monkeypatch, tmp_path):
    """`conftest.client` is a bare `TestClient(app)` and never emits startup.
    A registry created only in the lifespan would be missing for every route
    test and every migrated handler -- an AttributeError on `app.state` before
    any assertion runs."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    assert app.state.runs is not None
    run, started = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    assert started and run.ready is not None


def test_start_without_a_portal_says_so_instead_of_attribute_erroring(monkeypatch, tmp_path):
    """A test that picks the wrong fixture should say so in one line, not send
    its author into main.py."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)

    async def work():
        pass

    with pytest.raises(RuntimeError, match="portal"):
        runner.start(app, run, work)


def test_start_works_from_a_synchronous_handler_thread(app_with_lifespan):
    """The streaming routes are `def`, so FastAPI runs them in a threadpool
    worker. This is the test that would have caught the whole design failing at
    runtime."""
    app = app_with_lifespan
    done = threading.Event()

    async def work():
        done.set()

    def from_worker_thread():
        run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
        runner.start(app, run, work)

    threading.Thread(target=from_worker_thread).start()
    assert done.wait(timeout=5), "the run never reached the lifespan loop"


def test_one_runner_raising_does_not_cancel_its_siblings(app_with_lifespan):
    """anyio cancels all siblings and propagates out of `_lifespan`, so without
    a per-run boundary one malformed scene would abort every other live run and
    stop the backup ticker."""
    app = app_with_lifespan

    async def boom():
        raise RuntimeError("one bad turn")

    async def fine():
        await anyio.sleep(0.05)

    bad, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    good, _ = app.state.runs.start_or_existing(OTHER, "turn", "chat", "a2", "i", LABELS)
    runner.start(app, bad, boom)
    runner.start(app, good, fine)

    # NOT a flag the coroutine sets: `_guarded` writes `landed` only after the
    # factory returns, so waking on such a flag and asserting the state is a
    # race that goes green idle and red under load -- in this test, which is
    # about isolation and would then be blamed for a defect it does not have.
    _wait_terminal(app, bad.id)
    _wait_terminal(app, good.id)
    assert bad.state == "failed"
    assert good.state == "landed"


def test_a_failed_run_records_why(app_with_lifespan):
    app = app_with_lifespan

    async def boom():
        raise RuntimeError("one bad turn")

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, boom)
    _wait_terminal(app, run.id)
    assert run.state == "failed"
    assert run.error and "one bad turn" in str(run.error)


def test_both_handshake_events_are_set_once_a_run_is_terminal(app_with_lifespan):
    """A cancel or a poll waiting on either event must never wait forever."""
    app = app_with_lifespan

    async def work():
        await anyio.lowlevel.checkpoint()

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, work)
    _wait_terminal(app, run.id)
    assert run.ready.is_set() and run.terminal.is_set()


def test_cancel_stops_a_live_run_and_it_ends_cancelled(app_with_lifespan):
    app = app_with_lifespan
    started = threading.Event()

    async def slow():
        started.set()
        await anyio.sleep(30)

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, slow)
    assert started.wait(timeout=5)

    runner.cancel(app, run)
    _wait_terminal(app, run.id)
    assert run.state == "cancelled"


def test_terminal_is_set_only_after_the_abort_hook_finishes(app_with_lifespan):
    """The slot stays held until the partial is persisted. Setting `terminal`
    first lets a fast re-send race that write."""
    app = app_with_lifespan
    started = threading.Event()
    seen = {}

    async def slow():
        started.set()
        try:
            await anyio.sleep(30)
        finally:
            # The abort hook's stand-in. Sample `terminal` FROM INSIDE it: an
            # assertion made after the run settles is true either way and
            # proves nothing about the order -- which is how the first version
            # of this test passed against an implementation that set `terminal`
            # before the hook ran.
            seen["terminal_already_set"] = run.terminal.is_set()

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, slow)
    assert started.wait(timeout=5)
    runner.cancel(app, run)
    _wait_terminal(app, run.id)

    assert run.terminal.is_set()
    assert seen["terminal_already_set"] is False, (
        "terminal was set before the abort hook finished, so a fast re-send "
        "could race the partial-persist")


def test_release_before_start_marks_the_run_terminal_and_sets_both_events(app_with_lifespan):
    """Task 5 allows a producing route to return early -- a validation failure,
    a check that could not resolve -- without ever scheduling the runner. A
    discovery or cancel landing in that window finds a real run and would
    otherwise wait forever on events no task will set."""
    app = app_with_lifespan
    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)

    runner.release_before_start(app, run, state="failed",
                                error={"kind": "check_error", "detail": "no"})

    assert run.state == "failed"
    assert run.ready.is_set() and run.terminal.is_set()
    assert run.error == {"kind": "check_error", "detail": "no"}


def test_an_early_error_is_replayable_by_the_same_attempt(app_with_lifespan):
    """`start_or_existing` returns an existing run even when terminal, so a
    client whose response was lost re-POSTs and adopts this record. If the
    early error was never buffered it streams NOTHING -- an empty terminal
    stream where the first caller got a specific, actionable error."""
    app = app_with_lifespan
    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.release_before_start(app, run, state="failed",
                                error={"kind": "check_error", "detail": "no"})

    again, started = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    assert again is run and not started
    assert again.frames, "the early error left nothing to replay"
    assert "check_error" in "".join(f["raw"] for f in again.frames)


def test_shutdown_cancels_live_runs_and_they_flush(app_with_lifespan_factory):
    flushed = []

    async def slow():
        try:
            await anyio.sleep(30)
        finally:
            flushed.append("partial")

    with app_with_lifespan_factory() as app:
        run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
        runner.start(app, run, slow)
        # Let the task actually reach its sleep, or shutdown may cancel a task
        # that never entered the `try` and the flush would not be the thing
        # under test.
        assert run.ready.wait(timeout=5)
    assert flushed == ["partial"]


def test_reap_drops_a_stale_terminal_run(app_with_lifespan):
    app = app_with_lifespan
    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    # Relative to NOW, never an absolute constant: `time.monotonic()` counts
    # from boot, so on a freshly-started CI runner it can be under
    # REAP_SECONDS -- making the cutoff negative and `0.0` NEWER than it. Green
    # on a long-lived machine, red on a fresh one.
    run.finish("landed", at=0.0,
               monotonic_at=time.monotonic() - runs_mod.REAP_SECONDS - 1)

    assert app.state.runs.reap(now=time.monotonic()) == 1
    assert app.state.runs.get(run.id, SCENE) is None


def test_the_reaper_loop_itself_drops_a_stale_run(monkeypatch, tmp_path,
                                                    app_with_lifespan_factory):
    """Drive the loop, not `reap`.

    Calling `reap` directly proves the registry can drop a run; it says nothing
    about whether the background sweep ever does. The first version of this
    module passed `anyio.current_time()` -- a monotonic clock reading a few
    thousand -- into a comparison against wall-clock `ended_at` values around
    1.8e9, so every sweep found nothing and the registry grew for the life of
    the process. A test that calls `reap` itself cannot see that, and did not.
    """
    monkeypatch.setattr(runner, "REAP_INTERVAL_SECONDS", 0.05)
    with app_with_lifespan_factory() as app:
        run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
        # A REALISTIC stamp -- what `finish()` would really have written. `0.0`
        # is below both a wall-clock and a monotonic cutoff, so it gets reaped
        # either way and the test proves nothing; that is how the first version
        # of this test passed against the very bug it was written for.
        run.finish("landed", at=time.time() - runs_mod.REAP_SECONDS - 1,
                   monotonic_at=time.monotonic() - runs_mod.REAP_SECONDS - 1)

        # And one that finished a moment ago, which must SURVIVE. Without this
        # half the test passes against a sweep that reaps everything it sees --
        # which is what mixing a wall clock into a monotonic comparison does,
        # in the opposite direction from the original bug.
        recent, _ = app.state.runs.start_or_existing(OTHER, "turn", "chat", "a2", "i", LABELS)
        recent.finish("landed")

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if app.state.runs.get(run.id, SCENE) is None:
                assert app.state.runs.get(recent.id, OTHER) is recent, (
                    "the sweep reaped a run that had only just finished")
                return
            time.sleep(0.02)
        raise AssertionError("the reaper never dropped a long-terminal run")


def test_the_factorys_terminal_outcome_is_applied(app_with_lifespan):
    """`_fence_stream` handles its own failures: it emits an error frame and
    RETURNS, reporting the outcome rather than raising. Inferring success from
    'did not raise' marks a run landed and fires a success notification for a
    reply that was never persisted."""
    app = app_with_lifespan

    async def handled_failure():
        return {"state": "failed", "error": {"kind": "llm_error", "detail": "upstream"}}

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, handled_failure)
    _wait_terminal(app, run.id)

    assert run.state == "failed"
    assert run.error == {"kind": "llm_error", "detail": "upstream"}


def test_a_factory_returning_nothing_still_lands(app_with_lifespan):
    """Most producers report nothing and simply finish."""
    app = app_with_lifespan

    async def quiet():
        return None

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, quiet)
    _wait_terminal(app, run.id)
    assert run.state == "landed"


def test_cancel_immediately_after_start_still_stops_the_run(app_with_lifespan):
    """Stop can arrive before `_guarded` has installed the cancel scope. Reading
    a missing scope and returning silently means the provider runs to
    completion while the cancel handler waits on `terminal` -- the user's
    explicit Stop ignored, in the ordinary scheduling race."""
    app = app_with_lifespan

    async def slow():
        await anyio.sleep(30)

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.start(app, run, slow)
    runner.cancel(app, run)          # no wait: race the scope install on purpose
    _wait_terminal(app, run.id)
    assert run.state == "cancelled"


def test_a_cancel_before_the_run_is_even_scheduled_is_not_lost(app_with_lifespan,
                                                              monkeypatch):
    """Stop during the route's SYNCHRONOUS setup, before `runner.start` runs.

    The readiness wait covers the ordinary race, but it is bounded -- and a
    route blocked on the campaign lock can outlast it. `ready.wait` then expired
    with no scope installed and `cancel` returned having recorded nothing, so
    the provider started normally after the user had already stopped it. The
    flag is what makes an expired wait safe instead of silent.

    `start` is deliberately called AFTER `cancel` here: that is the window, and
    a test that started first would only re-run the race above.
    """
    app = app_with_lifespan
    ran = threading.Event()
    # A real expiry, shortened. Nothing will ever set `ready` here -- `start`
    # has not been called -- so the wait runs its full course either way; this
    # only keeps the test from spending `READY_TIMEOUT_SECONDS` proving it.
    monkeypatch.setattr(runner, "READY_TIMEOUT_SECONDS", 0.05)

    async def should_never_run():
        ran.set()
        await anyio.sleep(30)

    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    runner.cancel(app, run)
    runner.start(app, run, should_never_run)

    _wait_terminal(app, run.id)
    assert run.state == "cancelled", f"the run was {run.state}: the Stop was dropped"
    assert not ran.is_set(), "the provider started after the user cancelled it"


def test_setup_is_undone_when_a_stop_lands_before_the_producer_starts(app_with_lifespan):
    """A Stop during a route's synchronous setup leaves only `cancel_requested`,
    and `_guarded` honours it with a checkpoint BEFORE entering the producer.
    The producer's `finally` is where a route's destructive setup is undone --
    regenerate removes the old reply and hands the stream the way to put it back
    -- so on this one path nothing undoes it and the reroll ends `cancelled`
    with the transcript permanently one reply short.
    """
    app = app_with_lifespan
    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
    entered = []
    undone = []

    async def producer():
        entered.append(True)
        if False:
            yield ""

    run.cancel_requested = True
    runs_mod.start_detached(app, run, producer, on_unstarted=lambda: undone.append(True))
    assert run.terminal.wait(5)

    assert run.state == "cancelled"
    assert entered == [], "the producer ran despite a Stop already recorded"
    assert undone == [True], "the route's setup was never undone"


def test_a_cancel_after_the_producer_started_leaves_teardown_to_the_producer(
        app_with_lifespan):
    """The counterweight, and the reason this is not simply always called on a
    cancel: once the producer is entered, its own `finally` owns the teardown.
    Running both would restore the old reply twice -- appending a duplicate of
    the very text the reroll was replacing.

    Cancelled while the producer is RUNNING, which is the discriminating case.
    A producer that simply finishes never reaches the cancelled branch at all,
    so a test built on one passes whatever the condition says.
    """
    app = app_with_lifespan
    run, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a2", "i", LABELS)
    undone = []
    own_teardown = []
    started = threading.Event()

    async def producer():
        try:
            started.set()
            yield _sse_frame()
            await anyio.sleep(30)
        finally:
            own_teardown.append(True)

    runs_mod.start_detached(app, run, producer, on_unstarted=lambda: undone.append(True))
    assert started.wait(5)
    runner.cancel(app, run)
    assert run.terminal.wait(5)

    assert run.state == "cancelled"
    assert own_teardown == [True], "the producer's own teardown did not run"
    assert undone == [], "the unstarted hook fired for a producer that had started"


def _sse_frame() -> str:
    return 'data: {"done": true}\n\n'


def test_the_terminal_sink_is_told_what_kind_of_work_landed(app_with_lifespan):
    """The completion notification's wording turns on this.

    A `turn` produces a reply and a `review` produces an end-of-scene form, so
    a shell told only "landed" announces "New Post" for an absorb -- sending
    the reader into the scene looking for narration that was never generated.
    Untested until #396 added a second notifying class, at which point the
    signature stopped being a formality.
    """
    app = app_with_lifespan
    seen = []
    app.state.on_run_terminal = lambda *args: seen.append(args)

    async def done():
        return None

    for cls, kind in (("turn", "chat"), ("review", "absorb")):
        run, _ = app.state.runs.start_or_existing(
            SCENE, cls, kind, None, "identity-1", LABELS)
        runner.start(app, run, done)
        _wait_terminal(app, run.id)

    assert [(a[1], a[2]) for a in seen] == [("landed", "turn"), ("landed", "review")]
    # ...and the rest of what a notification needs to be worth tapping, which
    # the class was inserted in the middle of.
    assert seen[0][3:] == ("Saltmarch", "Mara", "saltmarch", "identity-1")


def test_a_failing_terminal_sink_does_not_fail_the_run(app_with_lifespan):
    """A notification is the least important thing a terminal run does: an OS
    that refuses one must not flip a successfully persisted run to `failed`."""
    app = app_with_lifespan

    def refuse(*_args):
        raise RuntimeError("no notifications here")

    app.state.on_run_terminal = refuse

    async def done():
        return None

    run, _ = app.state.runs.start_or_existing(SCENE, "review", "absorb", None, "i", LABELS)
    runner.start(app, run, done)
    assert _wait_terminal(app, run.id).state == "landed"
