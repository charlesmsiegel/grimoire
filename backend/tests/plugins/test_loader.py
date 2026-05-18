"""Tests for plugin loading: import, instantiate, protocol-check."""

from __future__ import annotations

import textwrap
from pathlib import Path

from grimoire.plugins.discovery import discover
from grimoire.plugins.loader import load_plugin
from grimoire.types.plugins import PluginKind

from .conftest import write_plugin


def _discover_one(root: Path):
    discovered, _ = discover([root])
    assert len(discovered) == 1
    return discovered[0]


def test_load_plugin_succeeds_for_valid_plugin(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    result = load_plugin(_discover_one(plugins_root))
    assert result.ok
    assert result.manifest is not None
    assert result.manifest.id == "alpha"
    assert len(result.instances) == 1
    assert result.instances[0].kind == PluginKind.LLM_PROVIDER


def test_load_plugin_passes_config_to_constructor(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    result = load_plugin(_discover_one(plugins_root), config={"api_key": "sk-123"})
    assert result.ok
    instance = result.instances[0].instance
    assert instance.config == {"api_key": "sk-123"}


def test_load_plugin_rejects_id_mismatch(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha", manifest={"id": "different-id"})
    discovered = _discover_one(plugins_root)
    result = load_plugin(discovered)
    assert not result.ok
    assert any("id" in e for e in result.errors)


def test_load_plugin_reports_missing_plugin_py(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha", omit_plugin_py=True)
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    assert any("plugin.py" in e for e in result.errors)


def test_load_plugin_reports_missing_class(plugins_root: Path) -> None:
    py = textwrap.dedent(
        """
        class Other:
            pass
        """
    ).strip()
    write_plugin(plugins_root, "alpha", plugin_py=py)
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    assert any("Provider" in e for e in result.errors)


def test_load_plugin_reports_import_errors(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha", plugin_py="raise RuntimeError('boom')")
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    assert any("import" in e.lower() for e in result.errors)


def test_load_plugin_rejects_instance_missing_protocol_members(plugins_root: Path) -> None:
    py = textwrap.dedent(
        """
        class Provider:
            def __init__(self, config=None):
                # Intentionally omit `complete`, `stream`, etc.
                self.id = "x"
                self.name = "X"
                self.capabilities = object()
        """
    ).strip()
    write_plugin(plugins_root, "alpha", plugin_py=py)
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    assert any("protocol" in e.lower() for e in result.errors)


def test_load_plugin_reports_invalid_manifest(plugins_root: Path) -> None:
    write_plugin(plugins_root, "alpha", manifest={"version": "not-semver"})
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    assert any("manifest invalid" in e for e in result.errors)
    assert result.manifest is None


def test_load_plugin_rejects_non_async_complete(plugins_root: Path) -> None:
    """A plugin whose ``complete`` is a plain ``def`` must fail at load time.

    The gateway awaits it; a sync impl would explode on first request. The
    stricter check should catch it now and surface a precise message.
    """
    py = textwrap.dedent(
        """
        class Provider:
            def __init__(self, config=None):
                self.id = "x"
                self.name = "X"
                self.capabilities = object()
            def complete(self, request):  # sync — should be async
                return None
            def stream(self, request):
                async def _g():
                    if False:
                        yield None
                return _g()
            async def list_models(self): return []
            async def estimate_tokens(self, text): return 0
            async def health_check(self):
                from grimoire.types.common import HealthLevel, HealthStatus
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
        """
    ).strip()
    write_plugin(plugins_root, "alpha", plugin_py=py)
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    joined = " ".join(result.errors)
    assert "complete" in joined and "async" in joined


def test_load_plugin_rejects_renamed_parameter(plugins_root: Path) -> None:
    """If a plugin renames a protocol parameter (e.g. ``request`` → ``payload``)
    the load should fail with a message pointing at the missing parameter."""
    py = textwrap.dedent(
        """
        class Provider:
            def __init__(self, config=None):
                self.id = "x"
                self.name = "X"
                self.capabilities = object()
            async def complete(self, payload):  # wrong name
                return None
            def stream(self, request):
                async def _g():
                    if False:
                        yield None
                return _g()
            async def list_models(self): return []
            async def estimate_tokens(self, text): return 0
            async def health_check(self):
                from grimoire.types.common import HealthLevel, HealthStatus
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
        """
    ).strip()
    write_plugin(plugins_root, "alpha", plugin_py=py)
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    joined = " ".join(result.errors)
    assert "request" in joined


def test_load_plugin_projects_typed_capabilities(plugins_root: Path) -> None:
    """A manifest's ``capabilities`` block lands on ``PluginManifest.capabilities``
    as typed shapes so the gateway can pick a model without instantiating."""
    write_plugin(
        plugins_root,
        "alpha",
        manifest={
            "capabilities": {
                "llm_provider": {
                    "streaming": True,
                    "tools": True,
                    "vision": False,
                    "max_context": 200000,
                },
            },
        },
    )
    result = load_plugin(_discover_one(plugins_root))
    assert result.ok
    assert result.manifest is not None
    caps = result.manifest.capabilities.llm_provider
    assert caps is not None
    assert caps.streaming is True
    assert caps.tools is True
    assert caps.max_context == 200000


def test_load_plugin_rejects_capabilities_for_unimplemented_kind(
    plugins_root: Path,
) -> None:
    """A plugin declaring `capabilities.imagegen_backend` but only implementing
    `llm_provider` is almost certainly a typo — fail loudly."""
    write_plugin(
        plugins_root,
        "alpha",
        manifest={
            "capabilities": {
                "imagegen_backend": {"text_to_image": True},
            },
        },
    )
    result = load_plugin(_discover_one(plugins_root))
    assert not result.ok
    assert any("imagegen_backend" in e for e in result.errors)


def test_load_plugin_supports_multi_kind_plugins(plugins_root: Path) -> None:
    py = textwrap.dedent(
        """
        from grimoire.types.common import HealthLevel, HealthStatus


        class LLM:
            def __init__(self, config=None):
                self.id = "x"
                self.name = "X"
                self.capabilities = object()
            async def complete(self, request): return None
            def stream(self, request):
                async def _g():
                    if False:
                        yield None
                return _g()
            async def list_models(self): return []
            async def estimate_tokens(self, text): return 0
            async def health_check(self):
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)


        class Embed:
            def __init__(self, config=None):
                self.id = "x-embed"
                self.name = "X-Embed"
                self.model_id = "test"
                self.dimensions = 8
            async def embed(self, texts): return [[0.0] * 8 for _ in texts]
            async def list_models(self): return []
            async def health_check(self):
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
        """
    ).strip()
    write_plugin(
        plugins_root,
        "alpha",
        manifest={
            "implements": ["llm_provider", "embedding_provider"],
            "classes": {
                "llm_provider": "LLM",
                "embedding_provider": "Embed",
            },
        },
        plugin_py=py,
    )
    result = load_plugin(_discover_one(plugins_root))
    assert result.ok, result.errors
    kinds = {i.kind for i in result.instances}
    assert kinds == {PluginKind.LLM_PROVIDER, PluginKind.EMBEDDING_PROVIDER}
