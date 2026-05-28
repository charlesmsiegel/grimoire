"""Per-campaign imagegen routing: a `task=...` argument resolves to a
specific backend/model via the LLM Gateway's `imagegen_route` lookup.

Implements the deferred §7 work flagged in
`docs/superpowers/specs/2026-05-18-plugins-COMPLETED.md`: previously
`imagegen_routing` was parsed but ignored; now ImageGenService routes
through it when a task name is passed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from grimoire.event_bus import EventBus
from grimoire.imagegen import BackendRegistry, ImageGenService, InMemoryDiffusersBackend
from grimoire.llm_gateway.config import GatewayConfig
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.imagegen import BackendCapabilities, GenerationRequest
from grimoire.types.llm import RetryPolicy, TimeoutPolicy


class _MarkerBackend:
    """Second backend used to verify per-task routing picks the right one."""

    capabilities = BackendCapabilities()
    deterministic_seed = True

    def __init__(self, backend_id: str = "marker") -> None:
        self.id = backend_id
        self.name = backend_id
        self.calls: list[GenerationRequest] = []

    async def generate(
        self, request: GenerationRequest, *, progress: Any = None, cancel_token: Any = None
    ) -> Any:
        self.calls.append(request)
        # Return a tiny synthetic result that's syntactically valid.
        from grimoire.types.imagegen import GenerationResult

        return GenerationResult(
            image_bytes=b"\x89PNG\r\n\x1a\n",
            thumbnail_bytes=b"\x89PNG\r\n\x1a\n",
            backend=self.id,
            model=request.model or "marker-default",
            seed=request.seed or 1,
        )

    async def health_check(self) -> Any:
        from grimoire.types.common import HealthLevel, HealthStatus

        return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


class _FakePluginsForRoutingWarning:
    """Minimal plugins shim so the gateway doesn't crash on imagegen warns."""

    def get_llm_provider(self, _id: str) -> Any:
        return None

    def get_embedding_provider(self, _id: str) -> Any:
        return None

    def get_imagegen_backend(self, _id: str) -> Any:
        return None

    def llm_providers(self) -> list[Any]:
        return []

    def embedding_providers(self) -> list[Any]:
        return []


def _gateway_config() -> GatewayConfig:
    return GatewayConfig(
        default_routes={},
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
    )


@pytest.fixture
async def env(tmp_path: Path):
    """Build a wired (gateway + imagegen) environment in one place."""
    data = tmp_path / "data"
    data.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "db.sqlite"), pool_size=2)
    await db.connect()
    store = StateStore(db, data)
    await store.upsert_campaign(campaign_id="camp-1", name="t")

    registry = BackendRegistry()
    registry.register(InMemoryDiffusersBackend())
    marker = _MarkerBackend(backend_id="marker")
    registry.register(marker)

    bus = EventBus()
    gw = LLMGatewayService(_FakePluginsForRoutingWarning(), db, _gateway_config(), data_root=data)
    svc = ImageGenService(
        store=store,
        registry=registry,
        default_backend_id="diffusers-memory",
        event_bus=bus,
        gateway=gw,
    )
    try:
        yield {
            "svc": svc,
            "gw": gw,
            "marker": marker,
            "data": data,
        }
    finally:
        await svc.aclose()
        await db.close()


async def _write_campaign_yaml(data_root: Path, campaign_id: str, body: str) -> None:
    campaign_dir = data_root / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign.yaml").write_text(body, encoding="utf-8")


async def test_generate_sync_routes_by_task(env) -> None:
    """A task name with an imagegen_routing entry picks the routed backend."""
    await _write_campaign_yaml(
        env["data"],
        "camp-1",
        "imagegen_routing:\n  portrait: marker.alternate-model\n",
    )
    result = await env["svc"].generate_sync(
        "camp-1",
        GenerationRequest(prompt="x", seed=42),
        task="portrait",
    )
    assert result.backend == "marker"
    assert result.model == "alternate-model"
    # The routed model overrode the request's blank model.
    assert env["marker"].calls[-1].model == "alternate-model"


async def test_generate_sync_without_task_uses_active_backend(env) -> None:
    """Omitting ``task`` keeps the previous behavior (active_backend)."""
    await _write_campaign_yaml(
        env["data"],
        "camp-1",
        "imagegen_routing:\n  portrait: marker.alternate-model\n",
    )
    result = await env["svc"].generate_sync(
        "camp-1",
        GenerationRequest(prompt="x", seed=42),
    )
    assert result.backend == "diffusers-memory"
    assert env["marker"].calls == []


