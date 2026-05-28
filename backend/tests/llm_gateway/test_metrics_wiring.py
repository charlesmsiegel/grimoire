"""Verify LLMGatewayService threads metrics through complete() and stream() (#355)."""

from __future__ import annotations

import json
from types import MethodType

import pytest

from grimoire.observability.config import MetricsConfig
from grimoire.observability.metrics import MetricsRegistry
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture
async def db(tmp_path):
    database = Database(stamp_migrated_db(tmp_path / "gw.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_complete_records_metric(db):
    from grimoire.llm_gateway.gateway import LLMGatewayService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    gw = LLMGatewayService.__new__(LLMGatewayService)
    gw._metrics = metrics

    async def _inner(self, task, request, campaign_id=None, **kwargs):
        return None

    gw._complete_inner = MethodType(_inner, gw)
    gw.complete = MethodType(LLMGatewayService.complete, gw)

    await gw.complete("turn", object())  # type: ignore[arg-type]

    rows = await db.fetchall(
        "SELECT labels FROM metric_samples WHERE module = 'llm_gateway' AND metric = 'complete'"
    )
    assert len(rows) == 1
    payload = json.loads(rows[0]["labels"])
    assert payload["success"] is True
    assert payload["labels"]["task"] == "turn"


@pytest.mark.asyncio
async def test_stream_records_metric(db):
    from grimoire.llm_gateway.gateway import LLMGatewayService

    metrics = MetricsRegistry(db, config=MetricsConfig(enabled=True, sample_rate_hot_path=1.0))

    gw = LLMGatewayService.__new__(LLMGatewayService)
    gw._metrics = metrics

    async def _inner_gen(self, task, request, campaign_id=None, **kwargs):
        if False:
            yield  # pragma: no cover
        return

    gw._stream_inner = MethodType(_inner_gen, gw)
    gw.stream = MethodType(LLMGatewayService.stream, gw)

    async for _ in gw.stream("turn", object()):  # type: ignore[arg-type]
        pass

    rows = await db.fetchall("SELECT module, metric FROM metric_samples WHERE metric = 'stream'")
    assert len(rows) == 1
    assert rows[0]["module"] == "llm_gateway"
