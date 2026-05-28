"""Smoke tests for the /api/observability routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.main import create_app
from grimoire.observability.config import CostConfig, ObservabilityConfig
from grimoire.observability.service import ObservabilityService
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.observability import LogEvent, LogLevel, TurnAudit


@pytest.fixture()
async def container_with_obs(tmp_path: Path) -> Iterator[ServiceContainer]:
    db = Database(stamp_migrated_db(tmp_path / "obs.sqlite"), pool_size=2)
    await db.connect()
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


@pytest.fixture()
def ws_client(container_with_obs: ServiceContainer) -> Iterator[TestClient]:
    """TestClient with the lifespan engaged so ``client.portal`` is available
    for cross-loop calls — required for WebSocket tests that need to invoke
    server-side coroutines while a socket is open."""
    app = create_app()
    app.state.container = container_with_obs
    with TestClient(app) as test_client:
        yield test_client


async def _seed_audit(obs: ObservabilityService, *, turn_id: str = "t_test") -> TurnAudit:
    audit = TurnAudit(
        turn_id=turn_id,
        campaign_id="c1",
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
    msg = body["messages"][0]
    assert msg["content"] == "hello"
    # Per-message tokens always reported (estimated); tier is None for legacy
    # audits whose messages lack the metadata tag.
    assert "tokens" in msg
    assert msg["tokens"] >= 1
    assert "tier" in msg


@pytest.mark.asyncio
async def test_get_turn_prompt_tier_from_metadata(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    obs = container_with_obs.observability
    audit = TurnAudit(
        turn_id="t_tier",
        campaign_id="c1",
        started_at=datetime.now(UTC),
        assembled_messages=[
            {"role": "system", "content": "lock-in block", "metadata": {"tier": "lock-in"}},
            {"role": "user", "content": "go north", "metadata": {"tier": "player-input"}},
        ],
    )
    await obs.record_turn_audit(audit)
    resp = client.get("/api/observability/turns/t_tier/prompt")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [m["tier"] for m in msgs] == ["lock-in", "player-input"]


@pytest.mark.asyncio
async def test_get_turn_prompt_diff(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    obs = container_with_obs.observability
    await obs.record_turn_audit(
        TurnAudit(
            turn_id="t_prev",
            campaign_id="c1",
            started_at=datetime.now(UTC),
            assembled_messages=[
                {"role": "system", "content": "old", "metadata": {"tier": "lock-in"}},
            ],
            context_messages_hash="h-prev",
        )
    )
    await obs.record_turn_audit(
        TurnAudit(
            turn_id="t_curr",
            campaign_id="c1",
            started_at=datetime.now(UTC),
            assembled_messages=[
                {"role": "system", "content": "new", "metadata": {"tier": "lock-in"}},
            ],
            context_messages_hash="h-curr",
        )
    )
    resp = client.get("/api/observability/turns/t_curr/prompt/diff?against=t_prev")
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages_hash_changed"] is True
    assert body["turn_id_a"] == "t_prev"
    assert body["turn_id_b"] == "t_curr"
    assert len(body["changed_messages"]) == 1


@pytest.mark.asyncio
async def test_get_turn_prompt_diff_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/api/observability/turns/missing/prompt/diff?against=also-missing")
    assert resp.status_code == 404


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
            id, campaign_id, turn_id, source, kind,
            target_scope, target_table, target_path, target_id,
            before, after, confidence, applied_at, reversed_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            "d_a",
            "c1",
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


# ---------------------------------------------------------------------- #
# /ws/observability/log — live debug-log tail (spec 16 §13)
# ---------------------------------------------------------------------- #


def _log_event(
    *,
    module: str = "extractor",
    operation: str = "extract",
    level: LogLevel = LogLevel.INFO,
    message: str = "hello",
    turn_id: str | None = None,
) -> LogEvent:
    return LogEvent(
        timestamp=datetime.now(UTC),
        level=level,
        module=module,
        operation=operation,
        payload={"message": message},
        turn_id=turn_id,
    )


def test_log_tail_streams_event(
    container_with_obs: ServiceContainer, ws_client: TestClient
) -> None:
    """A connected tail receives each accepted event."""
    obs = container_with_obs.observability
    with ws_client.websocket_connect("/ws/observability/log") as ws:
        # The connect() call's accept() races with the route's subscribe()
        # path; if we call log() too early, the subscriber doesn't exist yet.
        # ping the server with an idempotent log of our own after the WS is
        # known-open by the test client.
        ws_client.portal.call(obs.log, _log_event(message="hello tail"))
        msg = ws.receive_json()
        assert msg["module"] == "extractor"
        assert msg["payload"]["message"] == "hello tail"


def test_log_tail_filters_by_minimum_level(
    container_with_obs: ServiceContainer, ws_client: TestClient
) -> None:
    obs = container_with_obs.observability
    with ws_client.websocket_connect("/ws/observability/log?level=warning") as ws:
        ws_client.portal.call(obs.log, _log_event(level=LogLevel.INFO, message="ignored"))
        ws_client.portal.call(obs.log, _log_event(level=LogLevel.ERROR, message="kept"))
        msg = ws.receive_json()
        assert msg["level"] == "ERROR"
        assert msg["payload"]["message"] == "kept"


def test_log_tail_filters_by_module(
    container_with_obs: ServiceContainer, ws_client: TestClient
) -> None:
    obs = container_with_obs.observability
    with ws_client.websocket_connect("/ws/observability/log?module=orchestrator") as ws:
        ws_client.portal.call(obs.log, _log_event(module="extractor", message="skip"))
        ws_client.portal.call(obs.log, _log_event(module="orchestrator", message="match"))
        msg = ws.receive_json()
        assert msg["module"] == "orchestrator"
        assert msg["payload"]["message"] == "match"


def test_log_tail_invalid_level_closes_with_policy_violation(
    ws_client: TestClient,
) -> None:
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as exc,
        ws_client.websocket_connect("/ws/observability/log?level=bogus"),
    ):
        pass
    assert exc.value.code == 1008


def test_log_tail_unavailable_without_observability(
    container_with_obs: ServiceContainer, ws_client: TestClient
) -> None:
    """Without an ObservabilityService on the container, close with 1011."""
    from starlette.websockets import WebSocketDisconnect

    container_with_obs.observability = None
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        ws_client.websocket_connect("/ws/observability/log"),
    ):
        pass
    assert exc.value.code == 1011


@pytest.mark.asyncio
async def test_turn_costs_returns_sorted_list(
    container_with_obs: ServiceContainer, client: TestClient
) -> None:
    db = container_with_obs.db
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO cost_records"
        " (campaign_id, turn_id, task, model, cost_usd,"
        " input_tokens, output_tokens, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("c1", "t_cost", "extraction", "m-2", 0.001, 400, 50, now),
    )
    await db.execute(
        "INSERT INTO cost_records"
        " (campaign_id, turn_id, task, model, cost_usd,"
        " input_tokens, output_tokens, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("c1", "t_cost", "primary", "m-1", 0.05, 800, 350, now),
    )

    resp = client.get("/api/observability/turns/t_cost/costs")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert [r["task"] for r in rows] == ["primary", "extraction"]
    assert rows[0]["total_usd"] == 0.05
    assert rows[0]["input_tokens"] == 800
    assert rows[0]["output_tokens"] == 350
    assert rows[0]["call_count"] == 1
    assert rows[1]["task"] == "extraction"
    assert rows[1]["input_tokens"] == 400


@pytest.mark.asyncio
async def test_turn_costs_unknown_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/api/observability/turns/no_such_turn/costs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_cost_config_defaults(client: TestClient) -> None:
    resp = client.get("/api/observability/config/cost")
    assert resp.status_code == 200
    assert resp.json() == {
        "surface_in_status_bar": True,
        "daily_budget_warn_usd": 5.00,
        "daily_budget_alert_usd": 20.00,
    }


@pytest.mark.asyncio
async def test_cost_config_custom(tmp_path: Path) -> None:
    db = Database(stamp_migrated_db(tmp_path / "obs2.sqlite"), pool_size=2)
    await db.connect()
    obs = ObservabilityService(
        db=db,
        config=ObservabilityConfig(
            cost=CostConfig(
                surface_in_status_bar=False,
                daily_budget_warn_usd=1.25,
                daily_budget_alert_usd=9.99,
            )
        ),
    )
    container = ServiceContainer(db=db)
    container.observability = obs
    app = create_app()
    app.state.container = container
    try:
        with TestClient(app) as test_client:
            resp = test_client.get("/api/observability/config/cost")
        assert resp.status_code == 200
        assert resp.json() == {
            "surface_in_status_bar": False,
            "daily_budget_warn_usd": 1.25,
            "daily_budget_alert_usd": 9.99,
        }
    finally:
        await obs.shutdown()
        await db.close()
