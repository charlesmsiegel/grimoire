"""sync_status appears in the /health response."""

from __future__ import annotations

from grimoire.api.container import ServiceContainer
from grimoire.api.health import HealthResponse


def test_sync_status_field_exists_on_container() -> None:
    c = ServiceContainer()
    assert c.sync_status == "syncing"


def test_sync_status_ready() -> None:
    c = ServiceContainer()
    c.sync_status = "ready"
    assert c.sync_status == "ready"


def test_health_response_model_includes_sync_status() -> None:
    resp = HealthResponse(
        status="ok",
        version="0.0.0",
        data_root="/tmp",
        sync_status="syncing",
    )
    assert resp.sync_status == "syncing"
