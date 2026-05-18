"""REST contract tests for /api/config/app (spec §16).

Covers GET (defaults + persisted values) and PATCH (partial updates,
validation, round-trip via the data-root app.yaml file).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from grimoire.files import load_yaml


def test_app_config_get_returns_defaults(client: TestClient, container) -> None:
    resp = client.get("/api/config/app")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["library_path"], str) and body["library_path"]
    assert body["backup"]["schedule"] == "off"
    assert body["backup"]["retention_days"] == 30


def test_app_config_patch_library_path(client: TestClient, tmp_path: Path) -> None:
    new_path = str(tmp_path / "alt-library")
    resp = client.patch("/api/config/app", json={"library_path": new_path})
    assert resp.status_code == 200
    assert resp.json()["library_path"] == new_path

    # Re-read returns the saved value
    resp = client.get("/api/config/app")
    assert resp.json()["library_path"] == new_path


def test_app_config_patch_backup_merges(client: TestClient) -> None:
    resp = client.patch(
        "/api/config/app",
        json={"backup": {"schedule": "weekly", "retention_days": 7}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["backup"]["schedule"] == "weekly"
    assert body["backup"]["retention_days"] == 7
    # location should fall through to default
    assert body["backup"]["location"] == "data/backups"

    # Patch only `location` — schedule should persist
    resp = client.patch("/api/config/app", json={"backup": {"location": "/srv/backups"}})
    body = resp.json()
    assert body["backup"]["location"] == "/srv/backups"
    assert body["backup"]["schedule"] == "weekly"


def test_app_config_patch_writes_yaml(client: TestClient) -> None:
    from grimoire import config as config_module

    resp = client.patch("/api/config/app", json={"library_path": "/tmp/x-lib"})
    assert resp.status_code == 200, resp.text
    yaml_path = config_module.settings.data_root / "config" / "app.yaml"
    assert yaml_path.exists(), f"missing {yaml_path}"
    parsed = load_yaml(yaml_path)
    assert parsed["library_path"] == "/tmp/x-lib"


def test_app_config_patch_rejects_empty_path(client: TestClient) -> None:
    resp = client.patch("/api/config/app", json={"library_path": "   "})
    assert resp.status_code == 422


def test_app_config_patch_rejects_bad_schedule(client: TestClient) -> None:
    resp = client.patch("/api/config/app", json={"backup": {"schedule": "every-equinox"}})
    assert resp.status_code == 422


def test_app_config_patch_rejects_negative_retention(client: TestClient) -> None:
    resp = client.patch("/api/config/app", json={"backup": {"retention_days": -1}})
    assert resp.status_code == 422
