"""Process/root ownership of disposable compiled artifacts (03-S1).

Clock and timer seams drive idle closure without waiting. Root transitions
are deliberately paused before pointer publication, when reopening the old
database would strand a handle. Fork cases use bounded child completion.
"""

import asyncio
import os
import select
import signal
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import grimoire.store as store
from grimoire import main
from grimoire.routes import config
from grimoire.store import compiled, logs, maintenance_reports, paths

_REAL_DEVICE_KEY = maintenance_reports.device_key


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr(compiled, "_state", compiled._State())
    monkeypatch.setattr(compiled, "_build", lambda: "b" * 64)
    monkeypatch.setattr(maintenance_reports, "device_key", lambda root: "a" * 64)
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    kind = compiled.Kind("search", 1, (), (), True)
    monkeypatch.setitem(compiled.KINDS, kind.name, kind)
    yield compiled
    compiled.close()


@pytest.fixture
def key(cache):
    return cache.make_key(cache.KINDS["search"], [("document", "a" * 64)], params={})


def _put(cache, root, key):
    with cache.batch(root) as request:
        request.put(key, "Realm")
    return cache._state.session


def test_two_workers_share_one_connection_without_locking_caller_compute(cache, key, tmp_path):
    session = _put(cache, tmp_path, key)
    compute = threading.Barrier(2)

    def read():
        with cache.batch(tmp_path) as request:
            answer = request.lookup(key)
            compute.wait(timeout=3)
            assert cache._state.lock.acquire(timeout=0.2)
            cache._state.lock.release()
            return answer.value if answer.hit else "Realm", cache._state.session

    with ThreadPoolExecutor(max_workers=2) as workers:
        answers = list(workers.map(lambda _: read(), range(2)))
    assert all(answer == "Realm" and owned is session for answer, owned in answers)


def test_idle_closes_handle_and_stale_timer_cannot_close_replacement(cache, key, tmp_path, monkeypatch):
    assert hasattr(cache, "_clock"), "idle clock seam is missing"
    now, timers = [0.0], []

    class Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback, self.cancelled = delay, callback, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(cache, "_clock", lambda: now[0])
    monkeypatch.setattr(cache, "_timer", Timer)
    session = _put(cache, tmp_path, key)
    assert len(timers) == 1 and timers[0].delay == 30.0
    now[0] = 20.0
    with cache.batch(tmp_path) as request:
        assert request.lookup(key).hit
    assert len(timers) == 1
    now[0] = 30.0
    timers[0].callback()
    assert cache._state.session is session and len(timers) == 2
    now[0] = 50.0
    timers[1].callback()
    assert cache._state.session is None
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")
    replacement = _put(cache, tmp_path, key)
    timers[0].callback()
    assert cache._state.session is replacement
    cache.close()
    assert timers[-1].cancelled


@pytest.mark.parametrize("defer", [False, True])
def test_root_change_drops_prepared_batch(cache, key, tmp_path, defer):
    assert hasattr(cache, "root_change"), "root-switch fence is missing"
    session = _put(cache, tmp_path, key)
    with cache.batch(tmp_path) as prepared:
        if not defer:
            prepared.put(key, "Mara")
        with cache.root_change():
            assert cache._state.session is None
            assert cache._state.lock.acquire(blocking=False)
            cache._state.lock.release()
        prepared.put(key, "Winifred")
        assert not prepared.lookup(key).hit
    assert cache._state.session is None
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")
    with cache.batch(tmp_path) as reader:
        assert reader.lookup(key).value == "Realm"


def test_batch_entered_inside_nested_fence_stays_disabled(cache, key, tmp_path):
    assert hasattr(cache, "root_change"), "root-switch fence is missing"
    with cache.root_change():
        ctx = cache.batch(tmp_path)
        request = ctx.__enter__()
        with cache.root_change():
            pass
        assert not request.lookup(key).hit
    request.put(key, "Realm")
    assert not request.lookup(key).hit
    ctx.__exit__(None, None, None)
    assert cache._state.session is None and not (tmp_path / ".cache").exists()


def test_failed_root_change_is_harmless(cache, key, tmp_path):
    assert hasattr(cache, "root_change"), "root-switch fence is missing"
    _put(cache, tmp_path, key)
    with pytest.raises(ValueError, match="root unavailable"), cache.root_change():
        raise ValueError("root unavailable")
    with cache.batch(tmp_path) as request:
        assert request.lookup(key).value == "Realm"


