"""Tests for §10: HealthStatus normalization in gateway.health_check()."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from grimoire.llm_gateway import LLMGatewayService
from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
)
from grimoire.storage import Database, apply_migrations
from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import ProviderCapabilities, RetryPolicy, TimeoutPolicy
from tests.llm_gateway.conftest import FakeLLMProvider, FakePlugins

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _config(**overrides) -> GatewayConfig:
    base = dict(
        default_routes={"main": "prov.model-a"},
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        embedding_cache=EmbeddingCacheConfig(enabled=True, max_entries=100),
        observability=ObservabilityConfig(log_all_requests=False),
    )
    base.update(overrides)
    return GatewayConfig(**base)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "health_norm.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def plugins() -> FakePlugins:
    return FakePlugins()


# --------------------------------------------------------------------------- #
# §10 tests
# --------------------------------------------------------------------------- #


async def test_provider_target_id_overridden(db, plugins) -> None:
    """Provider returns target_id='some-other-id'; gateway forces target_id=provider_id."""
    provider = FakeLLMProvider(id="my-provider")
    provider.health = HealthStatus(
        level=HealthLevel.HEALTHY,
        target_id="some-other-id",
        checked_at=None,
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    status = await gw.health_check("my-provider")

    assert status.target_id == "my-provider"
    assert status.level == HealthLevel.HEALTHY
    assert status.checked_at is not None


async def test_provider_checked_at_preserved_when_set(db, plugins) -> None:
    """Provider sets checked_at; gateway preserves it but overrides target_id."""
    existing_ts = "2026-01-01T00:00:00+00:00"
    provider = FakeLLMProvider(id="my-provider")
    provider.health = HealthStatus(
        level=HealthLevel.HEALTHY,
        target_id="something",
        checked_at=existing_ts,
    )
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    status = await gw.health_check("my-provider")

    assert status.target_id == "my-provider"
    assert status.checked_at == existing_ts


async def test_no_health_check_method_returns_healthy_normalized(db, plugins) -> None:
    """Provider without health_check → HEALTHY; target_id and checked_at populated."""

    @dataclass
    class MinimalProvider:
        id: str = "minimal-prov"
        name: str = "minimal"
        capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

        async def complete(self, request): ...

        def stream(self, request): ...

        async def list_models(self):
            return []

        async def estimate_tokens(self, text: str) -> int:
            return 1

        # NOTE: intentionally no health_check method

    provider = MinimalProvider(id="minimal-prov")
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    status = await gw.health_check("minimal-prov")

    assert status.level == HealthLevel.HEALTHY
    assert status.target_id == "minimal-prov"
    assert status.checked_at is not None


async def test_probe_raises_returns_unhealthy_normalized(db, plugins) -> None:
    """Provider health_check raises → UNHEALTHY; target_id and checked_at populated."""

    @dataclass
    class BrokenProvider:
        id: str = "broken-prov"
        name: str = "broken"
        capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

        async def complete(self, request): ...

        def stream(self, request): ...

        async def list_models(self):
            return []

        async def estimate_tokens(self, text: str) -> int:
            return 1

        async def health_check(self) -> HealthStatus:
            raise ConnectionError("cannot reach server")

    provider = BrokenProvider(id="broken-prov")
    plugins.add_llm(provider)
    gw = LLMGatewayService(plugins, db, _config())

    status = await gw.health_check("broken-prov")

    assert status.level == HealthLevel.UNHEALTHY
    assert status.target_id == "broken-prov"
    assert status.checked_at is not None
    assert "ConnectionError" in status.message


async def test_unknown_provider_unconfigured_normalized(db, plugins) -> None:
    """No provider found → UNCONFIGURED; target_id and checked_at populated."""
    gw = LLMGatewayService(plugins, db, _config())

    status = await gw.health_check("nonexistent-provider")

    assert status.level == HealthLevel.UNCONFIGURED
    assert status.target_id == "nonexistent-provider"
    assert status.checked_at is not None
