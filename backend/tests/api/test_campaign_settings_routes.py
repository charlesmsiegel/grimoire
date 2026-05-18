"""REST contract tests for per-campaign settings tabs (spec §15).

Covers GET/PUT for the four settings tabs that didn't persist before:
Routing, ImageGen, Storage, Advanced. Each test uses a real StateStore
backed by an in-memory(-ish) sqlite file so the JSON columns round-trip
through the same schema the app sees.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations


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
    return client


def test_routing_round_trip(settings_client) -> None:
    # Default empty
    resp = settings_client.get("/api/campaigns/camp-1/routing")
    assert resp.status_code == 200
    assert resp.json() == {"llm": {}, "embedding": {}}

    # Save
    resp = settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={
            "llm": {"main": "openai", "drift_check": "anthropic", "ignored": ""},
            "embedding": {"main": "openai-embed"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"]["main"] == "openai"
    assert "ignored" not in body["llm"]  # empty strings dropped

    # Re-read
    resp = settings_client.get("/api/campaigns/camp-1/routing")
    assert resp.json()["llm"]["drift_check"] == "anthropic"


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


def test_routing_storage_advanced_coexist(settings_client) -> None:
    """All three keys live in campaigns.config — saving one must not clobber the others."""
    settings_client.put(
        "/api/campaigns/camp-1/routing",
        json={"llm": {"main": "x"}, "embedding": {}},
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
    assert settings_client.get("/api/campaigns/camp-1/routing").json()["llm"]["main"] == "x"
    assert settings_client.get("/api/campaigns/camp-1/storage").json()["schedule"] == "weekly"
    assert settings_client.get("/api/campaigns/camp-1/advanced").json()["debug_log"] is True
