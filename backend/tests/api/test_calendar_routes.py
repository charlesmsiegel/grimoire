"""Smoke tests for the calendar REST surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.library import LibraryService
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.world.calendar_service import CalendarService


@pytest.fixture()
async def wired_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GRIMOIRE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GRIMOIRE_DATABASE_PATH", str(tmp_path / "cal.sqlite"))
    from grimoire import config as config_module

    config_module.settings = config_module.Settings()

    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "cal.sqlite"), pool_size=2)
    await db.connect()
    store = StateStore(db, data_root)
    library = LibraryService(store)
    calendar = CalendarService(library)

    container = ServiceContainer()
    container.library = library
    container.calendar = calendar
    container.state_store = store
    try:
        yield container
    finally:
        await db.close()


@pytest.fixture()
def client(wired_container: ServiceContainer):
    from grimoire.main import create_app

    app = create_app()
    app.state.container = wired_container
    with TestClient(app) as test_client:
        yield test_client


def test_list_calendars_returns_builtins(client: TestClient) -> None:
    resp = client.get("/api/library/calendars")
    assert resp.status_code == 200
    data = resp.json()
    ids = {c["id"] for c in data}
    assert "gregorian" in ids
    assert "hebrew" in ids
    assert "stardate" in ids


def test_get_builtin_gregorian(client: TestClient) -> None:
    resp = client.get("/api/library/calendars/gregorian")
    assert resp.status_code == 200
    data = resp.json()
    assert data["system"] == "gregorian"
    assert data["builtin"] is True


def test_create_custom_calendar(client: TestClient) -> None:
    resp = client.post(
        "/api/library/calendars",
        json={
            "id": "test-cal",
            "name": "Test",
            "system": "custom",
            "custom": {
                "months": [{"name": "A", "days": 30}],
                "days_per_week": 7,
                "epoch_jdn": 2400000,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == "test-cal"

    # Verify it appears in the list
    listed = client.get("/api/library/calendars").json()
    assert "test-cal" in {c["id"] for c in listed}


def test_cannot_overwrite_builtin(client: TestClient) -> None:
    resp = client.post(
        "/api/library/calendars",
        json={"id": "gregorian", "name": "Override", "system": "custom"},
    )
    assert resp.status_code == 409


def test_convert_date(client: TestClient) -> None:
    resp = client.post(
        "/api/library/calendars/convert",
        json={
            "from_calendar_id": "gregorian",
            "to_calendar_ids": ["hebrew", "islamic", "buddhist"],
            "year": 2025,
            "month": 5,
            "day": 22,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["buddhist"]["year"] == 2568
    assert data["hebrew"]["year"] == 5785


def test_holidays_in_year(client: TestClient) -> None:
    resp = client.get(
        "/api/library/calendars/gregorian/holidays",
        params={"year": 2024, "sets": "us-federal"},
    )
    assert resp.status_code == 200
    names = {h["name"] for h in resp.json()}
    assert "Thanksgiving" in names


def test_list_holiday_sets(client: TestClient) -> None:
    resp = client.get("/api/library/holiday-sets")
    assert resp.status_code == 200
    ids = {s["id"] for s in resp.json()}
    assert "us-federal" in ids
    assert "wheel-of-the-year" in ids
