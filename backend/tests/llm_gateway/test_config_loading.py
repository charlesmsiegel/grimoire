"""Tests for §2: settings-based config loading and per-campaign model_routing.

Covers:
  §2.1  GatewaySettings pydantic model → GatewayConfig
  §2.2  Lazy-load model_routing from campaign.yaml on first gateway call
  §2.3  Persist set_route mutations back to campaign.yaml (atomic write)
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
)
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.llm_gateway.settings import GatewaySettings
from grimoire.types.llm import (
    CompletionRequest,
    Message,
    MessageRole,
    ModelInfo,
    RetryPolicy,
    TimeoutPolicy,
)
from tests.llm_gateway.conftest import FakeEmbeddingProvider, FakeLLMProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request() -> CompletionRequest:
    return CompletionRequest(
        model="ignored",
        messages=[Message(role=MessageRole.USER, content="hi")],
        max_tokens=16,
        temperature=0.0,
    )


def _minimal_config() -> GatewayConfig:
    return GatewayConfig(
        default_routes={"main": "fake.model"},
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        observability=ObservabilityConfig(log_all_requests=False),
    )


# ---------------------------------------------------------------------------
# §2.1  GatewaySettings  →  GatewayConfig
# ---------------------------------------------------------------------------


class TestGatewaySettings:
    def test_defaults_produce_empty_routes(self) -> None:
        s = GatewaySettings()
        cfg = s.to_gateway_config()
        assert isinstance(cfg, GatewayConfig)
        assert cfg.default_routes == {}
        assert cfg.fallback_routes == {}

    def test_routes_parsed_correctly(self) -> None:
        s = GatewaySettings(
            default_routes={"main": "anthropic.claude-opus-4-7"},
            fallback_routes={"main": "anthropic.claude-haiku-4-5"},
        )
        cfg = s.to_gateway_config()
        assert cfg.default_routes == {"main": "anthropic.claude-opus-4-7"}
        assert cfg.fallback_routes == {"main": "anthropic.claude-haiku-4-5"}

    def test_nested_retry_config(self) -> None:
        s = GatewaySettings(
            retry={"max_retries": 5, "initial_delay_ms": 200, "backoff_factor": 3.0}
        )
        cfg = s.to_gateway_config()
        assert cfg.retry.max_retries == 5
        assert cfg.retry.initial_delay_ms == 200
        assert cfg.retry.backoff_factor == 3.0

    def test_nested_timeout_config(self) -> None:
        s = GatewaySettings(timeout={"total_seconds": 60.0, "first_token_seconds": 10.0})
        cfg = s.to_gateway_config()
        assert cfg.timeout.total_seconds == 60.0
        assert cfg.timeout.first_token_seconds == 10.0

    def test_nested_embedding_cache_config(self) -> None:
        s = GatewaySettings(embedding_cache={"enabled": False, "max_entries": 500})
        cfg = s.to_gateway_config()
        assert cfg.embedding_cache.enabled is False
        assert cfg.embedding_cache.max_entries == 500

    def test_nested_observability_config(self) -> None:
        s = GatewaySettings(
            observability={
                "log_all_requests": False,
                "log_response_text": True,
                "response_excerpt_chars": 100,
            }
        )
        cfg = s.to_gateway_config()
        assert cfg.observability.log_all_requests is False
        assert cfg.observability.log_response_text is True
        assert cfg.observability.response_excerpt_chars == 100

    def test_full_round_trip(self) -> None:
        """All fields set → GatewayConfig has matching values."""
        s = GatewaySettings(
            default_routes={"main": "p.m", "drift_check": "p.h"},
            fallback_routes={"main": "p.fallback"},
            retry={"max_retries": 1, "initial_delay_ms": 100, "backoff_factor": 1.5},
            timeout={"total_seconds": 30.0, "first_token_seconds": 5.0},
            embedding_cache={"enabled": True, "max_entries": 1000},
            observability={
                "log_all_requests": True,
                "log_response_text": False,
                "response_excerpt_chars": 50,
            },
        )
        cfg = s.to_gateway_config()
        assert cfg.default_routes == {"main": "p.m", "drift_check": "p.h"}
        assert cfg.fallback_routes == {"main": "p.fallback"}
        assert cfg.retry == RetryPolicy(max_retries=1, initial_delay_ms=100, backoff_factor=1.5)
        assert cfg.timeout == TimeoutPolicy(total_seconds=30.0, first_token_seconds=5.0)
        assert cfg.embedding_cache == EmbeddingCacheConfig(enabled=True, max_entries=1000)
        assert cfg.observability == ObservabilityConfig(
            log_all_requests=True, log_response_text=False, response_excerpt_chars=50
        )


# ---------------------------------------------------------------------------
# §2.2  Lazy loading of model_routing from campaign.yaml
# ---------------------------------------------------------------------------


class TestLazyLoadCampaignRouting:
    async def test_loads_routes_from_campaign_yaml(self, db, plugins, tmp_path: Path) -> None:
        """First complete() for a campaign reads its campaign.yaml."""
        provider = FakeLLMProvider(id="anthropic", response_text="ok")
        plugins.add_llm(provider)

        # Write a campaign.yaml with model_routing
        campaign_dir = tmp_path / "campaigns" / "camp-abc"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "title: Test Campaign\nmodel_routing:\n  main: anthropic.fast-model\n",
            encoding="utf-8",
        )

        # Gateway starts with no routes configured for "main"
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)

        resp = await gw.complete("main", _request(), campaign_id="camp-abc")
        assert resp.text == "ok"
        # Model overridden to the campaign-level route
        assert provider.seen_requests[0].model == "fast-model"

    async def test_lazy_load_only_happens_once(self, db, plugins, tmp_path: Path) -> None:
        """Subsequent calls don't re-read the YAML."""
        provider = FakeLLMProvider(id="anthropic")
        plugins.add_llm(provider)

        campaign_dir = tmp_path / "campaigns" / "camp-once"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n  main: anthropic.m\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.complete("main", _request(), campaign_id="camp-once")
        await gw.complete("main", _request(), campaign_id="camp-once")

        # Both calls routed to "m"; provider called twice total
        assert provider.call_count == 2
        assert all(r.model == "m" for r in provider.seen_requests)

    async def test_missing_campaign_yaml_is_silent(self, db, plugins, tmp_path: Path) -> None:
        """No file → no error; falls back to default_routes."""
        provider = FakeLLMProvider(id="fake")
        plugins.add_llm(provider)

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        # _minimal_config has default route main → fake.model
        resp = await gw.complete("main", _request(), campaign_id="camp-absent")
        assert resp.text == "hello"
        assert provider.seen_requests[0].model == "model"

    async def test_missing_model_routing_block_is_silent(self, db, plugins, tmp_path: Path) -> None:
        """campaign.yaml present but has no model_routing: → no error."""
        provider = FakeLLMProvider(id="fake")
        plugins.add_llm(provider)

        campaign_dir = tmp_path / "campaigns" / "camp-norouting"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text("title: No Routing\n", encoding="utf-8")

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        resp = await gw.complete("main", _request(), campaign_id="camp-norouting")
        assert resp.text == "hello"

    async def test_bad_routing_entry_is_logged_and_skipped(
        self, db, plugins, tmp_path: Path, caplog
    ) -> None:
        """Malformed route string: warning logged, bad entry skipped, valid ones applied."""
        provider = FakeLLMProvider(id="anthropic")
        plugins.add_llm(provider)

        campaign_dir = tmp_path / "campaigns" / "camp-bad"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n  main: anthropic.good\n  drift_check: INVALID_NO_DOT\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        with caplog.at_level(logging.WARNING, logger="grimoire.llm_gateway.gateway"):
            resp = await gw.complete("main", _request(), campaign_id="camp-bad")

        assert resp.text == "hello"
        # good route was applied
        assert provider.seen_requests[0].model == "good"
        # warning was emitted for the bad entry
        assert any(
            "drift_check" in r.message or "INVALID_NO_DOT" in r.message for r in caplog.records
        )

    async def test_no_data_root_skips_campaign_loading(self, db, plugins) -> None:
        """data_root=None → lazy-load is skipped entirely, no AttributeError."""
        provider = FakeLLMProvider(id="fake")
        plugins.add_llm(provider)

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=None)
        resp = await gw.complete("main", _request(), campaign_id="camp-x")
        assert resp.text == "hello"

    async def test_lazy_load_applies_to_stream(self, db, plugins, tmp_path: Path) -> None:
        """stream() also triggers lazy loading."""
        provider = FakeLLMProvider(id="anthropic", stream_chunks=["x"])
        plugins.add_llm(provider)

        campaign_dir = tmp_path / "campaigns" / "camp-stream"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n  main: anthropic.stream-model\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        chunks = []
        async for chunk in gw.stream("main", _request(), campaign_id="camp-stream"):
            chunks.append(chunk)

        assert provider.seen_requests[0].model == "stream-model"


