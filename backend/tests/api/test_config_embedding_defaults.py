"""Tests for GET/PATCH /api/config/embedding-defaults."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_embedding_defaults_empty(client: TestClient) -> None:
    resp = client.get("/api/config/embedding-defaults")
    assert resp.status_code == 200
    assert resp.json()["route"] is None


def test_patch_embedding_defaults(client: TestClient) -> None:
    resp = client.patch(
        "/api/config/embedding-defaults",
        json={"route": "embed-openrouter.openai/text-embedding-3-small"},
    )
    assert resp.status_code == 200
    assert resp.json()["route"] == "embed-openrouter.openai/text-embedding-3-small"

    resp2 = client.get("/api/config/embedding-defaults")
    assert resp2.json()["route"] == "embed-openrouter.openai/text-embedding-3-small"


def test_patch_embedding_defaults_clear(client: TestClient) -> None:
    client.patch(
        "/api/config/embedding-defaults",
        json={"route": "embed-openrouter.openai/text-embedding-3-small"},
    )
    resp = client.patch(
        "/api/config/embedding-defaults",
        json={"route": None},
    )
    assert resp.status_code == 200
    assert resp.json()["route"] is None


def test_patch_empty_body_is_noop(client: TestClient) -> None:
    client.patch(
        "/api/config/embedding-defaults",
        json={"route": "embed-openrouter.openai/text-embedding-3-small"},
    )
    resp = client.patch("/api/config/embedding-defaults", json={})
    assert resp.status_code == 200
    assert resp.json()["route"] == "embed-openrouter.openai/text-embedding-3-small"
