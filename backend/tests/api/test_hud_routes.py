"""Tests for the Scene HUD REST routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.hud.config import HudConfigService
from grimoire.hud.service import HudService


@pytest.fixture()
def hud_client(client: TestClient, container: ServiceContainer, tmp_path: Path) -> TestClient:
    container.hud_config = HudConfigService(tmp_path / "campaigns")
    svc = HudService(config_service=container.hud_config)

    async def fake_fetch(widget, _cid, _scene, _obs):
        return {"id": widget.id, "title": widget.title}

    from grimoire.hud.widgets import CORE_WIDGETS

    for w in CORE_WIDGETS:
        svc.register_fetcher(w.id, fake_fetch)

    container.hud = svc
    return client


def test_get_aggregate(hud_client: TestClient) -> None:
    r = hud_client.get("/api/campaigns/c_1/hud")
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == "c_1"
    assert len(body["widgets"]) >= 1
    ids = {w["id"] for w in body["widgets"]}
    assert "core.in-game-date" in ids
    # `core.temperature` defaults to visible_when=false
    assert "core.temperature" not in ids


def test_get_single_widget(hud_client: TestClient) -> None:
    r = hud_client.get("/api/campaigns/c_1/hud/widgets/core.in-game-date")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "core.in-game-date"
    assert body["status"] == "ok"


def test_get_available_widgets(hud_client: TestClient) -> None:
    r = hud_client.get("/api/campaigns/c_1/hud/widgets/available")
    assert r.status_code == 200
    ids = {w["id"] for w in r.json()}
    assert "core.in-game-date" in ids
    assert "core.present-cast" in ids


def test_config_defaults_when_missing(hud_client: TestClient) -> None:
    r = hud_client.get("/api/campaigns/c_1/hud/config")
    assert r.status_code == 200
    body = r.json()
    assert body["density"] == "comfortable"
    assert any(e["id"] == "core.in-game-date" for e in body["ordered_widgets"])


def test_config_put_round_trip(hud_client: TestClient) -> None:
    payload = {
        "density": "compact",
        "position": "bottom",
        "ordered_widgets": [{"id": "core.in-game-date", "visible": False, "options": {}}],
        "groups": [],
        "pinned_extras": {"char_alice": ["scar"]},
    }
    r = hud_client.put("/api/campaigns/c_1/hud/config", json=payload)
    assert r.status_code == 200
    again = hud_client.get("/api/campaigns/c_1/hud/config").json()
    assert again["density"] == "compact"
    assert again["pinned_extras"] == {"char_alice": ["scar"]}


def test_config_reset(hud_client: TestClient) -> None:
    hud_client.put(
        "/api/campaigns/c_1/hud/config",
        json={
            "density": "compact",
            "position": "bottom",
            "ordered_widgets": [],
            "groups": [],
            "pinned_extras": {},
        },
    )
    r = hud_client.post("/api/campaigns/c_1/hud/config/reset")
    assert r.status_code == 200
    assert r.json()["density"] == "comfortable"


def test_lifespan_auto_wires_hud(client: TestClient, container: ServiceContainer) -> None:
    # Without the ``hud_client`` fixture (which injects fakes), the lifespan
    # should have auto-wired a HudService — the route still answers, but with
    # ``no fetcher registered`` errors per widget since nobody registered any.
    r = client.get("/api/campaigns/c_1/hud")
    assert r.status_code == 200
    assert all(w["status"] == "error" for w in r.json()["widgets"])


@pytest.mark.parametrize("bad_id", [".hidden", ".."])
def test_unsafe_campaign_id_returns_4xx_on_config(hud_client: TestClient, bad_id: str) -> None:
    # Defense in depth: Starlette/HTTPX normalizes ``..`` and ``%2F`` at the
    # routing layer (so most traversal attempts 404 before reaching the
    # handler), and ``validate_path_component`` catches anything else as a
    # 400. Either way, no fs operation runs on an unsafe id.
    r = hud_client.get(f"/api/campaigns/{bad_id}/hud/config")
    assert r.status_code in (400, 404)
