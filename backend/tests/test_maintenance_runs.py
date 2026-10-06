"""The `maintenance` run class: image-store upkeep as one detached run (stage 4, M1-M3).

Migration and collection both walk the whole store and may delete legacy files,
so the class is shaped around three promises:

* **one at a time, everywhere it can be checked** -- one exclusion key for the
  class, and a synced marker that another device's live run refuses;
* **never torn apart by the tree it walks** -- while it is live, a data-dir
  move, a fork, a delete, an export and the scheduled backup are refused, and
  while one of those holds the store it may not start;
* **the thread is never abandoned** -- a cancel or a shutdown asks it to stop
  and then waits, so the report it writes in its own `finally` always lands.

Work functions here are stand-ins for the real migrate and collect passes: what
these tests own is the run, not what the run does.
"""

from __future__ import annotations

import importlib
import json
import os
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import main, routes, runner
from grimoire.main import create_app
from grimoire.routes import runs
from grimoire.store import maintenance_reports, paths, proclock
from tests.llm_fakes import FakeCatalog, FakeOpenRouter

WAIT = 10.0


@pytest.fixture(autouse=True)
def _machine(monkeypatch, tmp_path_factory):
    """This machine's lock directory, so the device id a test mints lands in a
    throwaway directory rather than the developer's real one -- and OUTSIDE the
    store, which is `tmp_path`, as the real one is."""
    machine = tmp_path_factory.mktemp("machine-a")
    monkeypatch.setattr(proclock, "lock_dir", lambda: machine)
    return machine


def _root():
    return paths.home().resolve()


