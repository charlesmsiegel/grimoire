"""The write set (spec 05-C4): what `store.atomic` publishes, collected per
boundary and handed off as a frozen snapshot.

Placeholder names only (Realm, Saltmarch, Mara); every store is a `tmp_path`.
"""

from __future__ import annotations

import contextvars
import threading
import time

import anyio
import pytest
from fastapi.testclient import TestClient

from grimoire import cache_warm, runner
from grimoire.main import create_app
from grimoire.store import atomic, writeset

SCENE = ("scene", "saltmarch", "0001--mara")
LABELS = {"campaign": "Saltmarch", "scene": "Mara"}


class Recorder:
    """Stands in for the write-through queue: records every snapshot offered."""

    def __init__(self) -> None:
        self.offers: list[frozenset[str]] = []
        self.lock = threading.Lock()

    def offer(self, snapshot):
        assert isinstance(snapshot, frozenset)
        with self.lock:
            self.offers.append(snapshot)

    def wait_for(self, count: int, timeout: float = 5.0) -> list[frozenset[str]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.offers) >= count:
                    return list(self.offers)
            time.sleep(0.01)
        raise AssertionError(f"expected {count} offers, saw {self.offers}")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    (tmp_path / "worlds" / "realm").mkdir(parents=True)
    return tmp_path


# -- the writers ------------------------------------------------------------


def test_each_writer_notes_its_path_after_success(home):
    target = home / "worlds" / "realm"
    with writeset.collecting() as scope:
        atomic.write_text(target / "a.md", "a")
        atomic.write_bytes(target / "b.bin", b"b")
        atomic.append_line(target / "c.jsonl", "{}")
        with atomic.streaming_write(target / "d.zip") as f:
            f.write(b"d")
    assert scope.close() == {str(target / name) for name in ("a.md", "b.bin", "c.jsonl", "d.zip")}


def test_a_write_that_raises_notes_nothing(home):
    missing = home / "nowhere" / "x.md"
    with writeset.collecting() as scope:
        with pytest.raises(OSError):
            atomic.write_text(missing, "x")
        with pytest.raises(OSError):
            atomic.write_bytes(missing, b"x")
        with pytest.raises(OSError):
            atomic.append_line(missing, "x")
        with pytest.raises(RuntimeError), atomic.streaming_write(home / "worlds" / "realm" / "e.zip") as f:
            f.write(b"partial")
            raise RuntimeError("interrupted")
    assert scope.close() == frozenset()
    assert not (home / "worlds" / "realm" / "e.zip").exists()


def test_nested_scopes_both_receive_and_isolated_replaces(home):
    path = home / "worlds" / "realm" / "world.md"
    with writeset.collecting() as outer:
        with writeset.collecting() as inner:
            atomic.write_text(path, "one")
        with writeset.collecting(isolated=True) as alone:
            atomic.write_text(path.with_name("lore.md"), "two")
    assert inner.close() == {str(path)}
    assert outer.close() == {str(path)}
    assert alone.close() == {str(path.with_name("lore.md"))}


def test_no_scope_records_nothing(home):
    atomic.write_text(home / "worlds" / "realm" / "world.md", "x")   # must not raise
    with writeset.collecting() as scope:
        pass
    assert scope.close() == frozenset()


def test_a_note_into_a_closed_scope_adds_nothing_from_any_thread(home):
    with writeset.collecting() as scope:
        snapshot = scope.close()
        writeset.note(home / "late.md")
        worker = threading.Thread(target=lambda: scope._add(str(home / "thread.md")))
        worker.start()
        worker.join()
    assert snapshot == frozenset()
    assert scope.close() == frozenset()


def test_a_closed_snapshot_is_frozen_while_threads_keep_writing(home):
    """No "set changed size during iteration": the snapshot is a copy."""
    stop = threading.Event()
    with writeset.collecting() as scope:
        def spam():
            n = 0
            while not stop.is_set():
                writeset.note(f"/x/{n}")
                n += 1
        # Each thread runs in a copy of this context, as a worker would.
        ctx_threads = [threading.Thread(target=contextvars.copy_context().run, args=(spam,))
                       for _ in range(4)]
        for t in ctx_threads:
            t.start()
        time.sleep(0.02)
        snapshot = scope.close()
        size = len(snapshot)
        list(snapshot)
        time.sleep(0.02)
        stop.set()
        for t in ctx_threads:
            t.join()
    assert len(snapshot) == size


def test_a_write_in_a_worker_thread_lands_in_the_callers_scope(home):
    path = home / "worlds" / "realm" / "world.md"

    async def main():
        with writeset.collecting() as scope:
            await anyio.to_thread.run_sync(atomic.write_text, path, "x")
        return scope.close()

    assert anyio.run(main) == {str(path)}


def test_a_raw_thread_is_not_collected(home):
    path = home / "worlds" / "realm" / "world.md"
    with writeset.collecting() as scope:
        t = threading.Thread(target=atomic.write_text, args=(path, "x"))
        t.start()
        t.join()
    assert scope.close() == frozenset()


# -- the request boundary ---------------------------------------------------


