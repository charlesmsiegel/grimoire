"""Tests for per-plugin venv isolation (§1).

We don't exercise the real ``pip install`` path here — the unit tests
shim :func:`ensure_plugin_venv` with a faked site-packages directory so
the test stays hermetic and fast.  Integration of the actual pip install
is covered by the live bundled-plugins test suite when running in an
environment that has the host deps installed.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from grimoire.plugins.config import (
    ConfigStoreConfig,
    IsolationConfig,
    PluginsConfig,
)
from grimoire.plugins.config_store import InMemoryKeyring
from grimoire.plugins.service import PluginsService
from grimoire.plugins.venv import cleanup_orphaned_venvs, prepended_sys_path

from .conftest import write_plugin


def _service_with_isolation(
    plugins_root: Path, config_root: Path, venv_root: Path
) -> PluginsService:
    cfg = PluginsConfig(
        root=plugins_root,
        config_store=ConfigStoreConfig(root=config_root),
        isolation=IsolationConfig(per_plugin_venv=True, venv_root=venv_root),
    )
    return PluginsService(cfg, keyring_backend=InMemoryKeyring())


def test_prepended_sys_path_restores_on_exit(tmp_path: Path) -> None:
    extra = tmp_path / "fake-site-packages"
    extra.mkdir()
    before = list(sys.path)
    with prepended_sys_path(extra):
        assert sys.path[0] == str(extra)
    assert sys.path == before


def test_prepended_sys_path_noop_when_none() -> None:
    before = list(sys.path)
    with prepended_sys_path(None):
        assert sys.path == before
    assert sys.path == before


def test_cleanup_orphaned_venvs_removes_unknown_dirs(tmp_path: Path) -> None:
    venv_root = tmp_path / "venvs"
    venv_root.mkdir()
    (venv_root / "alive").mkdir()
    (venv_root / "stale").mkdir()
    (venv_root / ".hidden").mkdir()

    cleanup_orphaned_venvs(venv_root, current_plugin_ids={"alive"})
    assert (venv_root / "alive").is_dir()
    assert not (venv_root / "stale").exists()
    # Hidden directories (e.g. tooling caches) are left alone.
    assert (venv_root / ".hidden").is_dir()


async def test_service_threads_venv_site_packages_through_loader(
    plugins_root: Path,
    config_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a plugin opts in via ``isolated_venv: true`` and the app
    enables ``per_plugin_venv``, the loader should see the venv's
    site-packages prepended onto ``sys.path`` for that plugin's import.

    The plugin's ``__init__`` captures ``sys.path`` so we can verify the
    prepend happened.
    """
    venv_root = tmp_path / "venvs"
    fake_site = tmp_path / "fake-site-packages"
    fake_site.mkdir()

    # Shim ensure_plugin_venv: pretend we built a venv and point at a
    # real-but-empty directory.
    import grimoire.plugins.service as service_module

    def _fake_ensure(plugin_id, requirements_path, venv_root, **kwargs):
        return fake_site

    monkeypatch.setattr(service_module, "ensure_plugin_venv", _fake_ensure)

    plugin_py = textwrap.dedent(
        """
        import sys
        from grimoire.types.common import HealthLevel, HealthStatus


        class Provider:
            def __init__(self, config=None):
                self.config = config or {}
                self.id = "x"
                self.name = "X"
                self.capabilities = object()
                self.import_sys_path = list(sys.path)

            async def complete(self, request):
                return None

            def stream(self, request):
                async def _g():
                    if False:
                        yield None
                return _g()

            async def list_models(self):
                return []

            async def estimate_tokens(self, text):
                return len(text)

            async def health_check(self):
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
        """
    ).strip()
    write_plugin(
        plugins_root,
        "alpha",
        manifest={"isolated_venv": True},
        plugin_py=plugin_py,
    )
    (plugins_root / "alpha" / "requirements.txt").write_text("# (none)\n")

    svc = _service_with_isolation(plugins_root, config_root, venv_root)
    await svc.rescan()
    provider = svc.get_llm_provider("alpha")
    assert provider is not None
    assert str(fake_site) in provider.import_sys_path
    # sys.path was restored after import — the prepend must not leak.
    assert str(fake_site) not in sys.path


async def test_service_skips_venv_when_isolation_off(
    plugins_root: Path,
    config_root: Path,
    tmp_path: Path,
) -> None:
    """A plugin can opt in via the manifest, but the venv is only built
    when the app-level :class:`IsolationConfig` enables the feature."""
    venv_root = tmp_path / "venvs"
    cfg = PluginsConfig(
        root=plugins_root,
        config_store=ConfigStoreConfig(root=config_root),
        # Default isolation: per_plugin_venv=False
    )
    plugin_py = textwrap.dedent(
        """
        from grimoire.types.common import HealthLevel, HealthStatus


        class Provider:
            def __init__(self, config=None):
                self.config = config or {}
                self.id = "x"
                self.name = "X"
                self.capabilities = object()

            async def complete(self, request):
                return None
            def stream(self, request):
                async def _g():
                    if False:
                        yield None
                return _g()
            async def list_models(self): return []
            async def estimate_tokens(self, text): return 0
            async def health_check(self):
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
        """
    ).strip()
    write_plugin(
        plugins_root,
        "alpha",
        manifest={"isolated_venv": True},
        plugin_py=plugin_py,
    )
    svc = PluginsService(cfg, keyring_backend=InMemoryKeyring())
    await svc.rescan()
    # Plugin loaded fine; no venv directory got created.
    assert svc.get_llm_provider("alpha") is not None
    assert not venv_root.exists()