def _wait(predicate, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never held")


def _wait_terminal(run, timeout=WAIT):
    assert run.terminal.wait(timeout), f"run {run.id} never finished"
    return run


class Held:
    """A work function that blocks until released, reporting in its `finally`
    the way the real passes do."""

    def __init__(self, honour_cancel=True, released=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        if released:
            self.release.set()
        self.finished = threading.Event()
        self.honour_cancel = honour_cancel
        self.saw_cancel = False

    def __call__(self, run):
        report = {"kind": run.kind, "cancelled": False}
        try:
            self.entered.set()
            deadline = time.monotonic() + WAIT
            while not self.release.is_set() and time.monotonic() < deadline:
                if self.honour_cancel and run.cancel_requested:
                    self.saw_cancel = True
                    report["cancelled"] = True
                    break
                time.sleep(0.01)
            return report
        finally:
            maintenance_reports.write(run.root, run.id, report)
            self.finished.set()


def _start(client, work, kind="migrate", attempt=None):
    """Start a maintenance run the way the Task 7 routes will, and hand back the
    live record."""
    body = runs.run_maintenance(client.app, kind, attempt, work)
    run = client.app.state.runs.get(body["run"]["id"], runs.GLOBAL_SUBJECT)
    assert run is not None
    return run


def _refused(call, kind, status=409):
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == status, exc.value.detail
    assert exc.value.detail["kind"] == kind, exc.value.detail
    return exc.value.detail


@pytest.fixture
def world_and_campaign(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    spare = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    cid = client.post("/api/campaigns",
                      json={"name": "Winifred", "world": wid}).json()["id"]
    return wid, spare, cid


# ---- the class and its key ------------------------------------------------

def test_maintenance_has_its_own_key_and_refuses_a_second_start(client):
    key = runs.exclusion_key(runs.GLOBAL_SUBJECT, "maintenance")
    assert key == "global\x00image-maintenance"
    # Keyed on the CLASS, not the subject: migration and collection never
    # overlap whoever started them.
    assert runs.exclusion_key(("world", "realm"), "maintenance") == key
    assert key != runs.exclusion_key(runs.GLOBAL_SUBJECT, "turn")

    work = Held()
    first = _start(client, work, attempt="a1")
    assert work.entered.wait(WAIT)

    detail = _refused(lambda: runs.run_maintenance(client.app, "gc", "a2", Held()),
                      "run_in_flight")
    assert detail["run_id"] == first.id
    # The same attempt is a duplicate delivery and adopts, rather than refusing.
    again = runs.run_maintenance(client.app, "migrate", "a1", Held())
    assert again["run"]["id"] == first.id
    assert again["run"]["cls"] == "maintenance"

    work.release.set()
    _wait_terminal(first)
    second = _start(client, Held(released=True), kind="gc", attempt="a3")
    assert second.id != first.id
    assert _wait_terminal(second).state == "landed"


def test_a_store_move_in_progress_refuses_maintenance_as_busy(client):
    with client.app.state.runs.hold_still():
        _refused(lambda: runs.run_maintenance(client.app, "migrate", None, Held()),
                 "busy")
    assert client.app.state.runs.any_live() is None


def test_maintenance_does_not_refuse_a_model_refresh(client):
    conn = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint",
        "base_url": "https://x", "api_key": "sk-x"}).json()["id"]
    client.app.dependency_overrides[routes.get_llm] = \
        lambda: FakeCatalog(models=[{"id": "m-1"}])
    work = Held()
    _start(client, work)
    assert work.entered.wait(WAIT)

    r = client.post(f"/api/llm-connections/{conn}/models/refresh")

    assert r.status_code == 202, r.text
    assert r.json()["run"]["cls"] == "draft"
    work.release.set()


# ---- tree operations, both orders of arrival ----------------------------------

def test_a_data_dir_move_fork_delete_and_export_are_refused_while_maintenance_runs(
        client, world_and_campaign, tmp_path):
    wid, spare, cid = world_and_campaign
    work = Held()
    run = _start(client, work)
    assert work.entered.wait(WAIT)

    dest = tmp_path / "moved"
    r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})
    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "runs_in_flight"
    assert not dest.exists()

    calls = [
        ("post", f"/api/worlds/{wid}/fork", {"name": "Realm Copy"}),
        ("delete", f"/api/worlds/{spare}", None),
        ("delete", f"/api/campaigns/{cid}", None),
        ("post", f"/api/campaigns/{cid}/fork", {"name": "Winifred Copy"}),
        ("get", f"/api/worlds/{wid}/export.zip", None),
        ("post", "/api/backups", None),
        ("post", "/api/backups/images", None),
    ]
    for method, path, body in calls:
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 409, f"{method} {path} answered {r.status_code}"
        assert r.json()["kind"] == "maintenance_running", f"{method} {path}: {r.json()}"
        assert r.json()["run_id"] == run.id

    # Nothing went ahead.
    worlds = {w["id"] for w in client.get("/api/worlds").json()}
    assert {wid, spare} <= worlds
    assert len(worlds) == 2
    assert client.get(f"/api/campaigns/{cid}").status_code == 200
    listing = client.get("/api/backups").json()
    assert listing["backups"] == [] and listing["image_backups"] == []

    work.release.set()
    _wait_terminal(run)
    # And they are allowed again once it is over.
    assert client.delete(f"/api/worlds/{spare}").status_code == 200


def test_the_backup_ticker_skips_its_turn_while_maintenance_runs(client, monkeypatch,
                                                                 caplog):
    calls = []
    monkeypatch.setattr(store.backups, "run_scheduled", lambda: calls.append(1))
    work = Held()
    run = _start(client, work)
    assert work.entered.wait(WAIT)
    # Switched on only now: with backups on, the lifespan's own first tick may
    # still be about to run, and would hold the store against the start above.
    store.config.write_config(backup_enabled="on")

    with caplog.at_level("INFO", logger="grimoire.main"):
        assert main._scheduled_backup(client.app) is None
    assert calls == []
    assert "maintenance" in caplog.text

    work.release.set()
    _wait_terminal(run)
    main._scheduled_backup(client.app)
    assert calls


def _blocking(monkeypatch, target, name):
    """Replace `target.name` with a stand-in that parks until released."""
    entered, release = threading.Event(), threading.Event()

    def parked(*_a, **_kw):
        entered.set()
        assert release.wait(WAIT)
        raise RuntimeError("released")

    monkeypatch.setattr(target, name, parked)
    return entered, release