# ---------------------------------------------------------------------------
# §2.3  Persist set_route mutations back to campaign.yaml
# ---------------------------------------------------------------------------


class TestSetRoutePersistence:
    async def test_set_route_writes_to_campaign_yaml(self, db, plugins, tmp_path: Path) -> None:
        """set_route with campaign_id writes into campaign.yaml."""
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("main", "p.new-model", campaign_id="camp-write")

        yaml_path = tmp_path / "campaigns" / "camp-write" / "campaign.yaml"
        assert yaml_path.is_file()
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert data["model_routing"]["main"] == "p.new-model"

    async def test_set_route_preserves_other_yaml_keys(self, db, plugins, tmp_path: Path) -> None:
        """Existing top-level keys survive the write."""
        campaign_dir = tmp_path / "campaigns" / "camp-preserve"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "title: My Campaign\nsome_other_key: 42\nmodel_routing:\n  drift_check: p.haiku\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("main", "p.opus", campaign_id="camp-preserve")

        data = yaml.safe_load(
            (tmp_path / "campaigns" / "camp-preserve" / "campaign.yaml").read_text(encoding="utf-8")
        )
        assert data["title"] == "My Campaign"
        assert data["some_other_key"] == 42
        assert data["model_routing"]["drift_check"] == "p.haiku"
        assert data["model_routing"]["main"] == "p.opus"

    async def test_set_route_creates_file_if_missing(self, db, plugins, tmp_path: Path) -> None:
        """When neither directory nor file exist they are created."""
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("main", "p.m", campaign_id="new-camp")

        yaml_path = tmp_path / "campaigns" / "new-camp" / "campaign.yaml"
        assert yaml_path.is_file()
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert data["model_routing"]["main"] == "p.m"

    async def test_set_route_no_campaign_id_does_not_touch_files(
        self, db, plugins, tmp_path: Path
    ) -> None:
        """Global set_route (campaign_id=None) must not write any file."""
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("main", "p.global", campaign_id=None)

        campaigns_dir = tmp_path / "campaigns"
        # Either the directory doesn't exist or it has no YAML files
        assert not campaigns_dir.exists() or not any(campaigns_dir.rglob("*.yaml"))

    async def test_persisted_route_visible_after_restart(self, db, plugins, tmp_path: Path) -> None:
        """A new LLMGatewayService reading the same data_root sees the persisted route."""
        provider = FakeLLMProvider(id="p")
        plugins.add_llm(provider)

        gw1 = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw1.set_route("main", "p.persisted-model", campaign_id="camp-restart")

        # Simulate restart: new service instance
        gw2 = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        resp = await gw2.complete("main", _request(), campaign_id="camp-restart")
        assert resp.text == "hello"
        assert provider.seen_requests[-1].model == "persisted-model"

    async def test_set_route_write_is_atomic(self, db, plugins, tmp_path: Path) -> None:
        """Write goes through a .tmp file then rename; no partial state on disk."""
        # We can't easily intercept the rename, but we can verify the final file
        # is valid YAML (not partial) and that no .tmp file is left behind.
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("main", "p.atomic", campaign_id="camp-atomic")

        yaml_path = tmp_path / "campaigns" / "camp-atomic" / "campaign.yaml"
        tmp_path2 = yaml_path.with_suffix(".yaml.tmp")
        assert not tmp_path2.exists(), ".tmp file should have been renamed away"
        # YAML is valid
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert data["model_routing"]["main"] == "p.atomic"

    async def test_set_route_no_data_root_skips_write(self, db, plugins) -> None:
        """data_root=None → set_route with campaign_id does not crash."""
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=None)
        # Should not raise even though we can't write
        await gw.set_route("main", "p.m", campaign_id="camp-x")


