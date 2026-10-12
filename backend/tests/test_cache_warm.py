"""The write-through queue (spec 05-S4, section 5.4).

The queue is turned back on here (`tests/conftest.py` turns it off for the
suite), its worker parked on a long tick so each test drains by hand on an
injected clock. Placeholder names only.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire import cache_warm
from grimoire.main import create_app
from grimoire.store import atomic, cache_sync, compiled, locks
from tests import cache_sync_fixtures as fx

cache, upper = fx.cache, fx.upper


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def warm(monkeypatch, tmp_path, cache):
    """A lifespan-entered app with the queue on and its worker parked."""
    monkeypatch.delenv("GRIMOIRE_CACHE_WARM", raising=False)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(cache_warm, "TICK_S", 3600.0)
    app = create_app()
    clock = Clock()
    with TestClient(app) as client:
        queue = app.state.cache_warm
        assert isinstance(queue, cache_warm.WriteThroughQueue)
        queue._clock = clock
        yield app, client, queue, clock, tmp_path


def _wait_offered(queue, count=1, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(queue) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"queue holds {len(queue)} paths, expected {count}")


def _spy(monkeypatch):
    calls = []
    real = cache_sync.sync_paths

    def spy(relpaths, **kwargs):
        calls.append((list(relpaths), kwargs["mode"]))
        return real(relpaths, **kwargs)

    monkeypatch.setattr(cache_sync, "sync_paths", spy)
    return calls


def test_the_queue_is_off_under_the_suites_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app):
        assert app.state.cache_warm is None


def test_an_entity_edit_is_warmed_after_the_quiet_period(warm, upper, monkeypatch):
    app, client, queue, clock, home = warm
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    eid = client.post(f"/api/worlds/{wid}/lore", json={"name": "Pact", "body": "first"}).json()["id"]
    (path,) = list((home / "worlds" / wid).rglob(f"{eid}.md"))
    rel = path.relative_to(home).as_posix()
    with compiled.batch(home) as request:
        cache_sync.derive_file(request, upper, path)        # a reader made it hot
    _wait_offered(queue)
    queue.clear()
    assert client.put(f"/api/worlds/{wid}/lore/{eid}", json={"body": "second"}).status_code == 200
    _wait_offered(queue)
    calls = _spy(monkeypatch)
    assert queue.drain(app) is None and calls == []          # not quiet yet
    clock.advance(cache_warm.WRITE_QUIET_S)
    report = queue.drain(app)
    assert calls == [([rel], "write")]
    item = {p.given: p for p in report.paths}[rel]
    assert item.kinds == {"test:upper": "built"} and item.status == "refreshed"
    assert report.tokens_bumped == 0


def test_repeated_writes_inside_the_quiet_period_make_one_warm_up(warm, monkeypatch):
    app, _client, queue, clock, home = warm
    transcript = home / "campaigns" / "saltmarch" / "scenes" / "0001--mara.md"
    transcript.parent.mkdir(parents=True)
    for n in range(3):
        atomic.write_text(transcript, f"turn {n}")
        queue.offer(frozenset({str(transcript)}))
        clock.advance(cache_warm.WRITE_QUIET_S / 2)
    calls = _spy(monkeypatch)
    queue.drain(app)
    assert calls == []                                       # the last write is still fresh
    clock.advance(cache_warm.WRITE_QUIET_S)
    queue.drain(app)
    queue.drain(app)
    assert calls == [(["campaigns/saltmarch/scenes/0001--mara.md"], "write")]


def test_bookkeeping_paths_are_dropped_before_any_lookup(warm, monkeypatch):
    app, _client, queue, clock, home = warm
    queue.offer(frozenset({str(home / "usage" / "2026-10.jsonl"), str(home / "logs" / "2026-10.jsonl"),
                           str(home / "llm_connections" / "openrouter.json"), str(home / "config.md"),
                           str(home / ".cache" / "embeddings" / "x.vec"),
                           str(home / "worlds" / "realm" / ".world.md.abcd1234.tmp"),
                           "worlds/relative.md", "/elsewhere/worlds/realm/world.md"}))
    assert len(queue) == 0
    calls = _spy(monkeypatch)
    clock.advance(cache_warm.WRITE_QUIET_S)
    assert queue.drain(app) is None and calls == []


def test_a_raising_route_hands_its_paths_to_the_real_queue(warm):
    app, _client, _queue, _clock, home = warm
    world = home / "worlds" / "realm"
    world.mkdir(parents=True)

    def write_then_raise():
        atomic.write_text(world / "world.md", "written before the failure")
        raise ValueError("after the write")

    app.add_api_route("/test-warm/raise", write_then_raise, methods=["PUT"])
    app.middleware_stack = app.build_middleware_stack()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.put("/test-warm/raise").status_code == 500
        _wait_offered(app.state.cache_warm)


def test_a_data_dir_move_clears_the_old_roots_paths(warm, tmp_path, monkeypatch):
    _app, client, queue, _clock, home = warm
    queue.offer(frozenset({str(home / "worlds" / "realm" / "world.md")}))
    assert len(queue) == 1
    # The pointer, not the env override, decides the root from here on.
    monkeypatch.delenv("GRIMOIRE_HOME")
    other = tmp_path / "other-library"
    assert client.put("/api/config/data-dir", json={"data_dir": str(other)}).status_code == 200
    assert len(queue) == 0


def test_an_entry_from_an_old_root_is_never_warmed_against_the_new_one(warm, monkeypatch, tmp_path):
    app, _client, queue, clock, home = warm
    queue.offer(frozenset({str(home / "worlds" / "realm" / "world.md")}))
    other = tmp_path / "other-library"
    other.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(other))
    calls = _spy(monkeypatch)
    clock.advance(cache_warm.WRITE_QUIET_S)
    assert queue.drain(app) is None and calls == [] and len(queue) == 0


def test_a_data_dir_move_is_refused_only_while_a_batch_runs(warm, monkeypatch, tmp_path):
    app, client, queue, clock, home = warm
    (home / "worlds" / "realm").mkdir(parents=True)
    path = home / "worlds" / "realm" / "world.md"
    path.write_text("x")
    queue.offer(frozenset({str(path)}))
    # Holding paths refuses nothing.
    other = tmp_path / "other-library"
    assert client.put("/api/config/data-dir", json={"data_dir": str(other)}).status_code == 200
    assert client.put("/api/config/data-dir", json={"data_dir": str(home)}).status_code == 200
    entered, release = threading.Event(), threading.Event()

    def slow(relpaths, **kwargs):
        entered.set()
        release.wait(5)
        return cache_sync.SyncReport(root=str(home), root_source="env", mode="write", dry_run=False)

    monkeypatch.setattr(cache_sync, "sync_paths", slow)
    queue.offer(frozenset({str(path)}))
    clock.advance(cache_warm.WRITE_QUIET_S)
    worker = threading.Thread(target=queue.drain, args=(app,))
    worker.start()
    assert entered.wait(5)
    refused = client.put("/api/config/data-dir", json={"data_dir": str(other)})
    release.set()
    worker.join(5)
    assert refused.status_code == 409 and refused.json()["kind"] == "busy"
    assert client.put("/api/config/data-dir", json={"data_dir": str(other)}).status_code == 200


def test_past_max_pending_paths_are_dropped_with_one_log_line(warm, monkeypatch, caplog):
    queue, home = warm[2], warm[4]
    monkeypatch.setattr(cache_warm, "MAX_PENDING", 2)
    with caplog.at_level(logging.WARNING, logger="grimoire.cache_warm"):
        queue.offer(frozenset(str(home / "worlds" / "realm" / f"{n}.md") for n in range(5)))
        queue.offer(frozenset(str(home / "worlds" / "realm" / f"{n}.md") for n in range(5, 9)))
    assert len(queue) == 2
    assert len([r for r in caplog.records if "queue full" in r.getMessage()]) == 1


def test_a_failed_warm_up_is_swallowed_and_logged_once(warm, monkeypatch, caplog):
    app, _client, queue, clock, home = warm
    monkeypatch.setattr(cache_warm, "_WARNED", set())     # once per process

    def broken(relpaths, **kwargs):
        raise RuntimeError("the warm-up broke")

    monkeypatch.setattr(cache_sync, "sync_paths", broken)
    with caplog.at_level(logging.WARNING, logger="grimoire.cache_warm"):
        for _ in range(2):
            queue.offer(frozenset({str(home / "worlds" / "realm" / "world.md")}))
            clock.advance(cache_warm.WRITE_QUIET_S)
            assert queue.drain(app) is None
    assert len([r for r in caplog.records if "RuntimeError" in r.getMessage()]) == 1


def test_a_batch_that_cannot_take_the_lane_gives_its_paths_back(warm, upper):
    app, _client, queue, clock, home = warm
    path = home / "worlds" / "realm" / "world.md"
    path.parent.mkdir(parents=True)
    path.write_text("x")
    with compiled.batch(home) as request:
        cache_sync.derive_file(request, upper, path)
    queue.offer(frozenset({str(path)}))
    clock.advance(cache_warm.WRITE_QUIET_S)
    with cache_sync._lane:
        report = queue.drain(app)
    assert report.stopped == "lane_busy" and len(queue) == 1
    assert queue.drain(app).stopped is None and len(queue) == 0


def test_the_network_phase_never_runs_on_a_request_thread_the_loop_or_under_a_lock(warm, monkeypatch):
    """A stand-in for 08's `reembed`: the queue's worker is the only vehicle."""
    app, _client, queue, clock, home = warm
    path = home / "campaigns" / "saltmarch" / "plot.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    with compiled.batch(home) as request:
        request.mark_materialized(path, "probe:docs")
    seen = []

    class Probe:
        name, on_write = "probe", "quiet"

        def owns(self, kind):
            return kind == "probe:docs"

        def local(self, ctx, hot):
            return cache_sync.LocalResult(plan="docs")

        def network(self, ctx, plan):
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            seen.append((threading.current_thread().name, on_loop, locks.campaign_locks_held()))
            return cache_sync.NetworkResult()

    monkeypatch.setattr(cache_sync, "HOOKS", (*cache_sync.HOOKS, Probe()))
    monkeypatch.setattr(cache_warm, "TICK_S", 0.01)
    clock.advance(10 * cache_warm.WRITE_QUIET_S)
    queue.offer(frozenset({str(path)}))
    clock.advance(cache_warm.WRITE_QUIET_S)
    # The worker is parked on its first long sleep; a fresh one wakes on the
    # short tick, as the lifespan's would.
    app.state.run_portal.start_task_soon(cache_warm._worker, app, queue)
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(seen) == 1
    thread_name, on_loop, locked = seen[0]
    assert not on_loop and not locked
    assert thread_name != threading.main_thread().name


def test_shutdown_stops_a_running_batch_at_its_next_check(monkeypatch, tmp_path, cache):
    monkeypatch.delenv("GRIMOIRE_CACHE_WARM", raising=False)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(cache_warm, "TICK_S", 0.01)
    monkeypatch.setattr(cache_warm, "WRITE_QUIET_S", 0.0)
    path = tmp_path / "campaigns" / "saltmarch" / "plot.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    with compiled.batch(tmp_path) as request:
        request.mark_materialized(path, "slow:docs")
    started, chunks = threading.Event(), []

    class Slow:
        name, on_write = "slow", "quiet"

        def owns(self, kind):
            return kind == "slow:docs"

        def local(self, ctx, hot):
            return cache_sync.LocalResult(plan="chunks")

        def network(self, ctx, plan):
            started.set()
            for n in range(500):                    # a long embedding run, chunk by chunk
                if ctx.halted():
                    break
                chunks.append(n)
                time.sleep(0.01)
            return cache_sync.NetworkResult()

    monkeypatch.setattr(cache_sync, "HOOKS", (Slow(),))
    app = create_app()
    with TestClient(app):
        app.state.cache_warm.offer(frozenset({str(path)}))
        assert started.wait(5)
    sent_at_shutdown = len(chunks)
    time.sleep(0.05)
    assert len(chunks) == sent_at_shutdown < 500


def test_the_queue_collects_under_the_root_of_the_offer(warm):
    _app, _client, queue, _clock, home = warm
    queue.offer(frozenset({str(home / "worlds" / "realm" / "world.md")}))
    assert list(queue._pending) == [(str(home), "worlds/realm/world.md")]
    assert Path(str(home)) == home


def test_a_drain_takes_at_most_one_batch_and_keeps_the_rest(warm, monkeypatch):
    app, _client, queue, clock, home = warm
    monkeypatch.setattr(cache_sync, "MAX_PATHS", 2)
    calls = _spy(monkeypatch)
    queue.offer(frozenset(str(home / "worlds" / "realm" / f"{n}.md") for n in range(5)))
    clock.advance(cache_warm.WRITE_QUIET_S)
    queue.drain(app)
    assert len(calls[0][0]) == 2 and len(queue) == 3


def test_a_move_drops_only_the_old_roots_paths(warm, tmp_path):
    _app, _client, queue, _clock, home = warm
    queue.offer(frozenset({str(home / "worlds" / "realm" / "world.md")}))
    queue._pending[(str(tmp_path / "new-root"), "worlds/realm/world.md")] = 0.0
    queue.clear(keep_root=str(tmp_path / "new-root"))
    assert list(queue._pending) == [(str(tmp_path / "new-root"), "worlds/realm/world.md")]
