"""Verify the Orchestrator threads metrics through `_run_turn` (#355).

Doesn't retest measure() semantics — just that the producer wires it.
"""

from __future__ import annotations

import json
from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "orch.db")
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_run_turn_records_metric(db):
    """Invoking OrchestratorService._run_turn() emits a metric row.

    We bypass the real turn pipeline by patching `_run_turn_inner` to a
    fast no-op stub — the test only checks that the `measure("orchestrator",
    "turn")` wrapper records.
    """
    from grimoire.orchestrator.service import OrchestratorService

    metrics = MetricsRegistry(
        db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0)
    )

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._metrics = metrics

    async def _stub_inner(self, **kwargs):
        return "turn-id"

    orch._run_turn_inner = MethodType(_stub_inner, orch)
    orch._run_turn = MethodType(OrchestratorService._run_turn, orch)

    result = await orch._run_turn(
        campaign_id="camp1",
        scene_id="scene1",
        player_input="hi",
        triggering_pc=None,
    )
    assert result == "turn-id"

    rows = await db.fetchall("SELECT module, metric, labels FROM metric_samples")
    matches = [
        r for r in rows
        if r["module"] == "orchestrator"
        and r["metric"] == "turn"
        and json.loads(r["labels"])["success"] is True
    ]
    assert matches, f"no orchestrator/turn metric recorded: {rows}"
