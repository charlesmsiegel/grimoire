from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire import config as _config
from grimoire.api.setup import router as setup_router


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Point data_root at an isolated dir so the sentinel never lands in
    # the developer's real ~/.grimoire.
    from fastapi import FastAPI

    monkeypatch.setattr(_config.settings, "data_root", tmp_path, raising=False)
    app = FastAPI()
    app.include_router(setup_router, prefix="/api")
    return TestClient(app)


def test_status_initially_incomplete(client: TestClient, tmp_path: Path) -> None:
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    body = response.json()
    assert body["completed"] is False
    assert body["data_root"] == str(tmp_path)


def test_mark_complete_creates_sentinel(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/setup/status", json={"completed": True})
    assert response.status_code == 200
    assert response.json()["completed"] is True
    assert (tmp_path / ".setup-complete").is_file()

    again = client.get("/api/setup/status")
    assert again.json()["completed"] is True


def test_mark_incomplete_removes_sentinel(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / ".setup-complete").touch()
    response = client.post("/api/setup/status", json={"completed": False})
    assert response.status_code == 200
    assert response.json()["completed"] is False
    assert not (tmp_path / ".setup-complete").exists()
