"""Tests for §6: Streaming retry / fallback on zero-chunk failures only.

Retry and fallback fire ONLY when zero chunks have been delivered.
Once any chunk has been yielded, subsequent failures propagate uncaught.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
)
from grimoire.llm_gateway.errors import (
    InvalidRequestError,
    TransientError,
)
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    Message,
    MessageRole,
    RetryPolicy,
    TimeoutPolicy,
)
from tests.llm_gateway.conftest import FakeLLMProvider, FakePlugins

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _request() -> CompletionRequest:
    return CompletionRequest(
        model="ignored",
        messages=[Message(role=MessageRole.USER, content="hi")],
        max_tokens=32,
        temperature=0.0,
    )


def _config(**overrides) -> GatewayConfig:
    base = dict(
        default_routes={"main": "primary.model-p"},
        retry=RetryPolicy(max_retries=2, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        embedding_cache=EmbeddingCacheConfig(enabled=False, max_entries=1),
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
    def types(self) -> list[str]:
        return [e.type for e in self._events]

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def by_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.type == event_type]

    def cleanup(self) -> None:
        self._sub.unsubscribe()


@dataclass
class MidStreamFailProvider:
    """Yields `chunks_before_fail` chunks then raises `error`."""

    id: str
    chunks_before_fail: int = 2
    error: BaseException = field(default_factory=lambda: TransientError("mid-stream boom"))
    call_count: int = 0
    seen_requests: list[CompletionRequest] = field(default_factory=list)

    async def complete(self, request: CompletionRequest) -> None:
        raise NotImplementedError

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        self.call_count += 1
        self.seen_requests.append(request)
        for i in range(self.chunks_before_fail):
            yield CompletionChunk(delta=f"chunk{i}", is_final=False)
        raise self.error

    async def list_models(self):
        return []

    async def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    async def health_check(self):
        from grimoire.types.common import HealthLevel, HealthStatus

        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


# --------------------------------------------------------------------------- #
# Test 1: Success on first try — no retries
# --------------------------------------------------------------------------- #


async def test_stream_success_on_first_try(db, plugins) -> None:
    """Stream yields chunks normally. ONE started, ONE received, no retries."""
    provider = FakeLLMProvider(id="primary", stream_chunks=["a", "b", "c"])
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)

    text = "".join(c.delta for c in chunks if c.delta)
    assert "a" in text and "b" in text and "c" in text

    assert collector.types == ["tier_resolved", "llm_request_started", "llm_response_received"]
    started = collector.by_type("llm_request_started")[0]
    assert started.payload["provider"] == "primary"
    assert started.payload["fallback_used"] is False

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["retries"] == 0
    assert received.payload["fallback_used"] is False

    assert provider.call_count == 1


# --------------------------------------------------------------------------- #
# Test 2: Zero-chunk transient failure, retry succeeds
# --------------------------------------------------------------------------- #


async def test_stream_zero_chunk_transient_retry_succeeds(db, plugins) -> None:
    """Provider raises before any chunk on attempt 1, succeeds on attempt 2.

    Expects: two llm_request_started, one llm_request_failed, one
    llm_response_received. Chunks from attempt 2 are yielded to the caller.
    """
    provider = FakeLLMProvider(
        id="primary",
        stream_chunks=["x", "y"],
        raise_sequence=[TransientError("flaky")],
    )
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)

    text = "".join(c.delta for c in chunks if c.delta)
    assert "x" in text and "y" in text

    # Attempt 1 failed, attempt 2 (first retry) succeeded
    assert provider.call_count == 2

    assert collector.types == [
        "tier_resolved",
        "llm_request_started",
        "llm_request_failed",
        "llm_request_started",
        "llm_response_received",
    ]

    started_events = collector.by_type("llm_request_started")
    assert started_events[0].payload["provider"] == "primary"
    assert started_events[1].payload["provider"] == "primary"

    failed_event = collector.by_type("llm_request_failed")[0]
    assert "TransientError" in failed_event.payload["error"]

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["fallback_used"] is False


# --------------------------------------------------------------------------- #
# Test 3: Zero-chunk transient failure, all retries exhausted, fallback succeeds
# --------------------------------------------------------------------------- #


async def test_stream_zero_chunk_retries_exhausted_fallback_succeeds(db, plugins) -> None:
    """Primary route exhausts all retries (max_retries=2, so 3 total attempts), then
    fallback route succeeds.

    Events: started(primary)*3, failed(primary), started(fallback), received(fallback).
    fallback_used: True in fallback event payloads.
    """
    primary = FakeLLMProvider(
        id="primary",
        stream_chunks=["z"],
        # Raise on all 3 attempts (initial + 2 retries)
        raise_sequence=[
            TransientError("fail1"),
            TransientError("fail2"),
            TransientError("fail3"),
        ],
    )
    secondary = FakeLLMProvider(id="secondary", stream_chunks=["from-fallback"])
    plugins.add_llm(primary)
    plugins.add_llm(secondary)

    cfg = _config(
        default_routes={"main": "primary.model-p"},
        fallback_routes={"main": "secondary.model-s"},
    )
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)

    text = "".join(c.delta for c in chunks if c.delta)
    assert "from-fallback" in text

    assert primary.call_count == 3  # initial + 2 retries
    assert secondary.call_count == 1

    assert collector.types == [
        "tier_resolved",
        "llm_request_started",  # primary attempt 1
        "llm_request_failed",  # primary attempt 1 fail
        "llm_request_started",  # primary retry 1
        "llm_request_failed",  # primary retry 1 fail
        "llm_request_started",  # primary retry 2
        "llm_request_failed",  # primary retry 2 fail
        "llm_request_started",  # fallback
        "llm_response_received",  # fallback success
    ]

    started_events = collector.by_type("llm_request_started")
    assert started_events[0].payload["provider"] == "primary"
    assert started_events[0].payload["fallback_used"] is False
    assert started_events[1].payload["provider"] == "primary"
    assert started_events[2].payload["provider"] == "primary"
    assert started_events[3].payload["provider"] == "secondary"
    assert started_events[3].payload["fallback_used"] is True

    failed_events = collector.by_type("llm_request_failed")
    assert len(failed_events) == 3
    for f in failed_events:
        assert f.payload["provider"] == "primary"

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["provider"] == "secondary"
    assert received.payload["fallback_used"] is True


# --------------------------------------------------------------------------- #
# Test 4: Zero-chunk PermanentError — no retries, fallback IS tried
# --------------------------------------------------------------------------- #


async def test_stream_zero_chunk_permanent_error_tries_fallback(db, plugins) -> None:
    """PermanentError is not retriable; fallback IS tried on first failure.

    Events: started(primary), failed(primary), started(fallback), received(fallback).
    """
    primary = FakeLLMProvider(
        id="primary",
        stream_chunks=["z"],
        raise_sequence=[InvalidRequestError("bad request")],
    )
    secondary = FakeLLMProvider(id="secondary", stream_chunks=["from-fallback"])
    plugins.add_llm(primary)
    plugins.add_llm(secondary)

    cfg = _config(
        default_routes={"main": "primary.model-p"},
        fallback_routes={"main": "secondary.model-s"},
    )
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)

    text = "".join(c.delta for c in chunks if c.delta)
    assert "from-fallback" in text

    # Primary should only have been called once (no retries for PermanentError)
    assert primary.call_count == 1
    assert secondary.call_count == 1

    assert collector.types == [
        "tier_resolved",
        "llm_request_started",  # primary
        "llm_request_failed",  # primary permanent fail
        "llm_request_started",  # fallback
        "llm_response_received",  # fallback success
    ]

    started_events = collector.by_type("llm_request_started")
    assert started_events[0].payload["provider"] == "primary"
    assert started_events[0].payload["fallback_used"] is False
    assert started_events[1].payload["provider"] == "secondary"
    assert started_events[1].payload["fallback_used"] is True

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["provider"] == "secondary"
    assert received.payload["fallback_used"] is True


# --------------------------------------------------------------------------- #
# Test 5: Mid-stream failure — partial chunks visible, no retry/fallback
# --------------------------------------------------------------------------- #


async def test_stream_mid_stream_failure_no_retry(db, plugins) -> None:
    """Provider yields 2 chunks then raises. Those 2 chunks reach the caller.
    No retry, no fallback. The exception propagates after the partial yield.
    """

    mid_stream_provider = MidStreamFailProvider(
        id="primary",
        chunks_before_fail=2,
        error=TransientError("mid-stream boom"),
    )

    # Build plugins manually — FakePlugins stores by id without type checks,
    # so we can inject our custom MidStreamFailProvider directly.
    plugins2 = FakePlugins()
    plugins2._llm["primary"] = mid_stream_provider

    gw = LLMGatewayService(plugins2, db, _config())

    chunks = []
    with pytest.raises(TransientError, match="mid-stream boom"):
        async for chunk in gw.stream("main", _request()):
            chunks.append(chunk)

    # The 2 chunks BEFORE the error must have reached the consumer
    assert len(chunks) == 2
    assert chunks[0].delta == "chunk0"
    assert chunks[1].delta == "chunk1"

    # Only one attempt — no retry for mid-stream failure
    assert mid_stream_provider.call_count == 1


# --------------------------------------------------------------------------- #
# Test 6: Zero-chunk failure, no fallback configured, retries exhausted — raises
# --------------------------------------------------------------------------- #


async def test_stream_zero_chunk_no_fallback_retries_exhausted_raises(db, plugins) -> None:
    """No fallback; primary exhausts retries. Stream raises the last exception."""
    provider = FakeLLMProvider(
        id="primary",
        stream_chunks=["z"],
        raise_sequence=[
            TransientError("fail1"),
            TransientError("fail2"),
            TransientError("fail3"),
        ],
    )
    plugins.add_llm(provider)

    # No fallback_routes configured
    cfg = _config(default_routes={"main": "primary.model-p"})
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    with pytest.raises(TransientError):
        async for _ in gw.stream("main", _request()):
            pass

    assert provider.call_count == 3  # initial + 2 retries

    # No fallback attempt
    started_events = collector.by_type("llm_request_started")
    for ev in started_events:
        assert ev.payload["provider"] == "primary"
    assert "llm_response_received" not in collector.types
