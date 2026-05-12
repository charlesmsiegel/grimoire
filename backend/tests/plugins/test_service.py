"""Tests for the PluginsService facade."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from grimoire.plugins.config import ConfigStoreConfig, PluginsConfig
from grimoire.plugins.config_store import InMemoryKeyring
from grimoire.plugins.service import PluginsService
from grimoire.types.common import HealthLevel
from grimoire.types.plugins import PluginKind, PluginLifecycle

from .conftest import write_plugin


def _service(plugins_root: Path, config_root: Path) -> PluginsService:
    cfg = PluginsConfig(
        root=plugins_root,
        config_store=ConfigStoreConfig(root=config_root),
    )
    return PluginsService(cfg, keyring_backend=InMemoryKeyring())


async def test_rescan_discovers_and_registers_plugins(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    write_plugin(plugins_root, "beta")
    svc = _service(plugins_root, config_root)

    report = await svc.rescan()
    assert sorted(report.discovered) == ["alpha", "beta"]
    assert sorted(report.loaded) == ["alpha", "beta"]
    assert report.failed == []

    providers = svc.llm_providers()
    assert len(providers) == 2
    assert svc.get_llm_provider("alpha") is not None


async def test_rescan_records_failures_and_continues(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "good")
    write_plugin(plugins_root, "bad", manifest={"version": "not-semver"})
    svc = _service(plugins_root, config_root)
    report = await svc.rescan()

    assert "good" in report.loaded
    failed_ids = [pid for pid, _ in report.failed]
    assert "bad" in failed_ids
    assert svc.get_llm_provider("good") is not None
    assert svc.get_llm_provider("bad") is None


async def test_rescan_removes_deleted_plugins(plugins_root: Path, config_root: Path) -> None:
    plugin_dir = write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()
    assert svc.get_llm_provider("alpha") is not None

    # Wipe directory and rescan
    import shutil

    shutil.rmtree(plugin_dir)

    report = await svc.rescan()
    assert "alpha" in report.removed
    assert svc.get_llm_provider("alpha") is None


async def test_list_installed_returns_manifests(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    manifests = await svc.list_installed()
    assert [m.id for m in manifests] == ["alpha"]
    assert manifests[0].implements == [PluginKind.LLM_PROVIDER]


async def test_get_status_reflects_lifecycle(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    status = await svc.get_status("alpha")
    # Has required `api_key` but no config file → loaded but not active.
    assert status.lifecycle == PluginLifecycle.LOADED

    await svc.set_config("alpha", {"api_key": "sk-xxx"})
    status = await svc.get_status("alpha")
    assert status.lifecycle == PluginLifecycle.ACTIVE
    assert status.config_present is True


async def test_get_status_failed_lookup(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "bad", manifest={"version": "not-semver"})
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    status = await svc.get_status("bad")
    assert status.lifecycle == PluginLifecycle.FAILED
    assert status.error


async def test_set_config_validates_against_schema(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    with pytest.raises(ValueError):
        # `api_key` is required.
        await svc.set_config("alpha", {})


async def test_get_config_returns_persisted_values(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()
    await svc.set_config("alpha", {"api_key": "sk-xxx"})

    cfg = await svc.get_config("alpha")
    assert cfg == {"api_key": "sk-xxx"}


async def test_deactivate_removes_from_registry(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()
    assert svc.get_llm_provider("alpha") is not None

    await svc.deactivate("alpha")
    assert svc.get_llm_provider("alpha") is None
    status = await svc.get_status("alpha")
    assert status.lifecycle == PluginLifecycle.DEACTIVATED

    await svc.activate("alpha")
    assert svc.get_llm_provider("alpha") is not None


async def test_health_check_returns_status_from_plugin(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    status = await svc.health_check("alpha")
    assert status.level == HealthLevel.HEALTHY


async def test_health_check_handles_exceptions(plugins_root: Path, config_root: Path) -> None:
    py = textwrap.dedent(
        """
        class Provider:
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
                raise RuntimeError("offline")
        """
    ).strip()
    write_plugin(plugins_root, "alpha", plugin_py=py)
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    status = await svc.health_check("alpha")
    assert status.level == HealthLevel.UNHEALTHY
    assert "offline" in (status.message or "")


async def test_health_check_unknown_plugin(plugins_root: Path, config_root: Path) -> None:
    svc = _service(plugins_root, config_root)
    status = await svc.health_check("nope")
    assert status.level == HealthLevel.UNCONFIGURED


async def test_health_check_all_covers_every_loaded_plugin(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    write_plugin(plugins_root, "beta")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    results = await svc.health_check_all()
    assert set(results) == {"alpha", "beta"}
    assert all(r.level == HealthLevel.HEALTHY for r in results.values())


async def test_validate_config_returns_result(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    ok = await svc.validate_config("alpha", {"api_key": "sk-xxx"})
    assert ok.ok
    bad = await svc.validate_config("alpha", {})
    assert not bad.ok


async def test_unload_clears_record(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    await svc.unload("alpha")
    assert svc.get_llm_provider("alpha") is None
    status = await svc.get_status("alpha")
    assert status.lifecycle == PluginLifecycle.UNLOADED
