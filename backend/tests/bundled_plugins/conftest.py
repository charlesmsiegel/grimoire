"""Shared helpers for tests that exercise the bundled plugins.

The bundled plugin tree lives at `backend/bundled_plugins/`. These
fixtures locate it via the discovery module so each test can load a
specific plugin without re-implementing the path math.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

BUNDLED_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "bundled_plugins"


def _import_plugin(plugin_id: str) -> object:
    """Import the `plugin.py` for the given bundled plugin id.

    Loaded under a synthetic module name so multiple plugins can be
    imported in the same test process without colliding on `Provider`.
    """
    entry = BUNDLED_PLUGINS_ROOT / plugin_id / "plugin.py"
    module_name = f"_grimoire_bundled_plugin_{plugin_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def assert_protocol_attrs(instance: object, names: Iterable[str]) -> None:
    missing = [name for name in names if not hasattr(instance, name)]
    assert not missing, f"missing protocol members: {missing}"


@pytest.fixture(scope="session")
def bundled_plugins_root() -> Path:
    assert BUNDLED_PLUGINS_ROOT.is_dir(), f"missing {BUNDLED_PLUGINS_ROOT}"
    return BUNDLED_PLUGINS_ROOT


@pytest.fixture
def st_module() -> object:
    return _import_plugin("embed-sentence-transformers")


@pytest.fixture
def openai_module() -> object:
    return _import_plugin("embed-openai")
