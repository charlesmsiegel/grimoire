"""Tests for LLM lifecycle event emissions from LLMGatewayService."""

from __future__ import annotations

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
)
from grimoire.llm_gateway.errors import InvalidRequestError, TransientError
from grimoire.types.llm import CompletionRequest, Message, MessageRole, RetryPolicy, TimeoutPolicy
from tests.llm_gateway.conftest import FakeEmbeddingProvider, FakeLLMProvider

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
        retry=RetryPolicy(max_retries=2, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100),
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


# --------------------------------------------------------------------------- #
# complete() success path
# --------------------------------------------------------------------------- #


async def test_complete_emits_started_then_received(db, plugins) -> None:
    provider = FakeLLMProvider(id="prov", response_text="ok")
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    await gw.complete("main", _request(), campaign_id="camp-1", turn_id="turn-42")

    assert collector.types == ["tier_resolved", "llm_request_started", "llm_response_received"]

    started = collector.by_type("llm_request_started")[0]
    assert started.payload["task"] == "main"
    assert started.payload["provider"] == "prov"
    assert started.payload["model"] == "model-a"
    assert started.payload["campaign_id"] == "camp-1"
    assert started.payload["turn_id"] == "turn-42"
    assert started.payload["fallback_used"] is False

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["task"] == "main"
    assert received.payload["provider"] == "prov"
    assert received.payload["model"] == "model-a"
    assert received.payload["campaign_id"] == "camp-1"
    assert received.payload["turn_id"] == "turn-42"
    assert received.payload["fallback_used"] is False
    assert isinstance(received.payload["latency_ms"], int)
    assert received.payload["retries"] == 0
    assert "usage" in received.payload
    assert received.payload["usage"]["input_tokens"] == 10
    assert received.payload["usage"]["output_tokens"] == 5


# --------------------------------------------------------------------------- #
# complete() failure path — PermanentError
# --------------------------------------------------------------------------- #


async def test_complete_permanent_error_emits_started_then_failed(db, plugins) -> None:
    provider = FakeLLMProvider(id="prov", raise_sequence=[InvalidRequestError("bad request")])
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    with pytest.raises(InvalidRequestError):
        await gw.complete("main", _request())

    assert collector.types == ["tier_resolved", "llm_request_started", "llm_request_failed"]

    failed = collector.by_type("llm_request_failed")[0]
    assert failed.payload["task"] == "main"
    assert failed.payload["provider"] == "prov"
    assert "InvalidRequestError" in failed.payload["error"]
    assert failed.payload["retries"] == 0
    assert failed.payload["fallback_used"] is False


# --------------------------------------------------------------------------- #
# complete() retry + fallback path
# --------------------------------------------------------------------------- #


async def test_complete_fallback_emits_full_sequence(db, plugins) -> None:
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
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    resp = await gw.complete("main", _request())
    assert resp.text == "from local"

    # Expected sequence: tier_resolved → started(cloud) → failed(cloud) → started(local) → received(local)
    assert collector.types == [
        "tier_resolved",
        "llm_request_started",
        "llm_request_failed",
        "llm_request_started",
        "llm_response_received",
    ]

    started_events = collector.by_type("llm_request_started")
    assert started_events[0].payload["provider"] == "cloud"
    assert started_events[0].payload["fallback_used"] is False
    assert started_events[1].payload["provider"] == "local"
    assert started_events[1].payload["fallback_used"] is True

    failed_event = collector.by_type("llm_request_failed")[0]
    assert failed_event.payload["provider"] == "cloud"
    assert failed_event.payload["fallback_used"] is False

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["provider"] == "local"
    assert received.payload["fallback_used"] is True


# --------------------------------------------------------------------------- #
# stream() success path
# --------------------------------------------------------------------------- #