@pytest.mark.parametrize("op", ["fork", "export"])
def test_maintenance_is_refused_while_a_fork_or_export_holds_the_exclusion(
        client, world_and_campaign, monkeypatch, op):
    wid, _, _ = world_and_campaign
    if op == "fork":
        entered, release = _blocking(monkeypatch, store.worlds, "fork_world")
        request = lambda: client.post(f"/api/worlds/{wid}/fork",
                                      json={"name": "Realm Copy"})
    else:
        entered, release = _blocking(monkeypatch, store.world_bundle, "write_bundle")
        request = lambda: client.get(f"/api/worlds/{wid}/export.zip")
    outcome = []

    def go():
        try:
            outcome.append(request().status_code)
        except RuntimeError:
            outcome.append("raised")

    t = threading.Thread(target=go)
    t.start()
    try:
        assert entered.wait(WAIT)
        _refused(lambda: runs.run_maintenance(client.app, "migrate", None, Held()),
                 "busy")
        assert client.app.state.runs.any_live() is None
    finally:
        release.set()
        t.join(WAIT)
    # The hold is released with the operation, whatever it answered.
    run = _start(client, Held(released=True), attempt="after")
    _wait_terminal(run)


def test_an_explicit_hold_refuses_maintenance_and_a_live_run_refuses_a_hold(client):
    with runs.maintenance_excluded(client.app):
        with runs.maintenance_excluded(client.app):     # holds nest
            pass
        _refused(lambda: runs.run_maintenance(client.app, "migrate", None, Held()),
                 "busy")
    work = Held()
    run = _start(client, work)
    assert work.entered.wait(WAIT)
    with pytest.raises(HTTPException) as exc, runs.maintenance_excluded(client.app):
        pass
    assert exc.value.detail["kind"] == "maintenance_running"
    work.release.set()
    _wait_terminal(run)


# ---- the device and the marker -------------------------------------------------

def test_the_device_id_is_machine_local_and_differs_when_cache_is_copied(
        client, monkeypatch, tmp_path, tmp_path_factory, _machine):
    first = maintenance_reports.device_id()
    assert first == maintenance_reports.device_id()
    # Machine-local: beside the process locks, never under the store, which
    # may be a synced folder.
    assert (_machine / maintenance_reports.DEVICE_ID_FILE).is_file()
    root = _root()
    maintenance_reports.write_marker(root, "a" * 32,
                                     maintenance_reports.device_key(root))
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            where = os.path.join(dirpath, f)
            assert first not in _read(where), f"the device id leaked into {where}"

    key = maintenance_reports.device_key(root)
    assert key != maintenance_reports.device_key(tmp_path / "elsewhere")

    # Another machine that syncs this store's `.cache` in still has its own id.
    maintenance_reports.write(root, "a" * 32, {"ok": True})
    other_machine = tmp_path_factory.mktemp("machine-b")
    monkeypatch.setattr(proclock, "lock_dir", lambda: other_machine)
    assert maintenance_reports.device_id() != first
    assert maintenance_reports.device_key(root) != key


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def test_the_heartbeat_refreshes_during_a_long_step(client, monkeypatch):
    monkeypatch.setattr(maintenance_reports, "HEARTBEAT_SECONDS", 0.05)
    seen = []

    def one_long_step(run):
        try:
            seen.append(maintenance_reports.read_marker(run.root)["heartbeat"])
            time.sleep(0.6)                     # one item, no boundary to beat at
            seen.append(maintenance_reports.read_marker(run.root)["heartbeat"])
            return {}
        finally:
            maintenance_reports.write(run.root, run.id, {})

    run = _start(client, one_long_step)
    _wait_terminal(run)

    assert run.state == "landed", run.error
    assert len(seen) == 2 and seen[1] > seen[0]
    marker = maintenance_reports.marker_path(run.root)
    # Ours, and released when the run ended.
    assert not marker.exists()


