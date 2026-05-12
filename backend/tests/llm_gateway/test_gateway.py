"""End-to-end tests for the LLM Gateway service."""

from __future__ import annotations

import pytest

from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
    RetryConfig,
    TimeoutConfig,
)
from grimoire.llm_gateway.errors import (
    InvalidRequestError,
    ProviderNotFoundError,
    RouteNotFoundError,
    TransientError,
)
from grimoire.types.common import HealthLevel
from grimoire.types.llm import (
    CompletionRequest,
    Message,
    MessageRole,
    ModelInfo,
    TokenUsage,
)
from tests.llm_gateway.conftest import FakeEmbeddingProvider, FakeLLMProvider


def _request(model: str = "ignored") -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=[Message(role=MessageRole.USER, content="hello")],
        max_tokens=64,
        temperature=0.5,
    )


def _config(**overrides) -> GatewayConfig:
    base = dict(
        default_routes={"main": "anthropic.opus", "drift_check": "anthropic.haiku"},
        retry=RetryConfig(max_retries=2, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutConfig(total_seconds=2.0, first_token_seconds=1.0),
        embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100),
        observability=ObservabilityConfig(log_all_requests=True),
    )
    base.update(overrides)
    return GatewayConfig(**base)


async def test_complete_resolves_route_and_overrides_model(db, plugins) -> None:
    provider = FakeLLMProvider(id="anthropic", response_text="hi there")
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request(), campaign_id="camp-1")

    assert resp.text == "hi there"
    assert provider.seen_requests[0].model == "opus"
    row = await db.fetchone("SELECT provider, model, task FROM llm_requests")
    assert (row["provider"], row["model"], row["task"]) == ("anthropic", "opus", "main")


async def test_complete_raises_route_not_found(db, plugins) -> None:
    gw = LLMGatewayService(plugins, db, _config())
    with pytest.raises(RouteNotFoundError):
        await gw.complete("never-configured", _request())


async def test_complete_raises_provider_not_found(db, plugins) -> None:
    gw = LLMGatewayService(plugins, db, _config())
    with pytest.raises(ProviderNotFoundError):
        await gw.complete("main", _request())


async def test_complete_retries_transient_errors(db, plugins) -> None:
    provider = FakeLLMProvider(
        id="anthropic",
        raise_sequence=[TransientError("blip"), TransientError("blip")],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())

    assert resp.text == "hello"
    assert provider.call_count == 3
    row = await db.fetchone("SELECT retries, fallback_used FROM llm_requests")
    assert row["retries"] == 2
    assert row["fallback_used"] == 0


async def test_complete_falls_back_after_primary_exhausted(db, plugins) -> None:
    primary = FakeLLMProvider(
        id="cloud",
        raise_sequence=[TransientError("a"), TransientError("b"), TransientError("c")],
    )
    secondary = FakeLLMProvider(id="local", response_text="from local")
    plugins.add_llm(primary)
    plugins.add_llm(secondary)

    cfg = _config(
        default_routes={"main": "cloud.big"},
        fallback_routes={"main": "local.small"},
    )
    gw = LLMGatewayService(plugins, db, cfg)
    resp = await gw.complete("main", _request())

    assert resp.text == "from local"
    rows = await db.fetchall(
        "SELECT provider, fallback_used, error FROM llm_requests ORDER BY rowid"
    )
    assert len(rows) == 2
    assert rows[0]["provider"] == "cloud"
    assert rows[0]["error"] is not None
    assert rows[0]["fallback_used"] == 0
    assert rows[1]["provider"] == "local"
    assert rows[1]["fallback_used"] == 1


async def test_stream_yields_chunks_and_logs(db, plugins) -> None:
    provider = FakeLLMProvider(id="anthropic", stream_chunks=["he", "ll", "o"])
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)

    deltas = [c.delta for c in chunks]
    assert deltas == ["he", "ll", "o", ""]
    assert chunks[-1].is_final
    row = await db.fetchone("SELECT prompt_tokens, completion_tokens FROM llm_requests")
    assert row is not None
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5


