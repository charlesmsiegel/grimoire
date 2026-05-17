"""Tests for §7: Per-call retry/timeout overrides on gateway calls.

Verifies that per-call `retry=` / `timeout=` kwargs override the global
GatewayConfig for that call only, that event payloads carry the override
info, and that no global-state mutation bleeds into subsequent calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
    RetryConfig,
    TimeoutConfig,
)
from grimoire.llm_gateway.errors import TransientError
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    Message,
    MessageRole,
    TokenUsage,
)
from tests.llm_gateway.conftest import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakePlugins,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _request() -> CompletionRequest:
    return CompletionRequest(
        model="ignored",
        messages=[Message(role=MessageRole.USER, content="hello")],
        max_tokens=64,
        temperature=0.0,
    )


def _config(**overrides) -> GatewayConfig:
    base = dict(
        default_routes={"main": "prov.model-a"},
        # Generous global defaults — tests will override per call.
        retry=RetryConfig(max_retries=3, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutConfig(total_seconds=30.0, first_token_seconds=10.0),
        embedding_cache=EmbeddingCacheConfig(enabled=False, max_entries=100),
        observability=ObservabilityConfig(log_all_requests=False),
    )
    base.update(overrides)
    return GatewayConfig(**base)


class EventCollector:
    """Subscribes to an EventBus and records all received events."""

    def __init__(self, bus: EventBus) -> None:
        self._events: list[Event] = []
        self._sub = bus.subscribe("*", self._handler)

    async def _handler(self, event: Event) -> None:
        self._events.append(event)

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def by_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.type == event_type]

    def cleanup(self) -> None:
        self._sub.unsubscribe()


# --------------------------------------------------------------------------- #
# Slow embedding provider (for timeout tests)
# --------------------------------------------------------------------------- #


@dataclass
class SlowEmbeddingProvider:
    id: str = "embed-slow"
    name: str = "slow-embeddings"
    model_id: str = "slow-model"
    dimensions: int = 4
    sleep_seconds: float = 1.0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await asyncio.sleep(self.sleep_seconds)
        return [[0.0, 1.0, 2.0, 3.0] for _ in texts]

    async def health_check(self):
        from grimoire.types.common import HealthLevel, HealthStatus

        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


# --------------------------------------------------------------------------- #
# Slow LLM provider (for timeout tests)
# --------------------------------------------------------------------------- #


@dataclass
class SlowLLMProvider:
    id: str
    name: str = "slow"
    sleep_seconds: float = 1.0
    call_count: int = 0

    async def complete(self, request: CompletionRequest):
        self.call_count += 1
        await asyncio.sleep(self.sleep_seconds)
        from grimoire.types.llm import CompletionResponse

        return CompletionResponse(
            text="too late",
            model=request.model,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        self.call_count += 1
        await asyncio.sleep(self.sleep_seconds)
        yield CompletionChunk(delta="never", is_final=True, usage=TokenUsage())

    async def list_models(self) -> list:
        return []


# --------------------------------------------------------------------------- #
# §7.1 — complete() with no override uses global config
# --------------------------------------------------------------------------- #


async def test_complete_no_override_uses_global_retry(db, plugins) -> None:
    """Provider raises once; global retry=3 allows recovery."""
    provider = FakeLLMProvider(
        id="prov",
        raise_sequence=[TransientError("blip")],
        response_text="recovered",
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    resp = await gw.complete("main", _request())
    # The call recovered via retry (1 failure + 1 success = 2 calls total).
    assert resp.text == "recovered"
    assert provider.call_count == 2


# --------------------------------------------------------------------------- #
# §7.2 — complete() retry=RetryConfig(max_retries=0): no retries
# --------------------------------------------------------------------------- #


async def test_complete_retry_override_zero_retries(db, plugins) -> None:
    """Override max_retries=0: first retriable failure is re-raised immediately."""
    provider = FakeLLMProvider(
        id="prov",
        raise_sequence=[TransientError("blip")],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    with pytest.raises(TransientError):
        await gw.complete("main", _request(), retry=RetryConfig(max_retries=0))

    # Only one call was made (no retry).
    assert provider.call_count == 1


# --------------------------------------------------------------------------- #
# §7.3 — complete() timeout override fires quickly
# --------------------------------------------------------------------------- #


async def test_complete_timeout_override_raises_quickly(db, plugins) -> None:
    """Tight per-call timeout fires before the slow provider responds."""
    slow = SlowLLMProvider(id="prov", sleep_seconds=1.0)
    plugins.add_llm(slow)
    gw = LLMGatewayService(plugins, db, _config())

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await gw.complete(
            "main",
            _request(),
            timeout=TimeoutConfig(total_seconds=0.05, first_token_seconds=0.05),
        )


# --------------------------------------------------------------------------- #
# §7.4 — stream() retry=RetryConfig(max_retries=0): first-chunk failure no retry
# --------------------------------------------------------------------------- #


async def test_stream_retry_override_zero_retries_no_retry(db, plugins) -> None:
    """With max_retries=0 and no fallback, first-chunk failure propagates immediately."""
    provider = FakeLLMProvider(
        id="prov",
        raise_sequence=[TransientError("boom")],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    with pytest.raises(TransientError):
        async for _ in gw.stream("main", _request(), retry=RetryConfig(max_retries=0)):
            pass

    # Only one attempt.
    assert provider.call_count == 1


async def test_stream_retry_override_zero_retries_falls_back(db, plugins) -> None:
    """With max_retries=0, a first-chunk failure skips retries and goes to fallback."""
    primary = FakeLLMProvider(
        id="primary",
        raise_sequence=[TransientError("primary fail")],
    )
    fallback = FakeLLMProvider(id="fallback", stream_chunks=["ok"])
    plugins.add_llm(primary)
    plugins.add_llm(fallback)

    cfg = _config(
        default_routes={"main": "primary.model-p"},
        fallback_routes={"main": "fallback.model-f"},
    )
    gw = LLMGatewayService(plugins, db, cfg)

    chunks = []
    async for chunk in gw.stream("main", _request(), retry=RetryConfig(max_retries=0)):
        chunks.append(chunk)

    # Fallback was used after 0 retries on primary.
    assert primary.call_count == 1  # only the single failure attempt
    assert any(c.delta == "ok" for c in chunks)


# --------------------------------------------------------------------------- #
# §7.5 — embed() timeout override fires quickly
# --------------------------------------------------------------------------- #


async def test_embed_timeout_override_raises_quickly(db, plugins) -> None:
    """Tight per-call timeout on embed fires before provider responds."""
    slow = SlowEmbeddingProvider(id="embed-slow", sleep_seconds=1.0)

    class _Plugins(FakePlugins):
        def get_embedding_provider(self, id: str):
            return slow if id == "embed-slow" else None

        def embedding_providers(self):
            return [slow]

    cfg = _config(default_routes={"posts": "embed-slow.slow-model"})
    gw = LLMGatewayService(_Plugins(), db, cfg)

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await gw.embed(
            "posts",
            ["hello"],
            timeout=TimeoutConfig(total_seconds=0.05, first_token_seconds=0.05),
        )


# --------------------------------------------------------------------------- #
# §7.6 — Event payload carries override fields
# --------------------------------------------------------------------------- #


async def test_complete_event_payload_carries_retry_override(db, plugins) -> None:
    """llm_request_started and llm_response_received carry retry_override."""
    provider = FakeLLMProvider(id="prov", response_text="ok")
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    override = RetryConfig(max_retries=0, initial_delay_ms=0, backoff_factor=1.0)
    await gw.complete("main", _request(), retry=override)

    started = collector.by_type("llm_request_started")[0]
    received = collector.by_type("llm_response_received")[0]

    assert started.payload["retry_override"] == {
        "max_retries": 0,
        "initial_delay_ms": 0,
        "backoff_factor": 1.0,
    }
    assert started.payload["timeout_override"] is None

    assert received.payload["retry_override"] == {
        "max_retries": 0,
        "initial_delay_ms": 0,
        "backoff_factor": 1.0,
    }
    assert received.payload["timeout_override"] is None


async def test_complete_event_payload_carries_timeout_override(db, plugins) -> None:
    """llm_request_started carries timeout_override when set."""
    provider = FakeLLMProvider(id="prov", response_text="ok")
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    t_override = TimeoutConfig(total_seconds=10.0, first_token_seconds=5.0)
    await gw.complete("main", _request(), timeout=t_override)

    started = collector.by_type("llm_request_started")[0]
    assert started.payload["retry_override"] is None
    assert started.payload["timeout_override"] == {
        "total_seconds": 10.0,
        "first_token_seconds": 5.0,
    }


async def test_embed_event_payload_carries_timeout_override(db, plugins) -> None:
    """embedding_request_started carries timeout_override when set."""
    embed = FakeEmbeddingProvider(id="embed-prov", model_id="emb-model")
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-prov.emb-model"})
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    t_override = TimeoutConfig(total_seconds=15.0, first_token_seconds=5.0)
    await gw.embed("posts", ["hello"], timeout=t_override)

    started = collector.by_type("embedding_request_started")[0]
    received = collector.by_type("embedding_response_received")[0]

    assert started.payload["timeout_override"] == {
        "total_seconds": 15.0,
        "first_token_seconds": 5.0,
    }
    assert started.payload["retry_override"] is None
    assert received.payload["timeout_override"] == {
        "total_seconds": 15.0,
        "first_token_seconds": 5.0,
    }
    assert received.payload["retry_override"] is None


async def test_no_override_event_payload_both_none(db, plugins) -> None:
    """When no override is passed, payload fields are both None."""
    provider = FakeLLMProvider(id="prov", response_text="ok")
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    await gw.complete("main", _request())

    started = collector.by_type("llm_request_started")[0]
    assert started.payload["retry_override"] is None
    assert started.payload["timeout_override"] is None


# --------------------------------------------------------------------------- #
# §7.7 — Per-call override does NOT mutate global state
# --------------------------------------------------------------------------- #


async def test_override_does_not_affect_subsequent_call(db, plugins) -> None:
    """After an overridden call, the next call still uses global config."""
    # Provider will fail once (retriable). Global max_retries=3, override=0.
    # First call: override=0 → immediate failure.
    # Second call: no override → 3 retries → succeeds after 1 failure.
    provider = FakeLLMProvider(
        id="prov",
        raise_sequence=[
            TransientError("first-call-fail"),  # used by first (override) call
            TransientError("second-call-blip"),  # first attempt of second call fails
            # second attempt of second call succeeds (no more raise items)
        ],
        response_text="global-default-success",
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    # First call with zero-retry override: should raise.
    with pytest.raises(TransientError):
        await gw.complete("main", _request(), retry=RetryConfig(max_retries=0))

    # After the failing override call, the global default still works.
    resp = await gw.complete("main", _request())
    assert resp.text == "global-default-success"
    # provider was called 3 times total: 1 (override) + 2 (global retry)
    assert provider.call_count == 3


async def test_stream_override_does_not_affect_subsequent_call(db, plugins) -> None:
    """Stream override with max_retries=0 does not affect a subsequent stream call."""
    provider = FakeLLMProvider(
        id="prov",
        raise_sequence=[
            TransientError("first-fail"),  # first (override) call fails
            TransientError("second-blip"),  # first attempt of second call fails
            # second attempt succeeds (no more raise items)
        ],
        stream_chunks=["hello"],
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    # Override call: fails immediately (0 retries).
    with pytest.raises(TransientError):
        async for _ in gw.stream("main", _request(), retry=RetryConfig(max_retries=0)):
            pass

    # Second call uses global retry=3: recovers after 1 blip.
    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)
    assert any(c.delta == "hello" for c in chunks)