def test_another_devices_live_marker_refuses_a_start(client):
    root = _root()
    maintenance_reports.write_marker(root, "b" * 32, "other-device", now=time.time() - 60)

    detail = _refused(lambda: runs.run_maintenance(client.app, "migrate", None, Held()),
                      "maintenance_elsewhere")
    assert "detail" in detail
    # Released, not stranded: nothing is live and the next start is not
    # refused with `run_in_flight` by the run that never began.
    assert client.app.state.runs.any_live() is None

    # A marker whose heartbeat stopped five minutes ago belongs to a run that
    # died with its device, and refuses nothing.
    maintenance_reports.write_marker(
        root, "b" * 32, "other-device",
        now=time.time() - maintenance_reports.MARKER_STALE_SECONDS - 1)
    run = _start(client, Held(released=True), attempt="later")
    _wait_terminal(run)
    assert run.state == "landed", run.error


def test_another_process_on_this_machine_refuses_a_start(client):
    """A second backend over the same store has its own registry and the same
    device key, so neither the exclusion key nor the marker can see it. The
    process lock can."""
    root = _root()
    fd = proclock.acquire(proclock.lock_path(root, "image-maintenance", "run"),
                          proclock.NO_WAIT)
    assert fd is not None
    try:
        detail = _refused(
            lambda: runs.run_maintenance(client.app, "migrate", None, Held()),
            "maintenance_elsewhere")
        assert "another grimoire process" in detail["detail"]
        assert client.app.state.runs.any_live() is None
        # Refused before the marker was claimed: nothing is left behind.
        assert maintenance_reports.read_marker(root) is None
    finally:
        proclock.release(fd)

    run = _start(client, Held(released=True), attempt="free")
    _wait_terminal(run)
    assert run.state == "landed", run.error
    # And the run let it go when it ended.
    again = proclock.acquire(proclock.lock_path(root, "image-maintenance", "run"),
                             proclock.NO_WAIT)
    assert again is not None
    proclock.release(again)


def test_a_pass_that_raised_before_reporting_still_leaves_a_minimal_report(client):
    def broken(run):
        raise OSError("/somewhere/private/Mara.png vanished")

    run = _start(client, broken)
    _wait_terminal(run)

    assert run.state == "failed"
    report = maintenance_reports.read(run.root, run.id)
    # The exception's CLASS only: its message can carry a path or a name.
    assert report == {"kind": "migrate", "state": "failed", "error": "OSError"}


def test_this_devices_own_marker_does_not_refuse(client):
    root = _root()
    maintenance_reports.write_marker(root, "c" * 32,
                                     maintenance_reports.device_key(root),
                                     now=time.time())
    run = _start(client, Held(released=True))
    _wait_terminal(run)
    assert run.state == "landed", run.error


# ---- cancel, shutdown and the thread -------------------------------------------

def test_cancel_waits_for_the_thread_and_still_reports(client):
    work = Held()
    run = _start(client, work)
    assert work.entered.wait(WAIT)

    r = client.post(f"/api/runs/{run.id}/cancel")

    assert r.status_code == 200, r.text
    body = r.json()["run"]
    assert work.finished.is_set(), "the cancel answered before the thread returned"
    assert work.saw_cancel
    assert body["state"] == "cancelled"
    assert body["result"] == {"kind": "migrate", "cancelled": True}
    assert maintenance_reports.read(run.root, run.id) == body["result"]


def test_a_failed_run_still_leaves_its_report(client):
    def boom(run):
        try:
            raise OSError("disk went away")
        finally:
            maintenance_reports.write(run.root, run.id, {"failed": True})

    run = _start(client, boom)
    _wait_terminal(run)
    assert run.state == "failed"
    assert run.error["kind"] == "run_failed"
    assert maintenance_reports.read(run.root, run.id) == {"failed": True}