async def test_generate_sync_task_without_route_falls_through(env) -> None:
    """A task with no matching imagegen_routing entry falls back to default."""
    # No campaign.yaml at all.
    result = await env["svc"].generate_sync(
        "camp-1",
        GenerationRequest(prompt="x", seed=42),
        task="portrait",
    )
    assert result.backend == "diffusers-memory"


async def test_queue_generation_routes_by_task(env) -> None:
    await _write_campaign_yaml(
        env["data"],
        "camp-1",
        "imagegen_routing:\n  scene_open: marker.scene-model\n",
    )
    # Seed a referenceable scene for FK.
    await env["svc"].store.db.execute(
        """
        INSERT INTO scenes (
          id, campaign_id, ordinal, slug, file_path,
          location_ref, in_game_start, in_game_end, pov_character_ref,
          present_character_refs, present_pc_refs, summary, running_summary,
          key_beats, tags, emotional_arc, post_count, threads_introduced,
          threads_paid_off, title, greeting_id, closed, closed_at_turn
        ) VALUES (?, ?, 1, ?, ?, NULL, NULL, NULL, NULL,
                  '[]', '[]', '', '', '[]', '[]', '', 0, '[]', '[]', '', NULL, 0, NULL)
        """,
        ("scene-1", "camp-1", "scene-1", "scenes/scene-1.md"),
    )

    job_id = await env["svc"].queue_generation(
        "camp-1",
        scene_id="scene-1",
        post_id=None,
        request=GenerationRequest(prompt="x", seed=99),
        task="scene_open",
    )
    jobs = await env["svc"].list_jobs("camp-1")
    target = next(j for j in jobs if j.id == job_id)
    assert target.backend == "marker"
    assert target.request.model == "scene-model"


async def test_unknown_backend_in_route_falls_back_with_warning(env, caplog) -> None:
    """A route naming an unregistered backend falls back to active_backend."""
    await _write_campaign_yaml(
        env["data"],
        "camp-1",
        "imagegen_routing:\n  portrait: nonexistent.some-model\n",
    )
    with caplog.at_level(logging.WARNING, logger="grimoire.imagegen.service"):
        result = await env["svc"].generate_sync(
            "camp-1",
            GenerationRequest(prompt="x", seed=42),
            task="portrait",
        )
    # Fell back to default backend.
    assert result.backend == "diffusers-memory"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("nonexistent" in m and "not registered" in m for m in msgs), msgs


async def test_no_gateway_means_task_is_silently_ignored(tmp_path) -> None:
    """ImageGenService constructed without a gateway treats ``task`` as a hint
    that nothing acts on. Existing callers that don't wire a gateway keep
    working."""
    data = tmp_path / "data"
    data.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "db.sqlite"), pool_size=1)
    await db.connect()
    store = StateStore(db, data)
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    registry = BackendRegistry()
    registry.register(InMemoryDiffusersBackend())
    svc = ImageGenService(
        store=store, registry=registry, default_backend_id="diffusers-memory", gateway=None
    )
    try:
        result = await svc.generate_sync(
            "camp-1",
            GenerationRequest(prompt="x", seed=1),
            task="portrait",
        )
        assert result.backend == "diffusers-memory"
    finally:
        await svc.aclose()
        await db.close()


async def test_set_gateway_late_bind_works(tmp_path) -> None:
    """`set_gateway` after construction wires routing the same as the kwarg."""
    data = tmp_path / "data"
    data.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "db.sqlite"), pool_size=1)
    await db.connect()
    store = StateStore(db, data)
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    registry = BackendRegistry()
    registry.register(InMemoryDiffusersBackend())
    marker = _MarkerBackend()
    registry.register(marker)

    svc = ImageGenService(store=store, registry=registry, default_backend_id="diffusers-memory")
    gw = LLMGatewayService(_FakePluginsForRoutingWarning(), db, _gateway_config(), data_root=data)
    svc.set_gateway(gw)

    await _write_campaign_yaml(data, "camp-1", "imagegen_routing:\n  portrait: marker.alt\n")
    try:
        result = await svc.generate_sync(
            "camp-1",
            GenerationRequest(prompt="x", seed=1),
            task="portrait",
        )
        assert result.backend == "marker"
        assert result.model == "alt"
    finally:
        await svc.aclose()
        await db.close()