def _app_with_routes(home, recorder=None):
    app = create_app()
    if recorder is not None:
        app.state.cache_warm = recorder
    world = home / "worlds" / "realm"

    def sync_write():
        atomic.write_text(world / "world.md", "def route")
        return {"ok": True}

    async def async_write():
        await anyio.to_thread.run_sync(atomic.write_text, world / "lore.md", "async route")
        return {"ok": True}

    def write_then_raise():
        atomic.write_text(world / "broken.md", "written before the failure")
        raise ValueError("after the write")

    def read_only():
        return {"ok": True}

    app.add_api_route("/test-writeset/sync", sync_write, methods=["PUT"])
    app.add_api_route("/test-writeset/async", async_write, methods=["PUT"])
    app.add_api_route("/test-writeset/raise", write_then_raise, methods=["PUT"])
    app.add_api_route("/test-writeset/read", read_only, methods=["GET"])
    return app, world


def test_a_def_route_and_an_async_route_hand_their_writes_over(home):
    rec = Recorder()
    app, world = _app_with_routes(home, rec)
    client = TestClient(app)
    assert client.put("/test-writeset/sync").status_code == 200
    assert client.put("/test-writeset/async").status_code == 200
    assert rec.wait_for(2) == [{str(world / "world.md")}, {str(world / "lore.md")}]


def test_a_route_that_writes_and_raises_still_hands_its_paths_over(home):
    rec = Recorder()
    app, world = _app_with_routes(home, rec)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.put("/test-writeset/raise").status_code == 500
    assert rec.wait_for(1) == [{str(world / "broken.md")}]


def test_a_request_that_writes_nothing_offers_nothing(home):
    rec = Recorder()
    app, _ = _app_with_routes(home, rec)
    TestClient(app).get("/test-writeset/read")
    time.sleep(0.05)
    assert rec.offers == []


def test_no_queue_drops_the_snapshot_and_raises_nothing(home):
    app, world = _app_with_routes(home)
    assert getattr(app.state, "cache_warm", None) is None
    assert TestClient(app).put("/test-writeset/sync").status_code == 200
    assert (world / "world.md").read_text() == "def route"


def test_a_failing_queue_never_fails_the_request(home):
    class Broken:
        def offer(self, snapshot):
            raise RuntimeError("queue broke")

    app, _ = _app_with_routes(home, Broken())
    assert TestClient(app).put("/test-writeset/sync").status_code == 200
    cache_warm.hand_off(app, frozenset({"x"}))                       # must not raise


# -- detached runs ----------------------------------------------------------


def _wait_terminal(app, run, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run.state != "running":
            return
        time.sleep(0.01)
    raise AssertionError("run never became terminal")


def test_a_run_outlives_its_request_and_keeps_its_own_set(home):
    """A turn keeps writing after the request that started it handed its
    scope off: the request's snapshot is unchanged, and the run's isolated
    scope alone carries what it wrote -- a follow-up it started included,
    which carries its own."""
    rec = Recorder()
    app = create_app()
    world = home / "worlds" / "realm"
    gate = threading.Event()
    runs = {}

    def start_run():
        atomic.write_text(world / "request.md", "request")
        parent, _ = app.state.runs.start_or_existing(SCENE, "turn", "chat", "a1", "i", LABELS)
        child, _ = app.state.runs.start_or_existing(("campaign", "saltmarch"), "background",
                                                    "follow-up", "a2", "", LABELS)
        runs.update(parent=parent, child=child)

        async def follow_up():
            await anyio.to_thread.run_sync(atomic.write_text, world / "follow-up.md", "child")

        async def turn():
            await anyio.to_thread.run_sync(atomic.write_text, world / "early.md", "run")
            await anyio.to_thread.run_sync(gate.wait)
            await anyio.to_thread.run_sync(atomic.write_text, world / "late.md", "run")
            await anyio.to_thread.run_sync(runner.start, app, child, follow_up)

        runner.start(app, parent, turn)
        return {"ok": True}

    app.add_api_route("/test-writeset/run", start_run, methods=["POST"])
    with TestClient(app) as client:
        app.state.cache_warm = rec
        assert client.post("/test-writeset/run").status_code == 200
        request_snapshot = rec.wait_for(1)[0]
        assert request_snapshot == {str(world / "request.md")}
        gate.set()
        _wait_terminal(app, runs["parent"])
        _wait_terminal(app, runs["child"])
        offers = rec.wait_for(3)
    assert request_snapshot == {str(world / "request.md")}
    assert offers[0] is request_snapshot
    assert {str(world / "early.md"), str(world / "late.md")} in offers[1:]
    assert {str(world / "follow-up.md")} in offers[1:]


def test_a_maintenance_thread_run_hands_its_own_writes_over(home):
    rec = Recorder()
    app = create_app()
    world = home / "worlds" / "realm"
    with TestClient(app):
        app.state.cache_warm = rec
        run, _ = app.state.runs.start_or_existing(("global",), "maintenance", "image-gc", "a1", "", {})

        def work(r):
            atomic.write_text(world / "report.md", "report")
            return {}

        runner.start_thread(app, run, work)
        _wait_terminal(app, run)
        assert rec.wait_for(1) == [{str(world / "report.md")}]
