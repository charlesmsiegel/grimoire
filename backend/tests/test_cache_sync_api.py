"""`POST /api/cache/sync` (spec 05-S7, section 7.8)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.store import cache_sync, compiled, revision
from tests import cache_sync_fixtures as fx

cache, upper = fx.cache, fx.upper


@pytest.fixture
def api(monkeypatch, tmp_path, cache):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    (tmp_path / "worlds" / "realm" / "lore").mkdir(parents=True)
    (tmp_path / "campaigns" / "saltmarch").mkdir(parents=True)
    app = create_app()
    with TestClient(app) as client:
        yield app, client, tmp_path


def _wait(client, run_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["state"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError("the sync never finished")


def _frames(client, run_id):
    body = client.get(f"/api/runs/{run_id}/stream").text
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def gated(monkeypatch):
    """A batch that waits on a gate inside its first phase, so a test can act
    while it is live."""
    entered, release = threading.Event(), threading.Event()
    real = cache_sync.sync_paths

    def slow(relpaths, **kwargs):
        entered.set()
        release.wait(10)
        return real(relpaths, **kwargs)

    monkeypatch.setattr(cache_sync, "sync_paths", slow)
    yield entered, release
    release.set()


def test_a_sync_answers_202_and_its_report_is_the_final_frame(api, upper):
    _app, client, home = api
    path = home / "worlds" / "realm" / "lore" / "pact.md"
    path.write_text("first")
    with compiled.batch(home) as request:
        cache_sync.derive_file(request, upper, path)
    path.write_text("second")
    got = client.post("/api/cache/sync", json={"paths": ["worlds/realm/lore/pact.md"]})
    assert got.status_code == 202
    run = _wait(client, got.json()["run"]["id"])
    assert run["state"] == "landed" and run["kind"] == "cache-sync"
    frames = _frames(client, run["id"])
    assert "progress" in frames[0] and "report" in frames[-1]
    report = frames[-1]["report"]
    assert report["paths"] == [{"path": "worlds/realm/lore/pact.md", "status": "refreshed",
                                "kinds": {"test:upper": "built"}}]
    assert all(set(f["progress"]) >= {"phase", "counts"} for f in frames[:-1])


def test_nothing_to_sync_is_400(api):
    _app, client, _home = api
    assert client.post("/api/cache/sync", json={}).status_code == 400
    bad = client.post("/api/cache/sync", json={"renamed": [{"old": "worlds/a.md"}]})
    assert bad.status_code == 400 and bad.json()["kind"] == "bad_rename"


def test_a_second_request_is_refused_whatever_its_options_and_spends_nothing(api, gated, monkeypatch):
    _app, client, _home = api
    entered, release = gated
    first = client.post("/api/cache/sync", json={"paths": ["worlds/realm"], "attempt_id": "a1"})
    assert entered.wait(5)
    bumps = []
    monkeypatch.setattr(revision, "bump", bumps.append)
    for body in ({"paths": ["campaigns/saltmarch"], "dry_run": True},
                 {"campaigns": ["saltmarch"], "embed": False},
                 {"all_records": True, "attempt_id": "a2"}):
        refused = client.post("/api/cache/sync", json=body)
        assert refused.status_code == 409
        assert refused.json()["kind"] == "sync_in_flight"
        assert refused.json()["run_id"] == first.json()["run"]["id"]
    assert bumps == []
    again = client.post("/api/cache/sync", json={"paths": ["worlds/realm"], "attempt_id": "a1"})
    assert again.status_code == 202 and again.json()["run"]["id"] == first.json()["run"]["id"]
    release.set()
    _wait(client, first.json()["run"]["id"])


def test_cancel_stops_the_batch_at_its_next_check(api, gated, upper):
    app, client, home = api
    entered, release = gated
    for n in range(3):
        path = home / "campaigns" / "saltmarch" / f"{n}.md"
        path.write_text("x")
        with compiled.batch(home) as request:
            cache_sync.derive_file(request, upper, path)
        path.write_text("edited")
    got = client.post("/api/cache/sync", json={"campaigns": ["saltmarch"]})
    run_id = got.json()["run"]["id"]
    assert entered.wait(5)
    canceller = threading.Thread(target=client.post, args=(f"/api/runs/{run_id}/cancel",))
    canceller.start()
    live = next(r for r in app.state.runs._runs.values() if r.id == run_id)
    deadline = time.monotonic() + 5
    while not live.cancel_requested and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    canceller.join(10)
    run = _wait(client, run_id)
    assert run["state"] == "cancelled"
    report = _frames(client, run_id)[-1]["report"]
    assert report["stopped"] == "cancelled" and report["tokens_bumped"] == 1
    assert report["counts"]["unsynced"] == 3


def test_a_data_dir_move_is_refused_while_a_sync_is_live(api, gated, tmp_path):
    _app, client, _home = api
    entered, release = gated
    got = client.post("/api/cache/sync", json={"paths": ["worlds/realm"]})
    assert entered.wait(5)
    moved = client.put("/api/config/data-dir", json={"data_dir": str(tmp_path / "elsewhere")})
    release.set()
    _wait(client, got.json()["run"]["id"])
    assert moved.status_code == 409
