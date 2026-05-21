"""Integration tests for /api/observability/metrics/trend and /known (#355)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.main import create_app
from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.observability.service import ObservabilityService
from grimoire.storage import Database, apply_migrations


@pytest.fixture()
async def container_with_obs(tmp_path: Path) -> Iterator[ServiceContainer]:
    db = Database(tmp_path / "obs.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    obs = ObservabilityService(db=db)
    # Force exhaustive sampling for hot paths too so the seeded rows always land.
    obs.metrics_registry = MetricsRegistry(
        db,
        config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0, sample_rate_cold_path=1.0),
    )
    container = ServiceContainer(db=db)
    container.observability = obs
    yield container
    await obs.shutdown()
    await db.close()


@pytest.fixture()
def client(container_with_obs: ServiceContainer) -> Iterator[TestClient]:
    app = create_app()
    app.state.container = container_with_obs
    yield TestClient(app)


@pytest.mark.asyncio
async def test_trend_route_happy_path(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    await container_with_obs.observability.metrics().record(
        module="orchestrator", operation="turn", duration_ms=150.0
    )
    resp = client.get(
        "/api/observability/metrics/trend",
        params={
            "module": "orchestrator",
            "operation": "turn",
            "bucket": "minute",
            "window_seconds": 600,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, list)
    assert payload and payload[-1]["count"] >= 1


@pytest.mark.asyncio
async def test_trend_route_400_on_bad_bucket(client: TestClient) -> None:
    resp = client.get(
        "/api/observability/metrics/trend",
        params={
            "module": "orchestrator",
            "operation": "turn",
            "bucket": "second",
            "window_seconds": 60,
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_trend_route_400_on_bad_window(client: TestClient) -> None:
    resp = client.get(
        "/api/observability/metrics/trend",
        params={
            "module": "orchestrator",
            "operation": "turn",
            "bucket": "minute",
            "window_seconds": 0,
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_known_route_returns_pairs(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    obs = container_with_obs.observability
    await obs.metrics().record(module="orchestrator", operation="turn", duration_ms=10.0)
    await obs.metrics().record(module="llm_gateway", operation="complete", duration_ms=20.0)
    resp = client.get("/api/observability/metrics/known")
    assert resp.status_code == 200
    payload = resp.json()
    pairs = {(p["module"], p["operation"]) for p in payload}
    assert pairs == {("orchestrator", "turn"), ("llm_gateway", "complete")}
