"""The image-store maintenance routes (stage 4, Task 7).

What these tests own is the HTTP contract of the three routes -- the 202 and its
run, the refusals made before a run is reserved, the report route -- and that the
work function persists the report the pass returns. What the passes do is
`test_image_migration_run.py` and `test_image_gc.py`; here they are the real
ones, over an empty or synthetic store, or a stand-in that blocks.
"""

from __future__ import annotations

import threading
import time

import pytest

from grimoire.store import image_gc, image_migration, maintenance_reports, paths, proclock

WAIT = 10.0
RUN_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _machine(monkeypatch, tmp_path_factory):
    """This machine's lock directory and device id, kept out of the developer's
    real ones and outside the store."""
    machine = tmp_path_factory.mktemp("machine-a")
    monkeypatch.setattr(proclock, "lock_dir", lambda: machine)
    return machine


def _wait_run(client, run_id, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["state"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never finished")


def _start(client, path, body=None, attempt=None):
    headers = {"X-Grimoire-Attempt": attempt} if attempt else {}
    if body is None:
        return client.post(path, headers=headers)
    return client.post(path, json=body, headers=headers)


MIGRATE = "/api/maintenance/images/migrate"
GC = "/api/maintenance/images/gc"


# ---- the start routes -------------------------------------------------------

def test_missing_body_means_dry_run(client):
    for path, kind in ((MIGRATE, "migrate"), (GC, "gc")):
        res = _start(client, path)
        assert res.status_code == 202, res.text
        started = res.json()["run"]
        assert started["cls"] == "maintenance" and started["kind"] == kind
        run = _wait_run(client, started["id"])
        assert run["state"] == "landed", run
        report = run["result"]
        # A dry run: the migration report says so, the collection's mode does.
        if kind == "migrate":
            assert report["dry_run"] is True
        else:
            assert report["mode"] == "scan"
        assert report["run_id"] == started["id"]
    # An explicit empty object is the same thing.
    res = _start(client, MIGRATE, {})
    assert res.status_code == 202
    assert _wait_run(client, res.json()["run"]["id"])["result"]["dry_run"] is True


def test_the_pass_report_is_persisted_and_served(client):
    started = _start(client, MIGRATE).json()["run"]
    run = _wait_run(client, started["id"])
    got = client.get(f"/api/maintenance/images/reports/{started['id']}")
    assert got.status_code == 200
    assert got.json() == run["result"]
    assert got.json()["kind"] == "image-migration"
    assert maintenance_reports.read(paths.home().resolve(), started["id"]) == run["result"]


def test_a_real_migration_runs_the_real_pass(client, monkeypatch):
    seen = {}

    def fake(root, *, dry_run, cancel=None):
        seen.update(root=root, dry_run=dry_run, cancel=cancel)
        return {"outcome": "done", "dry_run": dry_run}

    monkeypatch.setattr(image_migration, "run", fake)
    res = _start(client, MIGRATE, {"dry_run": False})
    run = _wait_run(client, res.json()["run"]["id"])
    assert run["state"] == "landed"
    assert seen["dry_run"] is False
    assert seen["root"] == paths.home().resolve()
    assert callable(seen["cancel"]) and seen["cancel"]() is False


def test_gc_delete_without_token_is_400(client):
    for body in ({"dry_run": False}, {"dry_run": False, "token": ""},
                 {"dry_run": False, "token": None}):
        res = _start(client, GC, body)
        assert res.status_code == 400, res.text
    # Refused BEFORE a run was reserved: nothing is live, nothing was reported.
    assert client.get("/api/runs").json()["runs"] == []
    assert _start(client, GC, {"dry_run": True}).status_code == 202


def test_gc_delete_carries_the_token_to_collect(client, monkeypatch):
    seen = {}

    def fake(root, token, *, cancel, run_id=None, now=None):
        seen.update(root=root, token=token, run_id=run_id)
        return {"kind": "image-gc", "mode": "collect", "state": "complete",
                "run_id": run_id}

    monkeypatch.setattr(image_gc, "collect", fake)
    res = _start(client, GC, {"dry_run": False, "token": "tok-1"})
    assert res.status_code == 202, res.text
    run = _wait_run(client, res.json()["run"]["id"])
    assert run["state"] == "landed"
    assert seen["token"] == "tok-1"
    assert seen["run_id"] == run["id"]
    assert seen["root"] == paths.home().resolve()


def test_an_unknown_token_is_a_refused_report_not_a_server_error(client):
    res = _start(client, GC, {"dry_run": False, "token": "nope"})
    assert res.status_code == 202
    run = _wait_run(client, res.json()["run"]["id"])
    assert run["state"] == "landed"
    assert run["result"]["state"] == "refused"
    assert run["result"]["deleted"]["objects"] == []


class _Held:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, root, *, dry_run, cancel=None):
        self.entered.set()
        deadline = time.monotonic() + WAIT
        while not self.release.is_set() and time.monotonic() < deadline:
            if cancel and cancel():
                return {"outcome": "cancelled", "dry_run": dry_run}
            time.sleep(0.01)
        return {"outcome": "done", "dry_run": dry_run}


def test_second_start_is_409(client, monkeypatch):
    held = _Held()
    monkeypatch.setattr(image_migration, "run", held)
    first = _start(client, MIGRATE, {"dry_run": False}, attempt="a1")
    assert first.status_code == 202
    assert held.entered.wait(WAIT)
    try:
        for path, body in ((MIGRATE, {}), (GC, {})):
            second = _start(client, path, body, attempt="a2")
            assert second.status_code == 409, second.text
            detail = second.json()
            assert detail["kind"] == "run_in_flight"
            assert detail["run_id"] == first.json()["run"]["id"]
        # The same attempt delivered twice adopts the live run.
        again = _start(client, MIGRATE, {"dry_run": False}, attempt="a1")
        assert again.status_code == 202
        assert again.json()["run"]["id"] == first.json()["run"]["id"]
        # And the live run is findable by listing, which is how a reloaded
        # Settings page finds it.
        live = client.get("/api/runs").json()["runs"]
        assert [r["id"] for r in live if r["state"] == "running"] == [first.json()["run"]["id"]]
        assert live[-1]["cls"] == "maintenance"
        # Cancel is the global run cancel route.
        cancelled = client.post(f"/api/runs/{first.json()['run']['id']}/cancel")
        assert cancelled.status_code == 200
    finally:
        held.release.set()
    run = _wait_run(client, first.json()["run"]["id"])
    assert run["state"] == "cancelled"
    assert maintenance_reports.read(paths.home().resolve(), run["id"])["outcome"] == "cancelled"


def test_a_pass_that_raises_still_leaves_a_report(client, monkeypatch):
    def boom(root, *, dry_run, cancel=None):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(image_migration, "run", boom)
    res = _start(client, MIGRATE, {"dry_run": False})
    run = _wait_run(client, res.json()["run"]["id"])
    assert run["state"] == "failed"
    stored = client.get(f"/api/maintenance/images/reports/{run['id']}").json()
    assert stored["kind"] == "image-migration"
    assert stored["state"] == "failed"
    assert "disk on fire" not in str(stored)       # a class name, never a message


def test_a_hold_on_the_store_refuses_a_start_as_busy(client):
    from grimoire.routes import runs
    with runs.maintenance_excluded(client.app):
        res = _start(client, MIGRATE)
    assert res.status_code == 409
    assert res.json()["kind"] == "busy"


# ---- the report route -------------------------------------------------------

def test_report_route_validates_the_run_id_and_404s_unknown(client):
    base = "/api/maintenance/images/reports"
    assert client.get(f"{base}/{RUN_ID}").status_code == 404
    for bad in ("nope", "../../config", "0123456789ABCDEF0123456789ABCDEF",
                RUN_ID[:-1], RUN_ID + "0"):
        res = client.get(f"{base}/{bad}")
        assert res.status_code in (400, 404), (bad, res.status_code)
        assert res.status_code != 200
    assert client.get(f"{base}/not-a-run-id").status_code == 400
    # A file that is there but is not a report reads as no report.
    root = paths.home().resolve()
    maintenance_reports.reports_dir(root).mkdir(parents=True, exist_ok=True)
    maintenance_reports.report_path(root, RUN_ID).write_text("[1, 2]")
    assert client.get(f"{base}/{RUN_ID}").status_code == 404
    maintenance_reports.write(root, RUN_ID, {"kind": "image-gc", "state": "complete"})
    assert client.get(f"{base}/{RUN_ID}").json() == {"kind": "image-gc",
                                                     "state": "complete"}
