"""Helpers for API tests: a TestClient bound to a custom :class:`ServiceContainer`.

The fixtures here let each test inject only the services it cares about. The
real database is still constructed in the lifespan (so health checks pass);
each test attaches the fakes it needs to ``app.state.container`` before issuing
requests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer
from grimoire.main import create_app
from grimoire.testing.db_template import stamp_migrated_db


@pytest.fixture()
def container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServiceContainer:
    """An empty container with a tmp data root.

    Tests populate the services they need before issuing requests.
    """
    monkeypatch.setenv("GRIMOIRE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GRIMOIRE_DATABASE_PATH", str(tmp_path / "test.sqlite"))
    # Pre-stamp the fully-migrated schema so the lifespan's apply_migrations
    # is a no-op instead of replaying every migration on each API test.
    stamp_migrated_db(tmp_path / "test.sqlite")
    # (Bundled-plugin loading in the lifespan is disabled globally by the
    # _no_bundled_plugins autouse fixture in the root conftest.)
    # Reload settings so the env vars take effect.
    from grimoire import config as config_module

    config_module.settings = config_module.Settings()
    return ServiceContainer()


@pytest.fixture()
def client(container: ServiceContainer) -> Iterator[TestClient]:
    app = create_app()
    app.state.container = container
    with TestClient(app) as test_client:
        yield test_client


class _FakeAttr:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


__all__ = ["_FakeAttr"]