# ---------------------------------------------------------------------------
# §7  embedding_routing / imagegen_routing on campaign.yaml
# ---------------------------------------------------------------------------


class TestEmbeddingRoutingBlock:
    async def test_embedding_routing_picked_up_by_resolver(
        self, db, plugins, tmp_path: Path
    ) -> None:
        """An ``embedding_routing`` block on campaign.yaml is loaded into the resolver."""
        embed_provider = FakeEmbeddingProvider(id="oai")
        plugins.add_embedding(embed_provider)

        campaign_dir = tmp_path / "campaigns" / "camp-embed"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "embedding_routing:\n  'embed:context': oai.text-embedding-3-small\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        # Trigger lazy load by calling the private hook directly.
        await gw._load_campaign_routing("camp-embed")

        route = gw._router.resolve("embed:context", campaign_id="camp-embed")
        assert route.provider_id == "oai"
        assert route.model == "text-embedding-3-small"

    async def test_embedding_routing_overrides_model_routing_same_task(
        self, db, plugins, tmp_path: Path
    ) -> None:
        """When both blocks list the same task, embedding_routing wins (loaded last)."""
        plugins.add_embedding(FakeEmbeddingProvider(id="oai"))

        campaign_dir = tmp_path / "campaigns" / "camp-both"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n"
            "  'embed:context': oai.old-model\n"
            "embedding_routing:\n"
            "  'embed:context': oai.new-model\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw._load_campaign_routing("camp-both")

        route = gw._router.resolve("embed:context", campaign_id="camp-both")
        assert route.model == "new-model"


