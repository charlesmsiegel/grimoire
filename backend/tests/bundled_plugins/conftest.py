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


def load_bundled(plugin_id: str, config: dict | None = None):
    """Discover and load a single bundled plugin by id via the real loader.

    Returns the `LoadResult` from `grimoire.plugins.loader.load_plugin`.
    """
    from grimoire.plugins.discovery import discover
    from grimoire.plugins.loader import load_plugin

    discovered, errors = discover([], bundled_roots=[BUNDLED_PLUGINS_ROOT])
    assert not errors, errors
    matched = [d for d in discovered if d.plugin_dir.name == plugin_id]
    assert matched, f"plugin {plugin_id!r} not discovered under {BUNDLED_PLUGINS_ROOT}"
    return load_plugin(matched[0], config)


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


@pytest.fixture
def anthropic_module() -> object:
    return _import_plugin("llm-anthropic")


@pytest.fixture
def llamacpp_module() -> object:
    return _import_plugin("llm-llamacpp")


@pytest.fixture
def export_markdown_module() -> object:
    return _import_plugin("export-markdown")


@pytest.fixture
def export_single_markdown_module() -> object:
    return _import_plugin("export-single-markdown")


@pytest.fixture
def export_json_module() -> object:
    return _import_plugin("export-json")


@pytest.fixture
def export_transcript_module() -> object:
    return _import_plugin("export-transcript")


@pytest.fixture
def export_html_module() -> object:
    return _import_plugin("export-html")