def test_shutdown_sets_cancel_and_waits(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    work = Held()
    with TestClient(app) as c:
        run = _start(c, work)
        assert work.entered.wait(WAIT)
    # The lifespan has exited: it asked the run to stop and waited for the
    # thread rather than abandoning it mid-item.
    assert work.saw_cancel
    assert work.finished.is_set()
    assert run.state == "cancelled"
    assert run.terminal.is_set()
    assert maintenance_reports.read(run.root, run.id) == {"kind": "migrate",
                                                          "cancelled": True}


def test_the_exclusion_holds_until_the_thread_returns(client, world_and_campaign,
                                                      monkeypatch, tmp_path):
    wid, _, _ = world_and_campaign
    monkeypatch.setattr(runs, "CANCEL_TIMEOUT_SECONDS", 0.2)
    work = Held(honour_cancel=False)        # a step that does not look up
    run = _start(client, work)
    assert work.entered.wait(WAIT)

    r = client.post(f"/api/runs/{run.id}/cancel")

    assert r.status_code == 200
    assert run.cancel_requested
    # Asked to stop, but the thread is still inside its step: the run is live,
    # and everything it excludes stays excluded.
    assert run.state == "running"
    _refused(lambda: runs.run_maintenance(client.app, "gc", "x", Held()), "run_in_flight")
    assert client.put("/api/config/data-dir",
                      json={"data_dir": str(tmp_path / "moved")}).json()["kind"] \
        == "runs_in_flight"
    assert client.post(f"/api/worlds/{wid}/fork",
                       json={"name": "Realm Copy"}).json()["kind"] == "maintenance_running"

    work.release.set()
    _wait_terminal(run)
    assert run.state == "cancelled"
    assert maintenance_reports.read(run.root, run.id) is not None
    after = _start(client, Held(released=True), attempt="y")
    _wait_terminal(after)


# ---- reports ---------------------------------------------------------------------

def test_the_report_survives_reap_and_old_reports_are_pruned(client):
    def quick(run):
        report = {"migrated": 0}
        try:
            return report
        finally:
            maintenance_reports.write(run.root, run.id, report)

    run = _start(client, quick)
    _wait_terminal(run)
    assert run.state == "landed"
    assert run.result == {"migrated": 0}
    assert client.get(f"/api/runs/{run.id}").json()["run"]["result"] == {"migrated": 0}

    client.app.state.runs.reap(now=time.monotonic() + runs.REAP_SECONDS + 1)
    assert client.get(f"/api/runs/{run.id}").status_code == 404
    root = run.root
    assert maintenance_reports.read(root, run.id) == {"migrated": 0}

    old, recent = "d" * 32, "e" * 32
    maintenance_reports.write(root, old, {"old": True})
    maintenance_reports.write(root, recent, {"recent": True})
    stale = time.time() - maintenance_reports.REPORT_MAX_AGE_SECONDS - 60
    os.utime(maintenance_reports.report_path(root, old), (stale, stale))
    # Something that is not a report is never touched by the prune.
    stray = maintenance_reports.reports_dir(root) / "notes.json"
    stray.write_text("{}", encoding="utf-8")
    os.utime(stray, (stale, stale))

    maintenance_reports.write(root, "f" * 32, {"next": True})

    assert maintenance_reports.read(root, old) is None
    assert maintenance_reports.read(root, recent) == {"recent": True}
    assert maintenance_reports.read(root, run.id) == {"migrated": 0}
    assert stray.exists()


@pytest.mark.parametrize("bad", ["", "../escape", "A" * 32, "a" * 31, "a" * 33,
                                 "g" * 32, "a" * 32 + "\n"])
def test_a_report_id_must_be_a_run_id(client, bad):
    root = _root()
    with pytest.raises(ValueError):
        maintenance_reports.write(root, bad, {})
    with pytest.raises(ValueError):
        maintenance_reports.read(root, bad)


def test_a_report_is_written_atomically_as_json(client):
    root = _root()
    maintenance_reports.write(root, "a" * 32, {"b": [1, 2]})
    path = maintenance_reports.report_path(root, "a" * 32)
    assert path.parent == root / ".cache" / "image-store" / "reports"
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": [1, 2]}


# ---- what the class is not -------------------------------------------------------

def test_maintenance_is_not_notifying(client):
    seen = []
    client.app.state.on_run_terminal = lambda *args: seen.append(args)
    assert "maintenance" not in runner.NOTIFYING_CLASSES
    assert "maintenance" not in runs.ATTACHABLE

    run = _start(client, Held(released=True))
    _wait_terminal(run)

    assert run.state == "landed"
    assert seen == []
    # Retired all the same: the live count is what keeps the Android service
    # promoted, and a finished run must stop holding it.
    assert client.app.state.runs.any_live() is None
