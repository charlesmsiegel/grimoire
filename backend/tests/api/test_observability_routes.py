"""Smoke tests for the /api/observability routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.main import create_app
from grimoire.observability.service import ObservabilityService
from grimoire.storage import Database, apply_migrations
from grimoire.types.observability import TurnAudit


@pytest.fixture()
async def container_with_obs(tmp_path: Path) -> Iterator[ServiceContainer]:
    db = Database(tmp_path / "obs.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    obs = ObservabilityService(db=db)
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


async def _seed_audit(obs: ObservabilityService, *, turn_id: str = "t_test") -> TurnAudit:
    audit = TurnAudit(
        turn_id=turn_id,
        campaign_id="c1",
        branch_id="main",
        scene_id="s1",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        player_input="hello",
        llm_provider="fake",
        llm_model="fake-1",
        response_text="world",
        context_messages_hash="abc123",
        assembled_messages=[
            {"role": "user", "content": "hello", "metadata": {}, "name": None},
        ],
    )
    await obs.record_turn_audit(audit)
    return audit


@pytest.mark.asyncio
async def test_get_turn_audit_round_trip(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    await _seed_audit(container_with_obs.observability)
    resp = client.get("/api/observability/turns/t_test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["turn_id"] == "t_test"
    assert body["llm_provider"] == "fake"


@pytest.mark.asyncio
async def test_get_turn_audit_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/api/observability/turns/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_turn_prompt(container_with_obs: ServiceContainer, client: TestClient) -> None:
    await _seed_audit(container_with_obs.observability)
    resp = client.get("/api/observability/turns/t_test/prompt")
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages_hash"] == "abc123"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_health_latest_empty(client: TestClient) -> None:
    resp = client.get("/api/observability/health/latest")
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_costs_session_empty(client: TestClient) -> None:
    resp = client.get("/api/observability/costs/session?campaign_id=c1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_usd"] == 0.0


@pytest.mark.asyncio
async def test_costs_total_today_empty(client: TestClient) -> None:
    resp = client.get("/api/observability/costs/total_today?campaign_id=c1")
    assert resp.status_code == 200
    assert resp.json() == {"total_usd": 0.0}


@pytest.mark.asyncio
async def test_errors_aggregate_empty(client: TestClient) -> None:
    resp = client.get("/api/observability/errors/aggregate")
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_health_probe_unknown_returns_404(client: TestClient) -> None:
    resp = client.post("/api/observability/health/probe?target_id=nope")
    assert resp.status_code == 404