class TestImagegenRoutingBlock:
    async def test_imagegen_routing_loaded_into_lookup(self, db, plugins, tmp_path: Path) -> None:
        """``imagegen_routing`` entries are stored for ImageGenService lookup."""
        campaign_dir = tmp_path / "campaigns" / "camp-img"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "imagegen_routing:\n  portrait: diffusers.sdxl-base\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.ensure_campaign_loaded("camp-img")

        route = gw.imagegen_route("portrait", "camp-img")
        assert route is not None
        assert route.provider_id == "diffusers"
        assert route.model == "sdxl-base"
        # Bulk accessor sees the same entry.
        assert gw.imagegen_routes_for("camp-img") == {"portrait": "diffusers.sdxl-base"}

    async def test_imagegen_routing_does_not_pollute_llm_resolver(
        self, db, plugins, tmp_path: Path
    ) -> None:
        """Imagegen routes must not be visible to ``list_routes`` / ``resolve``."""
        campaign_dir = tmp_path / "campaigns" / "camp-img-iso"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "imagegen_routing:\n  scene_open: diffusers.sdxl\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.ensure_campaign_loaded("camp-img-iso")

        # The LLM resolver should not know about the imagegen task.
        llm_routes = await gw.list_routes(campaign_id="camp-img-iso")
        assert "scene_open" not in llm_routes

    async def test_imagegen_routing_bad_entry_skipped(
        self, db, plugins, tmp_path: Path, caplog
    ) -> None:
        """Malformed imagegen route logs a warning and is skipped."""
        campaign_dir = tmp_path / "campaigns" / "camp-img-bad"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "imagegen_routing:\n  portrait: NO_DOT\n  scene_open: diffusers.ok\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        with caplog.at_level(logging.WARNING, logger="grimoire.llm_gateway.gateway"):
            await gw.ensure_campaign_loaded("camp-img-bad")

        msgs = [r.getMessage() for r in caplog.records]
        assert any("bad imagegen_routing" in m and "NO_DOT" in m for m in msgs), msgs
        # Bad entry skipped; valid entry preserved.
        assert gw.imagegen_route("portrait", "camp-img-bad") is None
        assert gw.imagegen_route("scene_open", "camp-img-bad") is not None


