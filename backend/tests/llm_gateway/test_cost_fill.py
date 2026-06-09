"""Tests for §5: Populate cost_estimate_usd from real token usage."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
    PricingOverride,
)
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.llm import (
    CompletionRequest,
    Message,
    MessageRole,
    ModelInfo,
    RetryPolicy,
    TimeoutPolicy,
    TokenUsage,
)
from tests.llm_gateway.conftest import FakeLLMProvider, FakePlugins

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _request(model: str = "ignored") -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=[Message(role=MessageRole.USER, content="hello")],
        max_tokens=64,
        temperature=0.0,
    )


def _config(**overrides) -> GatewayConfig:
    base = dict(
        default_routes={"main": "prov.model-a"},
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100),
        observability=ObservabilityConfig(log_all_requests=True),
    )
    base.update(overrides)
    return GatewayConfig(**base)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(stamp_migrated_db(tmp_path / "cost_fill.sqlite"), pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def plugins() -> FakePlugins:
    return FakePlugins()


# --------------------------------------------------------------------------- #
# §5 tests
# --------------------------------------------------------------------------- #


async def test_cost_filled_from_token_usage(db, plugins) -> None:
    """Provider returns cost=None; gateway fills from ModelInfo pricing."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())

    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02  # 0.01 + 0.01 = 0.02
    assert resp.cost_estimate_usd == pytest.approx(expected)


async def test_provider_cost_not_overwritten(db, plugins) -> None:
    """Provider returns a cost; gateway must NOT overwrite it."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=0.99,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())

    assert resp.cost_estimate_usd == pytest.approx(0.99)


async def test_cost_filled_when_only_output_cost_set(db, plugins) -> None:
    """input_cost_per_1k=None but output_cost_per_1k=0.02 → treat input as 0."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=None,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())

    expected = 0 + 500 / 1000.0 * 0.02  # 0.01
    assert resp.cost_estimate_usd == pytest.approx(expected)


async def test_cost_filled_when_only_input_cost_set(db, plugins) -> None:
    """output_cost_per_1k=None but input_cost_per_1k=0.01 → treat output as 0."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=None,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())

    expected = 1000 / 1000.0 * 0.01 + 0  # 0.01
    assert resp.cost_estimate_usd == pytest.approx(expected)


async def test_cost_stays_none_when_both_costs_none(db, plugins) -> None:
    """Both costs None → skip computation; cost stays None."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=None,
                output_cost_per_1k=None,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())

    assert resp.cost_estimate_usd is None


async def test_pricing_cache_hit_only_calls_list_models_once(db, plugins) -> None:
    """Two calls with same provider/model → list_models() called exactly once."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=100, output_tokens=50),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    list_models_call_count = 0
    original_list_models = provider.list_models

    async def counting_list_models():
        nonlocal list_models_call_count
        list_models_call_count += 1
        return await original_list_models()

    provider.list_models = counting_list_models

    gw = LLMGatewayService(plugins, db, _config())

    await gw.complete("main", _request())
    await gw.complete("main", _request())

    assert list_models_call_count == 1


async def test_list_models_raises_no_propagation_cost_none(db, plugins) -> None:
    """list_models() raises → error does not propagate; cost stays None."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=100, output_tokens=50),
        response_cost=None,
        models=[],
    )
    plugins.add_llm(provider)

    async def failing_list_models():
        raise RuntimeError("pricing API down")

    provider.list_models = failing_list_models

    gw = LLMGatewayService(plugins, db, _config())

    # Should not raise
    resp = await gw.complete("main", _request())
    assert resp.cost_estimate_usd is None


async def test_list_models_raises_second_call_no_retry(db, plugins) -> None:
    """After list_models() fails, next call also gets None (cache hit on None)."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=100, output_tokens=50),
        response_cost=None,
        models=[],
    )
    plugins.add_llm(provider)
    call_count = 0

    async def failing_list_models():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("pricing API down")

    provider.list_models = failing_list_models

    gw = LLMGatewayService(plugins, db, _config())

    resp1 = await gw.complete("main", _request())
    resp2 = await gw.complete("main", _request())

    assert resp1.cost_estimate_usd is None
    assert resp2.cost_estimate_usd is None
    # list_models only called once due to None caching
    assert call_count == 1


async def test_cost_appears_in_audit_log(db, plugins) -> None:
    """cost_estimate_usd filled value shows up in the llm_requests audit row."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    await gw.complete("main", _request())

    row = await db.fetchone("SELECT cost_usd FROM llm_requests")
    assert row is not None
    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert row["cost_usd"] == pytest.approx(expected)


async def test_cost_appears_in_response_received_event(db, plugins) -> None:
    """cost_estimate_usd filled value shows up in the llm_response_received event."""

    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    bus = EventBus()
    received_events = []
    bus.subscribe("llm_response_received", lambda e: received_events.append(e))

    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)
    await gw.complete("main", _request())

    assert len(received_events) == 1
    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert received_events[0].payload["cost_estimate_usd"] == pytest.approx(expected)


async def test_stream_prefers_provider_reported_cost(db, plugins) -> None:
    """A cost on the final chunk (actual charge) wins over the price book,
    mirroring how complete() preserves a provider-supplied cost."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        stream_cost=0.99,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    async for _ in gw.stream("main", _request()):
        pass

    row = await db.fetchone("SELECT cost_usd FROM llm_requests")
    assert row is not None
    assert row["cost_usd"] == pytest.approx(0.99)


async def test_stream_cost_falls_back_to_price_book(db, plugins) -> None:
    """Without a chunk-supplied cost, streaming still computes from usage."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        stream_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=0.01,
                output_cost_per_1k=0.02,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    async for _ in gw.stream("main", _request()):
        pass

    row = await db.fetchone("SELECT cost_usd FROM llm_requests")
    assert row is not None
    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert row["cost_usd"] == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# Pricing overrides
# --------------------------------------------------------------------------- #


async def test_pricing_override_used_when_provider_returns_none(db, plugins) -> None:
    """Provider has no pricing; override supplies it."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(id="model-a", name="Model A"),
        ],
    )
    plugins.add_llm(provider)
    config = _config(
        pricing_overrides={
            "model-a": PricingOverride(input_cost_per_1k=0.01, output_cost_per_1k=0.02)
        }
    )
    gw = LLMGatewayService(plugins, db, config)

    resp = await gw.complete("main", _request())

    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert resp.cost_estimate_usd == pytest.approx(expected)


async def test_pricing_override_takes_precedence_over_provider(db, plugins) -> None:
    """Override wins even when the provider reports pricing."""
    provider = FakeLLMProvider(
        id="prov",
        response_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        response_cost=None,
        models=[
            ModelInfo(
                id="model-a",
                name="Model A",
                input_cost_per_1k=999.0,
                output_cost_per_1k=999.0,
            ),
        ],
    )
    plugins.add_llm(provider)
    config = _config(
        pricing_overrides={
            "model-a": PricingOverride(input_cost_per_1k=0.01, output_cost_per_1k=0.02)
        }
    )
    gw = LLMGatewayService(plugins, db, config)

    resp = await gw.complete("main", _request())

    expected = 1000 / 1000.0 * 0.01 + 500 / 1000.0 * 0.02
    assert resp.cost_estimate_usd == pytest.approx(expected)
