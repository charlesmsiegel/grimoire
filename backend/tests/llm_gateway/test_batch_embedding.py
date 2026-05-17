"""Tests for §8: batch embedding by provider max_batch_size."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
)
from grimoire.llm_gateway.errors import PermanentError, TransientError
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import ModelInfo, RetryPolicy, TimeoutPolicy

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@dataclass
class BatchFakeEmbeddingProvider:
    """Fake embedding provider with configurable max_batch_size."""

    id: str = "embed-fake"
    name: str = "fake-embeddings"
    model_id: str = "fake-model"
    dimensions: int = 4
    max_batch_size: int | None = None
    call_count: int = 0
    seen_inputs: list[list[str]] = field(default_factory=list)
    raise_sequence: list[BaseException] = field(default_factory=list)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        self.seen_inputs.append(list(texts))
        if self.raise_sequence:
            err = self.raise_sequence.pop(0)
            if err is not None:
                raise err
        out: list[list[float]] = []
        for text in texts:
            base = float(sum(ord(c) for c in text) % 100)
            out.append([base, base + 1.0, base + 2.0, base + 3.0])
        return out

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def health_check(self) -> HealthStatus:
        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


class EventCollector:
    def __init__(self, bus: EventBus) -> None:
        self._events: list[Event] = []
        self._sub = bus.subscribe("*", self._handler)

    async def _handler(self, event: Event) -> None:
        self._events.append(event)

    @property
    def types(self) -> list[str]:
        return [e.type for e in self._events]

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def by_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.type == event_type]

    def cleanup(self) -> None:
        self._sub.unsubscribe()


def _config(**overrides) -> GatewayConfig:
    base = dict(
        default_routes={"posts": "embed-fake.fake-model"},
        retry=RetryPolicy(max_retries=2, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100),
        observability=ObservabilityConfig(log_all_requests=False),
    )
    base.update(overrides)
    return GatewayConfig(**base)


# --------------------------------------------------------------------------- #
# §8.1 — Batches of [2, 2, 1] for 5 texts with max_batch_size=2
# --------------------------------------------------------------------------- #


async def test_batches_into_correct_sizes(db, plugins) -> None:
    """5 texts with batch_size=2 → 3 calls of sizes [2, 2, 1], result in input order."""
    provider = BatchFakeEmbeddingProvider(id="embed-fake", model_id="fake-model", max_batch_size=2)
    plugins.add_embedding(provider)
    gw = LLMGatewayService(plugins, db, _config())

    texts = ["a", "b", "c", "d", "e"]
    result = await gw.embed("posts", texts)

    assert provider.call_count == 3
    assert provider.seen_inputs == [["a", "b"], ["c", "d"], ["e"]]
    assert len(result) == 5

    # Verify order: each result matches the deterministic vector for its text
    for text, vec in zip(texts, result, strict=True):
        base = float(sum(ord(c) for c in text) % 100)
        assert vec == [base, base + 1.0, base + 2.0, base + 3.0]


# --------------------------------------------------------------------------- #
# §8.2 — max_batch_size=None → single call (unchanged behavior)
# --------------------------------------------------------------------------- #


async def test_no_batch_size_single_call(db, plugins) -> None:
    """Provider without max_batch_size uses the existing single-call path."""
    provider = BatchFakeEmbeddingProvider(
        id="embed-fake", model_id="fake-model", max_batch_size=None
    )
    plugins.add_embedding(provider)
    gw = LLMGatewayService(plugins, db, _config())

    result = await gw.embed("posts", ["x", "y", "z"])

    assert provider.call_count == 1
    assert provider.seen_inputs == [["x", "y", "z"]]
    assert len(result) == 3


# --------------------------------------------------------------------------- #
# §8.3 — max_batch_size >= len(missing) → single call
# --------------------------------------------------------------------------- #


async def test_batch_size_larger_than_input_single_call(db, plugins) -> None:
    """When batch_size >= len(missing), skip batching and use single call."""
    provider = BatchFakeEmbeddingProvider(
        id="embed-fake", model_id="fake-model", max_batch_size=100
    )
    plugins.add_embedding(provider)
    gw = LLMGatewayService(plugins, db, _config())

    result = await gw.embed("posts", ["a", "b", "c"])

    assert provider.call_count == 1
    assert provider.seen_inputs == [["a", "b", "c"]]
    assert len(result) == 3


# --------------------------------------------------------------------------- #
# §8.4 — All-or-nothing: batch 2/3 fails → whole call fails, no cache write,
#          no embedding_response_received, but llm_request_failed IS emitted
# --------------------------------------------------------------------------- #


async def test_batch_failure_all_or_nothing(db, plugins) -> None:
    """Failure on any batch causes whole embed to fail; no cache write, no success event."""
    # max_retries=0 so PermanentError raises immediately
    provider = BatchFakeEmbeddingProvider(
        id="embed-fake",
        model_id="fake-model",
        max_batch_size=2,
        raise_sequence=[
            None,  # batch 1 succeeds
            PermanentError("provider limit exceeded"),  # batch 2 fails
        ],
    )
    plugins.add_embedding(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    cfg = _config(
        embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100),
    )
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    with pytest.raises(PermanentError):
        await gw.embed("posts", ["a", "b", "c", "d", "e"])

    # No success event
    assert "embedding_response_received" not in collector.types
    # Failure event IS emitted
    assert "llm_request_failed" in collector.types

    # Verify no partial cache write: a fresh call with cache enabled should
    # call the provider again (nothing was cached)
    provider.call_count = 0
    provider.seen_inputs.clear()
    # Allow the fresh call to succeed
    provider.raise_sequence = []

    result = await gw.embed("posts", ["a", "b"])
    assert provider.call_count >= 1  # had to fetch from provider (not from cache)
    assert len(result) == 2


# --------------------------------------------------------------------------- #
# §8.5 — Partial cache hit: only missing texts are batched; order preserved
# --------------------------------------------------------------------------- #


async def test_partial_cache_hit_batched_correctly(db, plugins) -> None:
    """Cached texts are not re-fetched; missing texts are batched; result order matches input."""
    provider = BatchFakeEmbeddingProvider(
        id="embed-fake", model_id="fake-model", dimensions=4, max_batch_size=2
    )
    plugins.add_embedding(provider)
    gw = LLMGatewayService(plugins, db, _config())

    # Warm cache with "a" and "e"
    await gw.embed("posts", ["a", "e"])
    assert provider.call_count == 1  # single batch (2 texts, batch_size=2 → exact fit)

    # Reset counters
    provider.call_count = 0
    provider.seen_inputs.clear()

    # Now fetch ["a", "b", "c", "d", "e"]: "a" and "e" are cached, "b","c","d" are missing
    result = await gw.embed("posts", ["a", "b", "c", "d", "e"])

    # Missing: ["b", "c", "d"] → 2 batches: ["b","c"] and ["d"]
    assert provider.call_count == 2
    assert provider.seen_inputs == [["b", "c"], ["d"]]
    assert len(result) == 5

    # Verify correct order
    texts = ["a", "b", "c", "d", "e"]
    for text, vec in zip(texts, result, strict=True):
        base = float(sum(ord(c) for c in text) % 100)
        assert vec == [base, base + 1.0, base + 2.0, base + 3.0]


# --------------------------------------------------------------------------- #
# §8.6 — Retries summed: batch 1 retries 2x, batch 2 succeeds first try
#          → total retries in event payload == 2
# --------------------------------------------------------------------------- #


async def test_retry_count_summed_across_batches(db, plugins) -> None:
    """retries in the event payload is the sum of per-batch retry counts."""
    call_log: list[list[str]] = []

    class RetryProvider(BatchFakeEmbeddingProvider):
        attempt: int = 0

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.call_count += 1
            self.seen_inputs.append(list(texts))
            call_log.append(list(texts))
            # First call is always batch ["a","b"]; fail it twice, succeed on 3rd
            if texts == ["a", "b"] and self.attempt < 2:
                self.attempt += 1
                raise TransientError("temporary glitch")
            # All other calls succeed normally
            out: list[list[float]] = []
            for text in texts:
                base = float(sum(ord(c) for c in text) % 100)
                out.append([base, base + 1.0, base + 2.0, base + 3.0])
            return out

    provider = RetryProvider(
        id="embed-fake",
        model_id="fake-model",
        max_batch_size=2,
    )
    plugins.add_embedding(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    # Allow up to 3 retries so 2 failures + 1 success is within budget
    cfg = _config(
        retry=RetryPolicy(max_retries=3, initial_delay_ms=0, backoff_factor=1.0),
    )
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    result = await gw.embed("posts", ["a", "b", "c", "d"])
    assert len(result) == 4

    received = collector.by_type("embedding_response_received")
    assert len(received) == 1
    # 2 retries on batch 1, 0 on batch 2 → sum = 2
    assert received[0].payload["retries"] == 2
