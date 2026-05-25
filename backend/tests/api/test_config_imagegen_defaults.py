"""Tests for GET/PATCH /api/config/imagegen-defaults."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_imagegen_defaults_empty(client: TestClient) -> None:
    resp = client.get("/api/config/imagegen-defaults")
    assert resp.status_code == 200
    assert resp.json()["backend"] is None


def test_patch_imagegen_defaults(client: TestClient) -> None:
    resp = client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": "imagegen-a1111"},
    )
    assert resp.status_code == 200
    assert resp.json()["backend"] == "imagegen-a1111"

    resp2 = client.get("/api/config/imagegen-defaults")
    assert resp2.json()["backend"] == "imagegen-a1111"


def test_patch_imagegen_defaults_clear(client: TestClient) -> None:
    client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": "imagegen-a1111"},
    )
    resp = client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": None},
    )
    assert resp.status_code == 200
    assert resp.json()["backend"] is None


def test_patch_empty_body_is_noop(client: TestClient) -> None:
    client.patch(
        "/api/config/imagegen-defaults",
        json={"backend": "imagegen-a1111"},
    )
    resp = client.patch("/api/config/imagegen-defaults", json={})
    assert resp.status_code == 200
    assert resp.json()["backend"] == "imagegen-a1111"
