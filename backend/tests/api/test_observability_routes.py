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
async def test_get_turn_deltas_envelope_shape(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    """Issue #351: the deltas endpoint returns an {applied, queued} envelope.

    Verifies the API shape end-to-end so the frontend "What changed?"
    panel can rely on the contract without re-deriving it.
    """
    from grimoire.types.common import Scope
    from grimoire.types.state import (
        AppliedDelta,
        DeltaKind,
        ReviewItem,
        StateDelta,
    )

    obs = container_with_obs.observability
    db = obs.audit_store._db
    await db.execute(
        """
        INSERT INTO deltas (
            id, campaign_id, branch_id, turn_id, source, kind,
            target_scope, target_table, target_path, target_id,
            before, after, confidence, applied_at, reversed_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            "d_a",
            "c1",
            "main",
            "t_with_deltas",
            "extractor",
            DeltaKind.FACT_ADD.value,
            Scope.CAMPAIGN_SQLITE.value,
            None,
            None,
            "f_1",
            "{}",
            '{"name": "curfew"}',
            0.95,
            datetime.now(UTC).isoformat(),
            "",
        ),
    )

    audit = TurnAudit(
        turn_id="t_with_deltas",
        campaign_id="c1",
        branch_id="main",
        scene_id="s1",
        started_at=datetime.now(UTC),
        extracted_deltas=[
            StateDelta(
                kind=DeltaKind.FACT_ADD,
                target_scope=Scope.CAMPAIGN_SQLITE,
                target_id="f_1",
                source="extractor",
                evidence="A curfew is announced.",
                confidence=0.95,
            ),
        ],
        applied_deltas=[
            AppliedDelta(
                id="d_a",
                delta=StateDelta(
                    kind=DeltaKind.FACT_ADD,
                    target_scope=Scope.CAMPAIGN_SQLITE,
                    target_id="f_1",
                ),
                campaign_id="c1",
                branch_id="main",
                turn_id="t_with_deltas",
                applied_at=datetime.now(UTC),
            ),
        ],
        queued_for_review=[
            ReviewItem(
                id="r_pending",
                delta=StateDelta(
                    kind=DeltaKind.FACT_ADD,
                    target_scope=Scope.CAMPAIGN_SQLITE,
                    target_id="f_1",
                ),
                campaign_id="c1",
            )
        ],
    )
    await obs.record_turn_audit(audit)

    resp = client.get("/api/observability/turns/t_with_deltas/deltas")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"applied", "queued"}
    assert len(body["applied"]) == 1
    applied = body["applied"][0]
    assert applied["status"] == "auto"
    assert applied["evidence"] == "A curfew is announced."
    assert applied["strategy"] == "extractor"
    assert applied["confidence"] == 0.95


@pytest.mark.asyncio
async def test_get_turn_deltas_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/api/observability/turns/never-existed/deltas")
    assert resp.status_code == 404


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