class TestSetRouteByKind:
    async def test_set_route_embedding_writes_embedding_block(
        self, db, plugins, tmp_path: Path
    ) -> None:
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route(
            "embed:context", "oai.text-embedding-3", campaign_id="c1", kind="embedding"
        )

        data = yaml.safe_load(
            (tmp_path / "campaigns" / "c1" / "campaign.yaml").read_text(encoding="utf-8")
        )
        assert data["embedding_routing"]["embed:context"] == "oai.text-embedding-3"
        assert "model_routing" not in data
        # Round-trip: a fresh service reads it back.
        gw2 = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw2.ensure_campaign_loaded("c1")
        route = gw2._router.resolve("embed:context", campaign_id="c1")
        assert route.raw == "oai.text-embedding-3"

    async def test_set_route_imagegen_writes_imagegen_block(
        self, db, plugins, tmp_path: Path
    ) -> None:
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("portrait", "diffusers.sdxl", campaign_id="c2", kind="imagegen")

        data = yaml.safe_load(
            (tmp_path / "campaigns" / "c2" / "campaign.yaml").read_text(encoding="utf-8")
        )
        assert data["imagegen_routing"]["portrait"] == "diffusers.sdxl"
        assert "model_routing" not in data
        # The imagegen route is exposed via the lookup helper.
        assert gw.imagegen_route("portrait", "c2") is not None
        # A fresh service reads it back from the YAML.
        gw2 = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw2.ensure_campaign_loaded("c2")
        route = gw2.imagegen_route("portrait", "c2")
        assert route is not None and route.raw == "diffusers.sdxl"

    async def test_set_route_default_kind_is_llm(self, db, plugins, tmp_path: Path) -> None:
        """Omitting ``kind`` writes to ``model_routing`` for backwards compat."""
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("main", "p.x", campaign_id="c3")
        data = yaml.safe_load(
            (tmp_path / "campaigns" / "c3" / "campaign.yaml").read_text(encoding="utf-8")
        )
        assert data["model_routing"]["main"] == "p.x"
        assert "embedding_routing" not in data
        assert "imagegen_routing" not in data

    async def test_clear_route_removes_entry_and_block_when_empty(
        self, db, plugins, tmp_path: Path
    ) -> None:
        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        await gw.set_route("portrait", "diffusers.a", campaign_id="c4", kind="imagegen")
        await gw.clear_route("portrait", campaign_id="c4", kind="imagegen")
        data = yaml.safe_load(
            (tmp_path / "campaigns" / "c4" / "campaign.yaml").read_text(encoding="utf-8")
        )
        assert "imagegen_routing" not in data
        assert gw.imagegen_route("portrait", "c4") is None


class TestRoutingCrossCheckUnknownModel:
    async def test_unknown_model_logs_warning_but_route_applied(
        self, db, plugins, tmp_path: Path, caplog
    ) -> None:
        """Provider is loaded; route names a model the provider doesn't advertise.

        A warning is emitted, but the route is still applied and resolvable.
        """
        provider = FakeLLMProvider(
            id="anthropic",
            models=[ModelInfo(id="claude-known", name="Claude Known")],
        )
        plugins.add_llm(provider)

        campaign_dir = tmp_path / "campaigns" / "camp-unknown"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n  main: anthropic.claude-unknown\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        with caplog.at_level(logging.WARNING, logger="grimoire.llm_gateway.gateway"):
            await gw._load_campaign_routing("camp-unknown")

        # Warning fired
        msgs = [r.getMessage() for r in caplog.records]
        assert any("claude-unknown" in m and "advertised list" in m for m in msgs), msgs
        # Route still applied
        route = gw._router.resolve("main", campaign_id="camp-unknown")
        assert route.provider_id == "anthropic"
        assert route.model == "claude-unknown"

    async def test_unknown_provider_does_not_warn(
        self, db, plugins, tmp_path: Path, caplog
    ) -> None:
        """If the referenced provider isn't loaded, the cross-check is silent."""
        # No providers registered.
        campaign_dir = tmp_path / "campaigns" / "camp-noprov"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n  main: nosuch.model-x\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        with caplog.at_level(logging.WARNING, logger="grimoire.llm_gateway.gateway"):
            await gw._load_campaign_routing("camp-noprov")

        msgs = [r.getMessage() for r in caplog.records]
        assert not any("advertised list" in m for m in msgs), msgs

    async def test_list_models_raises_is_silent(self, db, plugins, tmp_path: Path, caplog) -> None:
        """If provider.list_models() raises, the cross-check stays silent."""

        class RaisingProvider(FakeLLMProvider):
            async def list_models(self) -> list[ModelInfo]:
                raise RuntimeError("not configured")

        provider = RaisingProvider(id="raising")
        plugins.add_llm(provider)

        campaign_dir = tmp_path / "campaigns" / "camp-raise"
        campaign_dir.mkdir(parents=True)
        (campaign_dir / "campaign.yaml").write_text(
            "model_routing:\n  main: raising.any-model\n",
            encoding="utf-8",
        )

        gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
        with caplog.at_level(logging.WARNING, logger="grimoire.llm_gateway.gateway"):
            await gw._load_campaign_routing("camp-raise")

        msgs = [r.getMessage() for r in caplog.records]
        assert not any("advertised list" in m for m in msgs), msgs
        # Route still applied
        route = gw._router.resolve("main", campaign_id="camp-raise")
        assert route.model == "any-model"
