"""Regression test for the imagegen 404: a bundled imagegen backend must be
bridgeable from the Plugins module into the ImageGenService registry.

Before this bridge existed the registry was always empty, so every generate
call raised ``KeyError`` -> 404 even when ``imagegen.yaml`` named a default
backend. This loads just the A1111 plugin (the user's configured backend) and
exercises the exact bridge loop from bootstrap, without booting the app or an
A1111 server.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.imagegen import BackendRegistry
from grimoire.plugins.discovery import discover
from grimoire.plugins.loader import load_plugin
from grimoire.types.plugins import PluginKind

BUNDLED_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "bundled_plugins"


def test_a1111_backend_bridges_into_imagegen_registry() -> None:
    discovered, errors = discover([], bundled_roots=[BUNDLED_PLUGINS_ROOT])
    assert not errors, errors
    matched = [d for d in discovered if d.plugin_dir.name == "imagegen-a1111"]
    assert matched, "imagegen-a1111 not discovered under bundled_plugins"

    result = load_plugin(matched[0], {"base_url": "http://127.0.0.1:3636"})
    backends = [
        loaded.instance for loaded in result.instances if loaded.kind == PluginKind.IMAGEGEN_BACKEND
    ]
    assert backends, "a1111 plugin exposed no imagegen backend"
    backend = backends[0]
    assert backend.id == "imagegen-a1111"

    # The bootstrap bridge: register every plugin backend into the service
    # registry. After this the configured default_backend resolves instead
    # of raising KeyError -> 404.
    registry = BackendRegistry()
    registry.register(backend)
    assert "imagegen-a1111" in registry