def test_root_switch_cannot_reopen_old_handle(cache, key, tmp_path, monkeypatch):
    session = _put(cache, tmp_path, key)
    app = main.create_app()
    app.state.start_inference_migration = lambda: None
    entered, release = threading.Event(), threading.Event()
    inside = []

    def paused(path):
        assert cache._state.session is None
        inside.append(cache.batch(tmp_path))
        inside[-1].__enter__()
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(store, "set_data_dir", paused)
    request = Request({"type": "http", "app": app})
    with ThreadPoolExecutor(max_workers=1) as workers:
        task = workers.submit(config.put_data_dir, config.DataDirUpdate(data_dir=str(tmp_path)), request)
        try:
            assert entered.wait(3)
            with cache.batch(tmp_path) as reader:
                assert not reader.lookup(key).hit
            assert cache._state.session is None
        finally:
            release.set()
        task.result(timeout=3)
    inside[0].__exit__(None, None, None)
    assert cache._state.session is None
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")


def test_event_loop_never_opens_or_executes_sqlite(cache, key, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("event loop reached SQLite")

    monkeypatch.setattr(cache._sqlite, "connect", forbidden)

    async def drive():
        with cache.batch(tmp_path) as request:
            assert not request.lookup(key).hit
            request.put(key, "Realm")
            request.flush()

    asyncio.run(drive())
    assert not (tmp_path / ".cache").exists()


def test_shutdown_close_runs_on_worker(cache, key, tmp_path, monkeypatch):
    session = _put(cache, tmp_path, key)
    app = main.create_app()
    actual, calls, loop_thread = cache.close, [], []

    def close():
        calls.append(threading.get_ident())
        assert getattr(app.state, "run_loop", None) is None
        actual()

    monkeypatch.setattr(cache, "close", close)

    async def drive():
        loop_thread.append(threading.get_ident())
        async with main._lifespan(app):
            pass

    anyio.run(drive)
    assert len(calls) == 1 and calls[0] != loop_thread[0]
    assert cache._state.session is None
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")


def test_pid_change_resets_inherited_state_without_taking_old_lock(cache, key, tmp_path, monkeypatch):
    parent = _put(cache, tmp_path, key)
    old = cache._state
    old.build_checked, old.build = True, "b" * 64
    old.warned.add("database")
    with old.lock:
        monkeypatch.setattr(cache.os, "getpid", lambda: old.pid + 1)
        with cache.batch(tmp_path) as child:
            assert child.lookup(key).value == "Realm"
    assert cache._state is not old and cache._state.lock is not old.lock
    assert cache._state.session is not parent
    assert not cache._state.warned
    # A child runs its parent's code: it keeps that BUILD rather than
    # fingerprinting whatever sources are on disk now.
    assert (cache._state.build_checked, cache._state.build) == (True, "b" * 64)
    # Simulated child owns a new connection; explicitly clean the real parent.
    parent.conn.close()


@pytest.mark.parametrize("owner", ["device", "append", "size"])
def test_helper_pid_fallback_resets_inherited_locks(monkeypatch, tmp_path, owner):
    module = maintenance_reports if owner == "device" else logs
    assert hasattr(module, "_pid"), "helper process ownership is missing"
    old_pid = module._pid
    guard = module._DEVICE_LOCK if owner == "device" else getattr(module, f"_{owner}_guard")
    with guard:
        monkeypatch.setattr(module.os, "getpid", lambda: old_pid + 1)
        if owner == "device":
            assert len(module.device_id()) == 32
            assert module._DEVICE_LOCK is not guard
        else:
            module._busy.on = True
            module._written["Realm"] = 99
            module._capped.add("Realm")
            assert module.record("warning", "store.compiled", "Compiled cache unavailable",
                                 failure_kind="database") is not None
            assert getattr(module, f"_{owner}_guard") is not guard
            assert "Realm" not in module._written and "Realm" not in module._capped


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork is unavailable")
@pytest.mark.parametrize("held", ["connection", "device", "append", "size"])
def test_fork_reopens_with_fresh_locks(cache, key, tmp_path, monkeypatch, held):
    monkeypatch.setattr(maintenance_reports, "device_key", _REAL_DEVICE_KEY)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    parent = _put(cache, tmp_path, key)
    cache._state.build_checked, cache._state.build = True, "b" * 64
    cache._state.warned.add("database")
    guards = {"connection": cache._state.lock, "device": maintenance_reports._DEVICE_LOCK,
              "append": logs._append_guard, "size": logs._size_guard}
    entered, release = threading.Event(), threading.Event()

    def holder():
        with guards[held]:
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(2)
    # The parent hook waits for a short SQLite operation. Helper guards stay
    # held across fork so the child's owner must reset them.
    if held == "connection":
        threading.Timer(0.02, release.set).start()
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        try:
            assert cache._state.build == "b" * 64 and not cache._state.devices
            assert not cache._state.warned and cache._state.session is None
            with cache.batch(tmp_path) as request:
                assert request.lookup(key).value == "Realm"
            assert cache._state.devices
            if held in ("append", "size"):
                cache._sqlite = None
                with cache.batch(tmp_path) as request:
                    assert not request.lookup(key).hit
                text = "".join(path.read_text(encoding="utf-8")
                               for path in logs.log_dir().glob("*.jsonl"))
                assert "sqlite_unavailable" in text
                assert "Realm" not in text and str(tmp_path) not in text
            os.write(writer, b"ok")
        except BaseException as error:  # noqa: BLE001 -- child must exit, never resume pytest
            os.write(writer, type(error).__name__.encode("ascii"))
        finally:
            os._exit(0)
    os.close(writer)
    release.set()
    thread.join(timeout=2)
    try:
        ready, _, _ = select.select([reader], [], [], 5)
        assert ready, "forked lookup did not complete within five seconds"
        assert os.read(reader, 128) == b"ok"
    finally:
        os.close(reader)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    assert not thread.is_alive()
    with pytest.raises(sqlite3.ProgrammingError):
        parent.conn.execute("SELECT 1")


def test_failed_route_root_initialization_returns_existing_error(cache, key, tmp_path, monkeypatch):
    monkeypatch.delenv("GRIMOIRE_HOME", raising=False)
    pointer = tmp_path / "pointer.json"
    monkeypatch.setattr(paths, "pointer_path", lambda: pointer)
    old = paths.set_data_dir(tmp_path / "old")
    before = pointer.read_bytes()
    _put(cache, old, key)
    target = tmp_path / "target"
    target.mkdir()
    (target / "worlds").write_text("Realm", encoding="utf-8")
    app = main.create_app()
    request = Request({"type": "http", "app": app})
    with pytest.raises(HTTPException) as caught:
        config.put_data_dir(config.DataDirUpdate(data_dir=str(target)), request)
    assert caught.value.status_code == 400 and caught.value.detail["kind"] == "data_dir"
    assert pointer.read_bytes() == before and paths.home() == old
    assert cache._state.session is None
    with cache.batch(old) as request:
        assert request.lookup(key).value == "Realm"


def test_existing_connection_is_unused_on_event_loop(cache, key, tmp_path):
    session = _put(cache, tmp_path, key)
    actual = session.conn

    class Forbidden:
        def execute(self, *args):
            raise AssertionError("event loop reached an existing SQLite handle")

        def close(self):
            actual.close()

    session.conn = Forbidden()

    async def drive():
        with cache.batch(tmp_path) as request:
            assert not request.lookup(key).hit
            request.put(key, "Mara")

    asyncio.run(drive())
    session.conn = actual
    with cache.batch(tmp_path) as request:
        assert request.lookup(key).value == "Realm"


def test_cancelled_lifespan_still_closes_compiled_resources(cache, key, tmp_path):
    session = _put(cache, tmp_path, key)
    app = main.create_app()

    async def drive():
        with anyio.CancelScope() as enclosing:
            ctx = main._lifespan(app)
            await ctx.__aenter__()
            enclosing.cancel()
            await ctx.__aexit__(None, None, None)

    anyio.run(drive)
    assert cache._state.session is None, "cancelled shutdown skipped the worker close"
    with pytest.raises(sqlite3.ProgrammingError):
        session.conn.execute("SELECT 1")


def test_default_home_resolution_cannot_adopt_an_epoch_after_root_switch(cache, key, tmp_path, monkeypatch):
    old = tmp_path / "Realm"
    old.mkdir()
    _put(cache, old, key)
    database = cache._state.session.path
    selected = threading.Event()
    resume = threading.Event()

    def paused_home():
        root = old
        selected.set()
        assert resume.wait(5)
        return root

    monkeypatch.setattr(paths, "home", paused_home)

    def lookup():
        with cache.batch() as request:
            found = request.lookup(key)
            request.put(key, "Winifred")
            return found.hit

    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(lookup)
        assert selected.wait(5)
        with cache.root_change():
            pass
        resume.set()
        assert pending.result(timeout=5) is False
    assert cache._state.session is None
    conn = sqlite3.connect(database)
    try:
        assert conn.execute("SELECT payload FROM artifacts").fetchone() == ('"Realm"',)
    finally:
        conn.close()


def test_timer_start_failure_discards_closed_session_and_can_retry(cache, tmp_path, monkeypatch):
    real_timer = cache._timer
    class FailedTimer:
        def __init__(self, *args):
            self.daemon = False
        def start(self):
            raise RuntimeError('thread creation unavailable')
        def cancel(self):
            pass
    monkeypatch.setattr(cache, '_timer', FailedTimer)
    with cache._state.lock:
        assert cache._open_locked(tmp_path) is None
    assert cache._state.session is None
    monkeypatch.setattr(cache, '_timer', real_timer)
    with cache._state.lock:
        session = cache._open_locked(tmp_path)
        assert session is not None
        assert session.conn.execute('SELECT 1').fetchone() == (1,)


def test_idle_reschedule_thread_failure_releases_the_session(cache, tmp_path, monkeypatch):
    session = _put(cache, tmp_path, cache.make_key(cache.KINDS['search'], [('document', 'a' * 64)], params={}))
    monkeypatch.setattr(cache, '_clock', lambda: session.last_used)
    def failed_timer(*args):
        raise RuntimeError('idle reschedule thread unavailable')
    monkeypatch.setattr(cache, '_timer', failed_timer)
    cache._idle(session)
    assert cache._state.session is None


def test_build_is_pinned_at_startup_not_at_first_use(monkeypatch, tmp_path):
    monkeypatch.setattr(compiled, "_state", compiled._State())
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    on_disk = ["a" * 64]
    monkeypatch.setattr(compiled.compiled_build, "fingerprint", lambda *a, **k: on_disk[0])
    app = main.create_app()

    async def drive():
        async with main._lifespan(app):
            # Sources edited after startup (a `git pull` under the server)
            # do not move the BUILD this process files its artifacts under.
            on_disk[0] = "b" * 64

    anyio.run(drive)
    assert compiled._build() == "a" * 64


def test_fork_child_keeps_the_parent_build(monkeypatch):
    monkeypatch.setattr(compiled, "_state", compiled._State())
    compiled._state.build_checked, compiled._state.build = True, "b" * 64
    compiled._after_fork_child()
    assert (compiled._state.build_checked, compiled._state.build) == (True, "b" * 64)


def test_disabled_cache_primes_nothing(monkeypatch):
    monkeypatch.setattr(compiled, "_state", compiled._State())
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    monkeypatch.setattr(compiled.compiled_build, "fingerprint", lambda *a, **k: pytest.fail("fingerprinted"))
    compiled.prime_build()
    assert not compiled._state.build_checked


def test_startup_acts_on_a_delete_published_elsewhere(cache, key, tmp_path, monkeypatch):
    # Codex (PR 506): a device whose library opens no batch -- no body is long
    # enough to count through the cache -- must still purge at its next open.
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    session = _put(cache, tmp_path, key)
    path = session.path
    cache.close()
    assert cache._publish_marker(tmp_path) is not None   # another device's delete
    app = main.create_app()

    async def drive():
        async with main._lifespan(app):
            pass

    anyio.run(drive)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM artifacts").fetchone() == (0,)


def test_opening_a_library_without_a_cache_creates_nothing(cache, tmp_path):
    root = tmp_path / "Realm"
    root.mkdir()
    cache.open_library(root)
    assert list(root.iterdir()) == []


def test_a_missing_build_is_reported_at_first_use_not_at_startup(monkeypatch, tmp_path):
    # A warning is a log row, and a log row would create the store before the
    # first request that needs it.
    monkeypatch.setattr(compiled, "_state", compiled._State())
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    monkeypatch.setattr(compiled.compiled_build, "fingerprint", lambda *a, **k: None)
    rows = []
    monkeypatch.setattr(compiled.logs, "record", lambda *args, **fields: rows.append(fields.get("failure_kind")))
    compiled.prime_build()
    assert rows == []
    assert compiled._available() is False
    assert rows == ["build"]
