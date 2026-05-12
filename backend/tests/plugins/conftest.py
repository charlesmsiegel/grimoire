"""Shared fixtures for plugin discovery / loading tests.

The helpers here build a complete plugin directory (manifest + plugin.py)
on the fly so each test can express only the unusual bits it cares about.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_plugin(
    root: Path,
    plugin_id: str,
    *,
    manifest: dict[str, Any] | None = None,
    plugin_py: str | None = None,
    omit_plugin_py: bool = False,
) -> Path:
    """Materialise a plugin directory and return its path."""
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    base_manifest = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "api_version": "1",
        "implements": ["llm_provider"],
        "classes": {"llm_provider": "Provider"},
        "config_schema": {
            "type": "object",
            "properties": {"api_key": {"type": "string", "secret": True}},
            "required": ["api_key"],
        },
    }
    full_manifest = {**base_manifest, **(manifest or {})}
    (plugin_dir / "manifest.yaml").write_text(
        yaml.safe_dump(full_manifest, sort_keys=False), encoding="utf-8"
    )

    if not omit_plugin_py:
        body = plugin_py or DEFAULT_PROVIDER_PY
        (plugin_dir / "plugin.py").write_text(body, encoding="utf-8")
    return plugin_dir


DEFAULT_PROVIDER_PY = textwrap.dedent(
    """
    from grimoire.types.common import HealthLevel, HealthStatus


    class Provider:
        def __init__(self, config=None):
            self.config = config or {}
            self.id = "test"
            self.name = "Test"
            self.capabilities = object()

        async def complete(self, request):
            return None

        def stream(self, request):
            async def _gen():
                if False:
                    yield None
            return _gen()

        async def list_models(self):
            return []

        async def estimate_tokens(self, text):
            return len(text)

        async def health_check(self):
            return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
    """
).strip()


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    return root


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    root = tmp_path / "config" / "plugins"
    root.mkdir(parents=True)
    return root


__all__ = ["DEFAULT_PROVIDER_PY", "write_plugin"]
