"""REST routes for the transient-state subsystem."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig


@pytest.fixture
async def populated_container(container: ServiceContainer, tmp_path: Path) -> ServiceContainer:
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    db = Database(tmp_path / "transient.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    store = StateStore(db, data_root)
    await store.upsert_campaign(campaign_id="c_test", name="t")
    container.state_store = store
    container.transient_state = TransientStateService(store, config=TransientStateConfig())
    try:
        yield container
    finally:
        # TestClient closes its lifespan loop before fixture finalizers run,
        # so an aiosqlite close on a dead loop can spam "Event loop is closed".
        # Swallow — tmp_path teardown reclaims the file either way.
        with suppress(Exception):
            await db.close()


def test_get_field_404_when_absent(client: TestClient, populated_container):
    r = client.get("/api/campaigns/c_test/entities/character/char_x/transient/mood")
    assert r.status_code == 404


def test_patch_then_get_field_roundtrip(client: TestClient, populated_container):
    r = client.patch(
        "/api/campaigns/c_test/entities/character/char_x/transient/mood",
        json={"value": "guarded"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] == "guarded"
    assert body["provenance"] == "user:edit"

    r = client.get("/api/campaigns/c_test/entities/character/char_x/transient/mood")
    assert r.status_code == 200
    assert r.json()["value"] == "guarded"


def test_get_bundle_returns_all_fields(client: TestClient, populated_container):
    client.patch(
        "/api/campaigns/c_test/entities/character/char_x/transient/mood",
        json={"value": "guarded"},
    )
    client.patch(
        "/api/campaigns/c_test/entities/character/char_x/transient/intent",
        json={"value": "wait"},
    )
    r = client.get("/api/campaigns/c_test/entities/character/char_x/transient")
    assert r.status_code == 200
    body = r.json()
    assert set(body["fields"]) == {"mood", "intent"}
    assert body["fields"]["mood"]["value"] == "guarded"


def test_delete_field(client: TestClient, populated_container):
    client.patch(
        "/api/campaigns/c_test/entities/character/x/transient/mood",
        json={"value": "happy"},
    )
    r = client.delete("/api/campaigns/c_test/entities/character/x/transient/mood")
    assert r.status_code == 204
    r = client.get("/api/campaigns/c_test/entities/character/x/transient/mood")
    assert r.status_code == 404


def test_delete_bundle_clears_everything(client: TestClient, populated_container):
    client.patch(
        "/api/campaigns/c_test/entities/character/x/transient/mood",
        json={"value": "a"},
    )
    client.patch(
        "/api/campaigns/c_test/entities/character/x/transient/intent",
        json={"value": "b"},
    )
    r = client.delete("/api/campaigns/c_test/entities/character/x/transient")
    assert r.status_code == 204
    r = client.get("/api/campaigns/c_test/entities/character/x/transient")
    assert r.json()["fields"] == {}


def test_history_returns_newest_first(client: TestClient, populated_container):
    for value in ["a", "b", "c"]:
        client.patch(
            "/api/campaigns/c_test/entities/character/x/transient/mood",
            json={"value": value},
        )
    r = client.get("/api/campaigns/c_test/entities/character/x/transient/mood/history")
    assert r.status_code == 200
    body = r.json()
    assert [e["value"] for e in body["entries"]] == ["c", "b", "a"]


def test_unknown_entity_kind_returns_400(client: TestClient, populated_container):
    r = client.get("/api/campaigns/c_test/entities/spaceship/x/transient/mood")
    assert r.status_code == 400


def test_conflicts_endpoint_returns_list(client: TestClient, populated_container):
    # user write
    client.patch(
        "/api/campaigns/c_test/entities/character/x/transient/mood",
        json={"value": "happy"},
    )
    # extractor losing write — go through service directly to use extractor provenance
    client.patch(
        "/api/campaigns/c_test/entities/character/x/transient/mood",
        json={"value": "sad", "provenance": "extractor:auto", "confidence": 0.9},
    )
    r = client.get("/api/campaigns/c_test/transient/conflicts")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["current"]["value"] == "happy"
    assert body[0]["losing"]["value"] == "sad"


def test_service_auto_wired_in_lifespan(client: TestClient, container: ServiceContainer):
    """main.py lifespan auto-constructs transient_state when none was injected."""
    assert container.transient_state is not None
