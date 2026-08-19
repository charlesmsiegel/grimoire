"""The backup surface: /api/backups, the config keys, and the schedule tick (#32)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from fastapi.testclient import TestClient
from grimoire import main
from grimoire.main import create_app
from grimoire.store import backups, config


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with TestClient(create_app()) as c:
        yield c


#: Deliberately in the past: the route stamps its archive with the real clock,
#: and these have to sort under it whenever the suite happens to run.
AT = datetime(2019, 5, 17, 21, 0, 0, tzinfo=UTC)


# ---- the routes ------------------------------------------------------------

def test_a_store_with_no_archives_lists_none_and_says_where_they_would_go(
        client, tmp_path):
    body = client.get("/api/backups").json()

    assert body["backups"] == []
    assert body["dir"] == str(tmp_path / "backups")


def test_backing_up_now_returns_the_refreshed_listing(client):
    body = client.post("/api/backups").json()

    assert body["created"].startswith("grimoire-")
    assert body["swept"] == []
    assert [b["name"] for b in body["backups"]] == [body["created"]]
    assert client.get("/api/backups").json()["backups"] == body["backups"]


def test_backing_up_now_applies_retention(client, tmp_path):
    client.put("/api/config", json={"backup_keep": "2"})
    for day in range(3):
        backups.create_backup(when=AT + timedelta(days=day))

    body = client.post("/api/backups").json()

    assert body["swept"] == ["grimoire-20190517T210000Z.zip",
                             "grimoire-20190518T210000Z.zip"]
    assert len(body["backups"]) == 2
    assert body["backups"][0]["name"] == body["created"]


def test_a_backup_that_cannot_be_written_is_a_reported_failure(client, monkeypatch):
    def boom(*_a, **_kw):
        raise PermissionError("read-only volume")

    monkeypatch.setattr(backups, "create_backup", boom)
    r = client.post("/api/backups")

    assert r.status_code == 500
    assert "could not write a backup" in r.json()["detail"]


def test_a_failed_sweep_is_not_reported_as_a_failed_backup(client, monkeypatch):
    """Under one try/except this answered "could not write a backup" for an
    archive that was written and is sitting right there — the opposite of what
    happened, about the half of the operation the user cares about."""
    def boom():
        raise PermissionError("read-only volume")

    monkeypatch.setattr(backups, "sweep", boom)
    r = client.post("/api/backups")
    body = r.json()

    assert r.status_code == 200
    assert body["created"].startswith("grimoire-")
    assert "old archives could not be removed" in body["retention_error"]
    # ...and the listing still comes back, so the caller can show the archive.
    assert [b["name"] for b in body["backups"]] == [body["created"]]


def test_a_clean_run_reports_no_retention_problem(client):
    assert client.post("/api/backups").json()["retention_error"] is None


def test_a_listing_that_cannot_be_read_is_not_reported_as_empty(client, monkeypatch):
    """"No restore points" and "could not look" send a reader in opposite
    directions, and this is read exactly when someone is deciding whether they
    are covered."""
    def boom():
        raise PermissionError("gone")

    monkeypatch.setattr(backups, "list_backups", boom)
    r = client.get("/api/backups")

    assert r.status_code == 500


def test_a_second_process_holding_the_backup_lock_is_a_409(client, monkeypatch):
    from grimoire.store import locks

    def busy(*_a, **_kw):
        raise locks.BackupBusy()

    monkeypatch.setattr(backups, "create_backup", busy)
    assert client.post("/api/backups").status_code == 409


# ---- the settings ----------------------------------------------------------

def test_the_config_route_reports_the_backup_settings(client):
    cfg = client.get("/api/config").json()

    assert cfg["backup_enabled"] == "off"
    assert cfg["backup_interval_hours"] == "24"
    assert cfg["backup_keep"] == "7"
    assert cfg["backup_dir"] == ""


def test_the_backup_settings_save_and_come_back(client, tmp_path):
    elsewhere = str(tmp_path / "elsewhere")
    saved = client.put("/api/config", json={
        "backup_enabled": "on", "backup_interval_hours": "6",
        "backup_keep": "3", "backup_dir": elsewhere}).json()

    assert saved["backup_enabled"] == "on"
    assert saved["backup_interval_hours"] == "6"
    assert saved["backup_keep"] == "3"
    assert saved["backup_dir"] == elsewhere
    assert client.get("/api/backups").json()["dir"] == elsewhere


def test_a_configured_backup_dir_is_where_the_archive_lands(client, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    client.put("/api/config", json={"backup_dir": str(elsewhere)})

    body = client.post("/api/backups").json()

    assert (elsewhere / body["created"]).exists()
    assert not (tmp_path / "backups").exists()


# ---- the schedule ----------------------------------------------------------

def _tick_until(predicate, timeout=20.0):
    """Run the ticker until `predicate` holds, then cancel it."""
    async def drive():
        async with anyio.create_task_group() as tg:
            tg.start_soon(main._backup_ticker)
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                await anyio.sleep(0.01)
            tg.cancel_scope.cancel()

    anyio.run(drive)


def test_the_ticker_backs_up_on_its_first_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    config.write_config(backup_enabled="on")

    _tick_until(lambda: bool(backups.list_backups()))

    assert len(backups.list_backups()) == 1


def test_the_ticker_leaves_a_store_with_backups_off_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    calls: list = []
    real = backups.run_scheduled
    monkeypatch.setattr(backups, "run_scheduled",
                        lambda *a, **kw: (calls.append(1), real(*a, **kw))[1])

    _tick_until(lambda: bool(calls))

    assert backups.list_backups() == []


def test_a_failed_pass_does_not_end_the_schedule(monkeypatch, tmp_path):
    """A full disk tonight must not mean no backups after it is cleared."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(main, "BACKUP_TICK_SECONDS", 0.01)
    passes = []

    def flaky():
        passes.append(len(passes))
        if len(passes) == 1:
            raise OSError("no space left on device")
        return

    monkeypatch.setattr(backups, "run_scheduled", flaky)
    _tick_until(lambda: len(passes) >= 3)

    assert len(passes) >= 3


def test_the_app_starts_and_stops_with_the_ticker_running(monkeypatch, tmp_path):
    """The task group wraps the yield, so a lifespan that never finishes would
    hang every test that uses TestClient — including this one."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    done = threading.Event()

    def boot():
        with TestClient(create_app()) as c:
            assert c.get("/api/config").status_code == 200
        done.set()

    worker = threading.Thread(target=boot, daemon=True)
    worker.start()
    worker.join(30)

    assert done.is_set(), "the app did not shut down with the ticker running"
