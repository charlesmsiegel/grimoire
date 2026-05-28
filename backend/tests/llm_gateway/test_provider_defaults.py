"""`register_provider_defaults`: a wizard-configured plugin should
auto-register as the default for the well-known LLM / embedding tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from grimoire.llm_gateway.config import GatewayConfig
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db

from .conftest import FakeEmbeddingProvider, FakeLLMProvider, FakePlugins


@dataclass
class _Validation:
    ok: bool


@dataclass
class _FakeManifest:
    id: str
    implements: list[str] = field(default_factory=list)


class _ConfiguredPlugins(FakePlugins):
    """FakePlugins + the manifest / config / validation surface the gateway calls."""

    def __init__(
        self,
        configs: dict[str, dict[str, Any]],
        manifests: list[_FakeManifest] | None = None,
        valid_ids: set[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._configs = configs
        self._manifests = list(manifests or [])
        self._valid_ids = valid_ids if valid_ids is not None else set(configs.keys())

    async def list_installed(self) -> list[_FakeManifest]:
        return list(self._manifests)

    async def get_config(self, plugin_id: str) -> dict[str, Any]:
        return dict(self._configs.get(plugin_id, {}))

    async def validate_config(self, plugin_id: str, _config: dict) -> _Validation:
        return _Validation(ok=plugin_id in self._valid_ids)


@pytest.fixture
async def empty_db(tmp_path: Path):
    database = Database(stamp_migrated_db(tmp_path / "g.sqlite"), pool_size=2)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


def _llm_manifest(plugin_id: str = "llm-openrouter") -> _FakeManifest:
    return _FakeManifest(id=plugin_id, implements=["llm_provider"])


def _embed_manifest(plugin_id: str = "embed-openrouter") -> _FakeManifest:
    return _FakeManifest(id=plugin_id, implements=["embedding_provider"])


async def test_registers_main_and_summarize_from_configured_llm_plugin(
    empty_db: Database,
) -> None:
    plugins = _ConfiguredPlugins(
        configs={"llm-openrouter": {"active_model": "deepseek/deepseek-v4-pro"}},
        manifests=[_llm_manifest()],
        llm={"llm-openrouter": FakeLLMProvider(id="openrouter")},
    )
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    # Routes are keyed by plugin_id (the registry key), not the provider
    # instance's `.id` — see the comment on register_provider_defaults.
    assert routes["main"] == "llm-openrouter.deepseek/deepseek-v4-pro"
    assert routes["library.summarize"] == "llm-openrouter.deepseek/deepseek-v4-pro"


async def test_registers_embed_default_from_configured_embedding_plugin(
    empty_db: Database,
) -> None:
    plugins = _ConfiguredPlugins(
        configs={"embed-openrouter": {"active_model": "cohere/embed-english-v3.0"}},
        manifests=[_embed_manifest()],
        embed={"embed-openrouter": FakeEmbeddingProvider(id="embed-or")},
    )
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    assert routes["library.embed"] == "embed-openrouter.cohere/embed-english-v3.0"


async def test_does_not_overwrite_existing_defaults(empty_db: Database) -> None:
    """A route set via env / config / prior call must not be clobbered."""
    plugins = _ConfiguredPlugins(
        configs={"llm-openrouter": {"active_model": "deepseek/deepseek-v4-pro"}},
        manifests=[_llm_manifest()],
        llm={"llm-openrouter": FakeLLMProvider(id="openrouter")},
    )
    gateway = LLMGatewayService(
        plugins=plugins,
        db=empty_db,
        config=GatewayConfig(default_routes={"main": "anthropic.claude-opus-4-7"}),
    )

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    assert routes["main"] == "anthropic.claude-opus-4-7"
    # library.summarize had no prior default, so it picks up the wizard plugin.
    assert routes["library.summarize"] == "llm-openrouter.deepseek/deepseek-v4-pro"


async def test_skips_unconfigured_plugin(empty_db: Database) -> None:
    """A plugin whose saved config fails validation (e.g., missing api_key)
    must not be registered — that would silently route turns to a broken
    provider.
    """
    plugins = _ConfiguredPlugins(
        configs={"llm-openrouter": {"active_model": "deepseek/deepseek-v4-pro"}},
        manifests=[_llm_manifest()],
        valid_ids=set(),  # validation fails
        llm={"llm-openrouter": FakeLLMProvider(id="openrouter")},
    )
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    assert "main" not in routes
    assert "library.summarize" not in routes


async def test_skips_plugin_without_active_model(empty_db: Database) -> None:
    plugins = _ConfiguredPlugins(
        configs={"llm-openrouter": {"api_key": "sk-test"}},  # no active_model
        manifests=[_llm_manifest()],
        llm={"llm-openrouter": FakeLLMProvider(id="openrouter")},
    )
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    assert "main" not in routes


async def test_no_plugins_is_a_noop(empty_db: Database) -> None:
    plugins = _ConfiguredPlugins(configs={}, manifests=[])
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    assert routes == {}


async def test_idempotent(empty_db: Database) -> None:
    """Calling register_provider_defaults a second time must not change state."""
    plugins = _ConfiguredPlugins(
        configs={"llm-openrouter": {"active_model": "deepseek/deepseek-v4-pro"}},
        manifests=[_llm_manifest()],
        llm={"llm-openrouter": FakeLLMProvider(id="openrouter")},
    )
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()
    first = await gateway.list_routes()
    await gateway.register_provider_defaults()
    second = await gateway.list_routes()

    assert first == second


async def test_skips_unconfigured_plugin_keeps_iterating(empty_db: Database) -> None:
    """Two LLM manifests in order: the first fails validation, the second
    is good. We should fall through to the second, not stop at the first.
    """
    plugins = _ConfiguredPlugins(
        configs={
            "llm-broken": {"active_model": "broken/model"},
            "llm-openrouter": {"active_model": "deepseek/deepseek-v4-pro"},
        },
        manifests=[
            _FakeManifest(id="llm-broken", implements=["llm_provider"]),
            _llm_manifest(),
        ],
        valid_ids={"llm-openrouter"},  # only the second validates
    )
    gateway = LLMGatewayService(plugins=plugins, db=empty_db, config=GatewayConfig())

    await gateway.register_provider_defaults()

    routes = await gateway.list_routes()
    assert routes["main"] == "llm-openrouter.deepseek/deepseek-v4-pro"
