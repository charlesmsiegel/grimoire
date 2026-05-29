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


@pytest.fixture(autouse=True)
def _isolate_api_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every API test's app to a per-test tmp data root.

    API route tests boot the full app via :func:`create_app`; the lifespan
    seeds default library assets and opens ``campaigns.sqlite`` under
    ``settings.data_root``. ``grimoire.main`` binds ``settings`` via
    ``from grimoire.config import settings`` at import time, so BOTH the config
    and main bindings must be patched — otherwise those writes land in the real
    ``~/.grimoire`` and leak across runs. Applies to every API test regardless
    of which container fixture it uses. The lifespan DB is pre-stamped so its
    ``apply_migrations`` is a no-op; monkeypatch restores everything afterwards.

    (Bundled-plugin loading in the lifespan is disabled separately by the
    ``_no_bundled_plugins`` autouse fixture in the root conftest.)
    """
    monkeypatch.setenv("GRIMOIRE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GRIMOIRE_DATABASE_PATH", str(tmp_path / "test.sqlite"))
    stamp_migrated_db(tmp_path / "test.sqlite")
    from grimoire import config as config_module
    from grimoire import main as main_module

    fresh = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", fresh)
    monkeypatch.setattr(main_module, "settings", fresh)


@pytest.fixture()
def container() -> ServiceContainer:
    """An empty container; data-root isolation is handled by the autouse
    :func:`_isolate_api_data_root` fixture. Tests attach the services they need."""
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