async def test_embed_caches_per_text(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-fake", model_id="m1")
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-fake.m1"})
    gw = LLMGatewayService(plugins, db, cfg)

    a = await gw.embed("posts", ["alpha", "beta"])
    assert len(a) == 2
    assert embed.call_count == 1

    # Second call hits the cache for both entries.
    b = await gw.embed("posts", ["alpha", "beta"])
    assert b == a
    assert embed.call_count == 1

    # Adding a new text only computes the new one.
    c = await gw.embed("posts", ["alpha", "gamma"])
    assert embed.call_count == 2
    assert embed.seen_inputs[-1] == ["gamma"]
    assert c[0] == a[0]


async def test_embed_returns_vectors_in_input_order(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-fake", model_id="m1")
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-fake.m1"})
    gw = LLMGatewayService(plugins, db, cfg)

    # Warm cache for "alpha"
    await gw.embed("posts", ["alpha"])
    embed.call_count = 0
    embed.seen_inputs.clear()
    # Mixed cached + uncached; ensure order maps back correctly.
    out = await gw.embed("posts", ["beta", "alpha", "gamma", "alpha"])
    assert len(out) == 4
    assert out[1] == out[3]  # both "alpha"
    # "beta" + "gamma" were missing → single batch call with two inputs.
    assert embed.call_count == 1
    assert embed.seen_inputs[0] == ["beta", "gamma"]


async def test_embed_disabled_cache_calls_every_time(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-fake", model_id="m1")
    plugins.add_embedding(embed)
    cfg = _config(
        default_routes={"posts": "embed-fake.m1"},
        embedding_cache=EmbeddingCacheConfig(enabled=False, max_entries=10),
    )
    gw = LLMGatewayService(plugins, db, cfg)
    await gw.embed("posts", ["alpha"])
    await gw.embed("posts", ["alpha"])
    assert embed.call_count == 2


async def test_set_route_updates_resolution(db, plugins) -> None:
    plugins.add_llm(FakeLLMProvider(id="other", response_text="other"))
    gw = LLMGatewayService(plugins, db, _config())
    await gw.set_route("main", "other.modelX", campaign_id="camp")
    resp = await gw.complete("main", _request(), campaign_id="camp")
    assert resp.text == "other"
    routes = await gw.list_routes("camp")
    assert routes["main"] == "other.modelX"


async def test_estimate_cost_uses_provider_pricing(db, plugins) -> None:
    provider = FakeLLMProvider(
        id="anthropic",
        models=[
            ModelInfo(
                id="opus",
                name="opus",
                input_cost_per_1k=0.015,
                output_cost_per_1k=0.075,
            )
        ],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())
    cost = await gw.estimate_cost(
        "main",
        CompletionRequest(
            model="ignored",
            messages=[Message(role=MessageRole.USER, content="x" * 400)],
            max_tokens=1000,
            temperature=0.0,
        ),
    )
    assert cost is not None
    assert cost > 0


async def test_estimate_tokens_falls_back_to_heuristic(db, plugins) -> None:
    gw = LLMGatewayService(plugins, db, _config())
    assert await gw.estimate_tokens("12345678") == 2


async def test_health_check_provider_passes_through(db, plugins) -> None:
    provider = FakeLLMProvider(id="anthropic")
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())
    status = await gw.health_check("anthropic")
    assert status.level == HealthLevel.HEALTHY


async def test_health_check_unknown_provider_unconfigured(db, plugins) -> None:
    gw = LLMGatewayService(plugins, db, _config())
    status = await gw.health_check("nope")
    assert status.level == HealthLevel.UNCONFIGURED


async def test_health_check_all_aggregates(db, plugins) -> None:
    plugins.add_llm(FakeLLMProvider(id="p1"))
    plugins.add_embedding(FakeEmbeddingProvider(id="p2"))
    gw = LLMGatewayService(plugins, db, _config())
    results = await gw.health_check_all()
    assert set(results) == {"p1", "p2"}
    assert all(s.level == HealthLevel.HEALTHY for s in results.values())


async def test_complete_disables_logging_when_configured(db, plugins) -> None:
    plugins.add_llm(FakeLLMProvider(id="anthropic"))
    cfg = _config(observability=ObservabilityConfig(log_all_requests=False))
    gw = LLMGatewayService(plugins, db, cfg)
    await gw.complete("main", _request())
    row = await db.fetchone("SELECT COUNT(*) AS c FROM llm_requests")
    assert row["c"] == 0


async def test_total_tokens_filled_in_when_provider_omits(db, plugins) -> None:
    provider = FakeLLMProvider(
        id="anthropic",
        response_usage=TokenUsage(input_tokens=7, output_tokens=3, total_tokens=0),
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())
    resp = await gw.complete("main", _request())
    assert resp.usage.total_tokens == 10
    row = await db.fetchone("SELECT total_tokens FROM llm_requests")
    assert row["total_tokens"] == 10


async def test_permanent_error_logs_zero_retries(db, plugins) -> None:
    provider = FakeLLMProvider(id="anthropic", raise_sequence=[InvalidRequestError("bad")])
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())
    with pytest.raises(InvalidRequestError):
        await gw.complete("main", _request())
    # PermanentError is raised on the first attempt; the audit row must
    # reflect that no retries happened.
    row = await db.fetchone("SELECT retries, error FROM llm_requests")
    assert row["retries"] == 0
    assert "InvalidRequestError" in row["error"]
    assert provider.call_count == 1


async def test_embed_logs_token_estimate_not_char_count(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-fake", model_id="m1")
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-fake.m1"})
    gw = LLMGatewayService(plugins, db, cfg)
    await gw.embed("posts", ["12345678"])  # 8 chars → ~2 tokens
    row = await db.fetchone("SELECT prompt_tokens FROM llm_requests")
    # Should be the chars//4 heuristic, not the raw character count.
    assert row["prompt_tokens"] == 2
