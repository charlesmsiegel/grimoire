"""§13 ImageGen REST surface."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.api.stream import _FORWARDED_EVENTS
from grimoire.event_bus import EventBus
from grimoire.imagegen import (
    BackendRegistry,
    ImageGenService,
    InMemoryDiffusersBackend,
)
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def imagegen_service(tmp_path) -> AsyncIterator[ImageGenService]:
    data = tmp_path / "data"
    data.mkdir()
    db = Database(tmp_path / "x.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="t")
    reg = BackendRegistry()
    reg.register(InMemoryDiffusersBackend())
    svc = ImageGenService(
        store=s,
        registry=reg,
        default_backend_id="diffusers-memory",
        event_bus=EventBus(),
    )
    try:
        yield svc
    finally:
        await svc.aclose()
        await db.close()


@pytest.fixture
def app_client(client: TestClient, container: ServiceContainer, imagegen_service) -> TestClient:
    container.imagegen = imagegen_service
    return client


def test_imagegen_forwarded_events_include_new_types() -> None:
    assert "imagegen_backend_health_changed" in _FORWARDED_EVENTS
    assert "imagegen_download_progress" in _FORWARDED_EVENTS
    assert "imagegen_warning" in _FORWARDED_EVENTS


def test_list_backends_returns_registered(app_client) -> None:
    resp = app_client.get("/api/imagegen/backends")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()}
    assert "diffusers-memory" in ids


def test_backend_health_returns_status(app_client) -> None:
    resp = app_client.get("/api/imagegen/backends/diffusers-memory/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_id"] == "diffusers-memory"
    assert body["level"] in ("healthy", "degraded", "unhealthy", "unconfigured")


def test_unknown_backend_health_returns_unconfigured(app_client) -> None:
    resp = app_client.get("/api/imagegen/backends/nope/health")
    assert resp.status_code == 200
    assert resp.json()["level"] == "unconfigured"


def test_set_active_backend_round_trips(app_client) -> None:
    resp = app_client.put(
        "/api/campaigns/camp-1/imagegen/active",
        json={"backend_id": "diffusers-memory"},
    )
    assert resp.status_code == 200
    resp = app_client.get("/api/campaigns/camp-1/imagegen/active")
    assert resp.json()["id"] == "diffusers-memory"


def test_set_active_backend_unknown_returns_404(app_client) -> None:
    resp = app_client.put(
        "/api/campaigns/camp-1/imagegen/active",
        json={"backend_id": "nope"},
    )
    assert resp.status_code == 404


def test_trigger_config_round_trip(app_client) -> None:
    resp = app_client.put(
        "/api/campaigns/camp-1/imagegen/trigger",
        json={
            "mode": "every_n_posts",
            "every_n": 3,
            "on_scene_open": False,
            "on_new_location": False,
            "on_new_character_appearance": False,
            "auto_during_combat": True,
        },
    )
    assert resp.status_code == 200
    resp = app_client.get("/api/campaigns/camp-1/imagegen/trigger")
    body = resp.json()
    assert body["mode"] == "every_n_posts"
    assert body["every_n"] == 3
    assert body["auto_during_combat"] is True


def test_fallback_backend_round_trip(app_client) -> None:
    resp = app_client.put(
        "/api/campaigns/camp-1/imagegen/fallback",
        json={"backend_id": "diffusers-memory"},
    )
    assert resp.status_code == 200
    resp = app_client.get("/api/campaigns/camp-1/imagegen/fallback")
    assert resp.json()["backend_id"] == "diffusers-memory"


def test_fallback_backend_clear_with_null(app_client) -> None:
    app_client.put(
        "/api/campaigns/camp-1/imagegen/fallback", json={"backend_id": "diffusers-memory"}
    )
    resp = app_client.put("/api/campaigns/camp-1/imagegen/fallback", json={"backend_id": None})
    assert resp.status_code == 200
    assert app_client.get("/api/campaigns/camp-1/imagegen/fallback").json()["backend_id"] is None


def test_list_image_jobs_returns_empty_for_new_campaign(app_client) -> None:
    resp = app_client.get("/api/campaigns/camp-1/images/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_image_unknown_returns_404(app_client) -> None:
    resp = app_client.get("/api/campaigns/camp-1/images/nope")
    assert resp.status_code == 404


def test_star_unknown_image_returns_404(app_client) -> None:
    resp = app_client.put("/api/campaigns/camp-1/images/nope/star", json={"starred": True})
    assert resp.status_code == 404


def test_delete_unknown_image_returns_404(app_client) -> None:
    resp = app_client.delete("/api/campaigns/camp-1/images/nope")
    assert resp.status_code == 404


def test_cancel_unknown_job_returns_404(app_client) -> None:
    resp = app_client.delete("/api/campaigns/camp-1/images/jobs/nope")
    assert resp.status_code == 404


def test_prioritize_unknown_job_returns_404(app_client) -> None:
    resp = app_client.patch("/api/campaigns/camp-1/images/jobs/nope", json={"priority": 9})
    assert resp.status_code == 404


def test_reroll_unknown_image_returns_404(app_client) -> None:
    resp = app_client.post("/api/campaigns/camp-1/images/nope/reroll")
    assert resp.status_code == 404


def test_variation_unknown_image_returns_404(app_client) -> None:
    resp = app_client.post("/api/campaigns/camp-1/images/nope/variation", json={"strength": 0.5})
    assert resp.status_code == 404


def test_generate_route_returns_job_id(app_client) -> None:
    resp = app_client.post(
        "/api/campaigns/camp-1/images/generate",
        json={"scene_id": None, "post_id": None, "priority": 5},
    )
    assert resp.status_code == 202
    assert "job_id" in resp.json()