async def test_stream_emits_started_then_received(db, plugins) -> None:
    provider = FakeLLMProvider(id="prov", stream_chunks=["he", "ll", "o"])
    plugins.add_llm(provider)
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)

    chunks = []
    async for chunk in gw.stream("main", _request(), campaign_id="camp-2", turn_id="turn-7"):
        chunks.append(chunk)

    assert collector.types == ["tier_resolved", "llm_request_started", "llm_response_received"]

    started = collector.by_type("llm_request_started")[0]
    assert started.payload["task"] == "main"
    assert started.payload["provider"] == "prov"
    assert started.payload["model"] == "model-a"
    assert started.payload["campaign_id"] == "camp-2"
    assert started.payload["turn_id"] == "turn-7"
    assert started.payload["fallback_used"] is False

    received = collector.by_type("llm_response_received")[0]
    assert received.payload["task"] == "main"
    assert received.payload["retries"] == 0
    assert received.payload["fallback_used"] is False
    assert isinstance(received.payload["latency_ms"], int)


# --------------------------------------------------------------------------- #
# embed() cache miss
# --------------------------------------------------------------------------- #


async def test_embed_cache_miss_emits_embedding_events(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-prov", model_id="emb-model", dimensions=4)
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-prov.emb-model"})
    bus = EventBus()
    collector = EventCollector(bus)
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    await gw.embed("posts", ["alpha", "beta"], campaign_id="camp-3", turn_id="turn-99")

    assert collector.types == ["embedding_request_started", "embedding_response_received"]

    started = collector.by_type("embedding_request_started")[0]
    assert started.payload["task"] == "posts"
    assert started.payload["provider"] == "embed-prov"
    assert started.payload["model"] == "emb-model"
    assert started.payload["campaign_id"] == "camp-3"
    assert started.payload["turn_id"] == "turn-99"
    assert started.payload["input_count"] == 2

    received = collector.by_type("embedding_response_received")[0]
    assert received.payload["task"] == "posts"
    assert received.payload["vector_count"] == 2
    assert received.payload["input_count"] == 2
    assert received.payload["dimensions"] == 4
    assert isinstance(received.payload["latency_ms"], int)


# --------------------------------------------------------------------------- #
# embed() pure cache hit — NO events
# --------------------------------------------------------------------------- #


async def test_embed_pure_cache_hit_emits_no_events(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-prov", model_id="emb-model")
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-prov.emb-model"})
    bus = EventBus()
    gw = LLMGatewayService(plugins, db, cfg, event_bus=bus)

    # Warm the cache
    await gw.embed("posts", ["alpha", "beta"])

    # Now subscribe and do a pure cache hit
    collector = EventCollector(bus)
    await gw.embed("posts", ["alpha", "beta"])

    assert collector.types == [], "No events expected for a pure cache hit"


# --------------------------------------------------------------------------- #
# event_bus=None — no crash
# --------------------------------------------------------------------------- #


async def test_no_event_bus_does_not_crash(db, plugins) -> None:
    provider = FakeLLMProvider(id="prov", response_text="ok")
    plugins.add_llm(provider)
    # No event_bus passed (default None)
    gw = LLMGatewayService(plugins, db, _config())
    resp = await gw.complete("main", _request())
    assert resp.text == "ok"


async def test_no_event_bus_stream_does_not_crash(db, plugins) -> None:
    provider = FakeLLMProvider(id="prov", stream_chunks=["hi"])
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())
    chunks = []
    async for chunk in gw.stream("main", _request()):
        chunks.append(chunk)
    assert any(c.delta == "hi" for c in chunks)


async def test_no_event_bus_embed_does_not_crash(db, plugins) -> None:
    embed = FakeEmbeddingProvider(id="embed-prov", model_id="emb-model")
    plugins.add_embedding(embed)
    cfg = _config(default_routes={"posts": "embed-prov.emb-model"})
    gw = LLMGatewayService(plugins, db, cfg)
    result = await gw.embed("posts", ["hello"])
    assert len(result) == 1


# --------------------------------------------------------------------------- #
# Raising handler — LLM call must be unaffected
# --------------------------------------------------------------------------- #


async def test_raising_handler_does_not_break_llm_call(db, plugins) -> None:
    provider = FakeLLMProvider(id="prov", response_text="ok despite raise")
    plugins.add_llm(provider)
    bus = EventBus()

    async def bad_handler(event: Event) -> None:
        raise RuntimeError("handler boom")

    bus.subscribe("*", bad_handler)

    gw = LLMGatewayService(plugins, db, _config(), event_bus=bus)
    # Should NOT raise; the bad handler is swallowed by EventBus._invoke
    resp = await gw.complete("main", _request())
    assert resp.text == "ok despite raise"
