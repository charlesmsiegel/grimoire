"""REST contract tests for per-campaign settings tabs (spec §15).

Covers GET/PUT for the four settings tabs that didn't persist before:
Routing, ImageGen, Storage, Advanced. Each test uses a real StateStore
backed by an in-memory(-ish) sqlite file so the JSON columns round-trip
through the same schema the app sees.

Routing is now backed by ``campaign.yaml`` via the LLM Gateway (which
also resolves these routes at request time); the other three tabs still
live in ``campaigns.config``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.llm_gateway.config import GatewayConfig
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.llm import RetryPolicy, TimeoutPolicy


class _NoOpPlugins:
    def get_llm_provider(self, _id):
        return None

    def get_embedding_provider(self, _id):
        return None

    def get_imagegen_backend(self, _id):
        return None

    def llm_providers(self):
        return []

    def embedding_providers(self):
        return []


@pytest.fixture
async def state_store(tmp_path) -> AsyncIterator[StateStore]:
    data = tmp_path / "data"
    data.mkdir()
    db = Database(tmp_path / "settings.sqlite", pool_size=1)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data)
    await s.upsert_campaign(campaign_id="camp-1", name="Camp One")
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
def settings_client(
    client: TestClient, container: ServiceContainer, state_store: StateStore
) -> TestClient:
    container.state_store = state_store
    # Routing endpoints now write through the gateway; wire a real one
    # pointed at the same data root so the YAML round-trips.
    if container.extras is None:
        container.extras = {}
    container.extras["llm_gateway"] = LLMGatewayService(
        _NoOpPlugins(),
        state_store.db,
        GatewayConfig(
            default_routes={},
            retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
            timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
        ),
        data_root=state_store.data_root,
    )
    return client


def test_routing_round_trip(settings_client) -> None:
    # Default empty (no campaign.yaml yet).
    resp = settings_client.get("/api/campaigns/camp-1/routing")
    assert resp.status_code == 200
    assert resp.json() == {"llm": {}, "embedding": {}, "imagegen": {}}

    # Save full routes (provider.model shape).
    resp = settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={
            "llm": {
                "main": "openai.gpt-4o",
                "drift_check": "anthropic.claude-haiku",
                "ignored": "",  # empty → clear (no-op since not present)
            },
            "embedding": {"embed:context": "openai.text-embedding-3"},
            "imagegen": {"portrait": "comfyui-local.sdxl"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["main"] == "openai.gpt-4o"
    assert body["llm"]["drift_check"] == "anthropic.claude-haiku"
    assert "ignored" not in body["llm"]  # empty strings dropped
    assert body["embedding"]["embed:context"] == "openai.text-embedding-3"
    assert body["imagegen"]["portrait"] == "comfyui-local.sdxl"

    # Re-read goes through the same campaign.yaml path.
    resp = settings_client.get("/api/campaigns/camp-1/routing")
    body = resp.json()
    assert body["llm"]["drift_check"] == "anthropic.claude-haiku"
    assert body["imagegen"]["portrait"] == "comfyui-local.sdxl"


def test_routing_clear_via_empty_string(settings_client) -> None:
    """An empty-string value clears a previously-set route."""
    settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={"llm": {"main": "openai.gpt-4o"}, "embedding": {}, "imagegen": {}},
    )
    settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={"llm": {"main": ""}, "embedding": {}, "imagegen": {}},
    )
    body = settings_client.get("/api/campaigns/camp-1/routing").json()
    assert "main" not in body["llm"]


def test_routing_rejects_invalid_route_string(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={"llm": {"main": "no_dot_here"}, "embedding": {}, "imagegen": {}},
    )
    assert resp.status_code == 422


def test_routing_unknown_campaign_404(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/missing/routing")
    assert resp.status_code == 404


def test_imagegen_settings_round_trip(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/imagegen")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] is None
    assert body["preset"] is None

    resp = settings_client.put(
        "/api/campaigns/camp-1/imagegen",
        json={
            "backend": "comfyui-local",
            "preset": "oil-painting",
            "sampler_defaults": {"steps": 25, "sampler": "DPM++ 2M Karras"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "comfyui-local"
    assert body["sampler_defaults"]["steps"] == 25

    resp = settings_client.get("/api/campaigns/camp-1/imagegen")
    assert resp.json()["preset"] == "oil-painting"


def test_imagegen_overwrite_replaces_prior_save(settings_client) -> None:
    """Successive PUTs to /imagegen overwrite each other cleanly."""
    settings_client.put(
        "/api/campaigns/camp-1/imagegen",
        json={
            "backend": "first",
            "preset": "watercolor",
            "sampler_defaults": {"steps": 30},
        },
    )
    resp = settings_client.put(
        "/api/campaigns/camp-1/imagegen",
        json={"backend": "second", "preset": "ink", "sampler_defaults": None},
    )
    assert resp.json()["backend"] == "second"
    assert resp.json()["preset"] == "ink"
    assert resp.json()["sampler_defaults"] is None


def test_storage_round_trip(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/storage")
    assert resp.json() == {"schedule": "off", "retention_days": 30}

    resp = settings_client.put(
        "/api/campaigns/camp-1/storage",
        json={"schedule": "daily", "retention_days": 90},
    )
    assert resp.status_code == 200
    assert resp.json() == {"schedule": "daily", "retention_days": 90}

    resp = settings_client.get("/api/campaigns/camp-1/storage")
    assert resp.json()["retention_days"] == 90


def test_advanced_round_trip(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/advanced")
    assert resp.json() == {"debug_log": False, "per_task_prompts": {}}

    resp = settings_client.put(
        "/api/campaigns/camp-1/advanced",
        json={
            "debug_log": True,
            "per_task_prompts": {"main": "Be terse.", "drift_check": "ignore weather drift"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["debug_log"] is True
    assert body["per_task_prompts"]["main"] == "Be terse."

    resp = settings_client.get("/api/campaigns/camp-1/advanced")
    assert resp.json()["per_task_prompts"]["drift_check"] == "ignore weather drift"


def test_narrator_response_mode_default(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/narrator")
    assert resp.status_code == 200
    assert resp.json() == {"response_mode": "all_at_once"}


def test_narrator_response_mode_round_trip(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/narrator",
        json={"response_mode": "per_character"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"response_mode": "per_character"}

    resp = settings_client.get("/api/campaigns/camp-1/narrator")
    assert resp.json()["response_mode"] == "per_character"


def test_narrator_response_mode_rejects_unknown(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/narrator",
        json={"response_mode": "round_robin"},
    )
    assert resp.status_code == 422


def test_narrator_response_mode_unknown_campaign_404(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/missing/narrator")
    assert resp.status_code == 404


def test_scene_narrator_override_round_trip(
    settings_client: TestClient,
    container: ServiceContainer,
    state_store: StateStore,
    tmp_path,
) -> None:
    """PATCH /campaigns/{id}/scenes/{scene_id} writes the override to the
    sidecar and the response reports the effective mode."""
    import asyncio

    from grimoire.scenes import (
        InMemoryEventBus,
        SceneInit,
        SceneManager,
        SceneManagerConfig,
    )

    scenes_root = tmp_path / "scenes_data"
    scenes_root.mkdir()
    scene_mgr = SceneManager(
        scenes_root,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=InMemoryEventBus(),
    )
    scene = asyncio.get_event_loop().run_until_complete(
        scene_mgr.start_scene(SceneInit(campaign_id="camp-1", title="Scene One")),
    )
    container.scenes = scene_mgr

    # Campaign default starts at all_at_once; effective inherits it.
    resp = settings_client.patch(
        f"/api/campaigns/camp-1/scenes/{scene.id}",
        json={"narrator_response_mode": "per_character"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["narrator_response_mode"]["scene_override"] == "per_character"
    assert body["narrator_response_mode"]["effective"] == "per_character"

    # Set the campaign default to per_character, then clear the scene override —
    # effective should fall back to the campaign default.
    settings_client.put(
        "/api/campaigns/camp-1/narrator",
        json={"response_mode": "per_character"},
    )
    resp = settings_client.patch(
        f"/api/campaigns/camp-1/scenes/{scene.id}",
        json={"clear_narrator_response_mode": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["narrator_response_mode"]["scene_override"] is None
    assert body["narrator_response_mode"]["effective"] == "per_character"


def test_scene_narrator_override_rejects_unknown_value(
    settings_client: TestClient,
    container: ServiceContainer,
    state_store: StateStore,
    tmp_path,
) -> None:
    import asyncio

    from grimoire.scenes import (
        InMemoryEventBus,
        SceneInit,
        SceneManager,
        SceneManagerConfig,
    )

    scenes_root = tmp_path / "scenes_data_2"
    scenes_root.mkdir()
    scene_mgr = SceneManager(
        scenes_root,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=InMemoryEventBus(),
    )
    scene = asyncio.get_event_loop().run_until_complete(
        scene_mgr.start_scene(SceneInit(campaign_id="camp-1", title="Scene Two")),
    )
    container.scenes = scene_mgr

    resp = settings_client.patch(
        f"/api/campaigns/camp-1/scenes/{scene.id}",
        json={"narrator_response_mode": "round-robin"},
    )
    assert resp.status_code == 422


def test_routing_storage_advanced_coexist(settings_client) -> None:
    """Routing now lives in campaign.yaml; storage/advanced stay in
    campaigns.config. Saving any one must not clobber the others."""
    settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={"llm": {"main": "p.m"}, "embedding": {}, "imagegen": {}},
    )
    settings_client.put(
        "/api/campaigns/camp-1/storage",
        json={"schedule": "weekly", "retention_days": 14},
    )
    settings_client.put(
        "/api/campaigns/camp-1/advanced",
        json={"debug_log": True, "per_task_prompts": {}},
    )

    # Re-read each — none of the writes should have nuked another tab.
    assert settings_client.get("/api/campaigns/camp-1/routing").json()["llm"]["main"] == "p.m"
    assert settings_client.get("/api/campaigns/camp-1/storage").json()["schedule"] == "weekly"
    assert settings_client.get("/api/campaigns/camp-1/advanced").json()["debug_log"] is True
