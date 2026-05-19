"""HTTP route tests for the Context Inspector."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.context import ContextBuilderService, ContextInspector
from grimoire.main import create_app

from ..context.test_builder import (
    StubCharacters,
    StubContinuity,
    StubLibrary,
    StubScenes,
    StubWorld,
    _Card,
)
from ..context.test_inspector import StubPinStore


def _builder() -> ContextBuilderService:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/al": _Card(full="# Al")},
        active="library:worlds/wod/characters/al",
    )
    return ContextBuilderService(
        library=StubLibrary(),
        characters=chars,
        world=StubWorld(),
        scenes=StubScenes(),
        continuity=StubContinuity(),
        state_store=None,
        gateway=None,
    )


@pytest.fixture()
def inspector_container() -> ServiceContainer:
    container = ServiceContainer()
    store = StubPinStore()
    builder = _builder()
    inspector = ContextInspector(builder=builder, store=store)
    container.extras["context_inspector"] = inspector
    return container


@pytest.fixture()
def client(inspector_container: ServiceContainer) -> Iterator[TestClient]:
    # Skip the FastAPI lifespan (no DB needed): the inspector is hand-
    # wired into container.extras so routes can resolve it directly.
    app = create_app()
    app.state.container = inspector_container
    yield TestClient(app)


def _preview(client: TestClient, **overrides: Any) -> dict:
    body: dict[str, Any] = {"player_input": "hi", "session_id": "s_test"}
    body.update(overrides)
    resp = client.post("/api/campaigns/camp/context/preview", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_preview_round_trip(client: TestClient) -> None:
    out = _preview(client)
    assert out["handle"].startswith("ph_")
    assert "summary" in out
    assert out["summary"]["source_count"] >= 1


def test_get_preview_after_post(client: TestClient) -> None:
    handle = _preview(client)["handle"]
    resp = client.get(
        f"/api/campaigns/camp/context/preview/{handle}",
        params={"session_id": "s_test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "messages" in body
    assert "sources" in body


def test_get_unknown_handle_returns_404(client: TestClient) -> None:
    resp = client.get(
        "/api/campaigns/camp/context/preview/ph_doesnotexist",
        params={"session_id": "s_test"},
    )
    assert resp.status_code == 404


def test_explain_returns_per_source_reasons(client: TestClient) -> None:
    handle = _preview(client)["handle"]
    resp = client.get(
        f"/api/campaigns/camp/context/preview/{handle}/explain",
        params={"session_id": "s_test"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert any("pc_card" in r["inclusion_reasons"] for r in rows)


def test_explain_session_isolation_404(client: TestClient) -> None:
    handle = _preview(client, session_id="s_a")["handle"]
    resp = client.get(
        f"/api/campaigns/camp/context/preview/{handle}/explain",
        params={"session_id": "s_b"},
    )
    assert resp.status_code == 404


def test_pin_then_list_then_clear(client: TestClient) -> None:
    body = {
        "target": {"entity_kind": "character", "entity_id": "library:.../henry"},
        "kind": "pin",
    }
    resp = client.post("/api/campaigns/camp/context/pins", json=body)
    assert resp.status_code == 200, resp.text
    pin_id = resp.json()["pin_id"]

    listed = client.get("/api/campaigns/camp/context/pins").json()
    assert any(p["id"] == pin_id for p in listed)

    cleared = client.delete(f"/api/campaigns/camp/context/pins/{pin_id}")
    assert cleared.status_code == 200
    listed_after = client.get("/api/campaigns/camp/context/pins").json()
    assert all(p["id"] != pin_id for p in listed_after)


def test_pin_with_invalid_target_400(client: TestClient) -> None:
    body = {"target": {}, "kind": "pin"}
    resp = client.post("/api/campaigns/camp/context/pins", json=body)
    assert resp.status_code == 400


def test_diff_two_handles(client: TestClient) -> None:
    h_a = _preview(client, player_input="alpha")["handle"]
    h_b = _preview(client, player_input="beta")["handle"]
    resp = client.post(
        "/api/campaigns/camp/context/diff",
        json={"a": h_a, "b": h_b, "session_id": "s_test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "entities_added" in body
    assert "budget_shifts" in body


def test_inspector_missing_returns_503() -> None:
    app = create_app()
    app.state.container = ServiceContainer()  # no inspector configured
    c = TestClient(app)
    resp = c.post(
        "/api/campaigns/camp/context/preview",
        json={"player_input": "x", "session_id": "s"},
    )
    assert resp.status_code == 503
