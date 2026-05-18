"""Tests for the PluginsService facade."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.plugins.config import ConfigStoreConfig, PluginsConfig
from grimoire.plugins.config_store import InMemoryKeyring
from grimoire.plugins.service import PluginsService
from grimoire.types.common import HealthLevel
from grimoire.types.orchestrator import EventType
from grimoire.types.plugins import PluginKind, PluginLifecycle

from .conftest import write_plugin


def _service(
    plugins_root: Path,
    config_root: Path,
    *,
    event_bus: EventBus | None = None,
) -> PluginsService:
    cfg = PluginsConfig(
        root=plugins_root,
        config_store=ConfigStoreConfig(root=config_root),
    )
    return PluginsService(
        cfg, keyring_backend=InMemoryKeyring(), event_bus=event_bus
    )


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


async def test_get_config_inherits_secret_from_sibling(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha", manifest={"shares_secrets_with": ["beta"]})
    write_plugin(plugins_root, "beta", manifest={"shares_secrets_with": ["alpha"]})
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    await svc.set_config("beta", {"api_key": "sk-shared"})

    inherited = await svc.get_config("alpha")
    assert inherited.get("api_key") == "sk-shared"


async def test_get_config_does_not_overwrite_own_secret(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha", manifest={"shares_secrets_with": ["beta"]})
    write_plugin(plugins_root, "beta", manifest={"shares_secrets_with": ["alpha"]})
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    await svc.set_config("alpha", {"api_key": "sk-alpha"})
    await svc.set_config("beta", {"api_key": "sk-beta"})

    assert (await svc.get_config("alpha"))["api_key"] == "sk-alpha"
    assert (await svc.get_config("beta"))["api_key"] == "sk-beta"


async def test_plugin_instance_constructed_with_inherited_secret(
    plugins_root: Path, config_root: Path
) -> None:
    # Plugin records its `config` dict, so we can inspect what the loader
    # actually passed it after the inheritance pass.
    py = textwrap.dedent(
        """
        from grimoire.types.common import HealthLevel, HealthStatus


        class Provider:
            def __init__(self, config=None):
                self.config = config or {}
                self.id = "rec"
                self.name = "Rec"
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
                return 0

            async def health_check(self):
                return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
        """
    ).strip()
    write_plugin(
        plugins_root,
        "alpha",
        manifest={"shares_secrets_with": ["beta"]},
        plugin_py=py,
    )
    write_plugin(
        plugins_root,
        "beta",
        manifest={"shares_secrets_with": ["alpha"]},
        plugin_py=py,
    )
    svc = _service(plugins_root, config_root)
    # Seed beta's config before any rescan; alpha has no config yet.
    await svc.rescan()
    await svc.set_config("beta", {"api_key": "sk-shared"})
    # Rescan again so alpha's provider is rebuilt with the inherited key.
    await svc.rescan()

    alpha = svc.get_llm_provider("alpha")
    assert alpha is not None
    assert alpha.config.get("api_key") == "sk-shared"


async def test_shared_secret_unset_when_neither_side_configured(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha", manifest={"shares_secrets_with": ["beta"]})
    write_plugin(plugins_root, "beta", manifest={"shares_secrets_with": ["alpha"]})
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    cfg = await svc.get_config("alpha")
    assert "api_key" not in cfg or not cfg["api_key"]


async def test_unload_clears_record(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    svc = _service(plugins_root, config_root)
    await svc.rescan()

    await svc.unload("alpha")
    assert svc.get_llm_provider("alpha") is None
    status = await svc.get_status("alpha")
    assert status.lifecycle == PluginLifecycle.UNLOADED


# --------------------------------------------------------------------------- #
# §5 — persistent activation state
# --------------------------------------------------------------------------- #


async def test_deactivation_persists_across_rescan(
    plugins_root: Path, config_root: Path
) -> None:
    """A plugin the user deactivates stays deactivated when the app restarts.

    Simulates restart by building a fresh ``PluginsService`` against the
    same config root; the persisted ``.activations.yaml`` should pin the
    plugin to ``DEACTIVATED`` instead of coming back active.
    """
    write_plugin(plugins_root, "alpha")
    first = _service(plugins_root, config_root)
    await first.rescan()
    await first.set_config("alpha", {"api_key": "sk-xxx"})
    await first.deactivate("alpha")
    assert first.get_llm_provider("alpha") is None

    # New process: fresh PluginsService against the same data root.
    second = _service(plugins_root, config_root)
    await second.rescan()
    assert second.get_llm_provider("alpha") is None
    status = await second.get_status("alpha")
    assert status.lifecycle == PluginLifecycle.DEACTIVATED


async def test_activation_clears_persisted_deactivated_flag(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    first = _service(plugins_root, config_root)
    await first.rescan()
    await first.set_config("alpha", {"api_key": "sk-xxx"})
    await first.deactivate("alpha")
    await first.activate("alpha")

    second = _service(plugins_root, config_root)
    await second.rescan()
    status = await second.get_status("alpha")
    assert status.lifecycle == PluginLifecycle.ACTIVE


# --------------------------------------------------------------------------- #
# §4 — lifecycle events
# --------------------------------------------------------------------------- #


def _record_events(bus: EventBus) -> list[Event]:
    seen: list[Event] = []

    async def _handler(event: Event) -> None:
        seen.append(event)

    bus.subscribe("*", _handler)
    return seen


async def test_rescan_emits_plugin_loaded(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "alpha")
    bus = EventBus()
    events = _record_events(bus)
    svc = _service(plugins_root, config_root, event_bus=bus)
    await svc.rescan()

    types = [e.type for e in events]
    assert EventType.PLUGIN_LOADED.value in types
    loaded = next(e for e in events if e.type == EventType.PLUGIN_LOADED.value)
    assert loaded.payload["plugin_id"] == "alpha"
    assert loaded.payload["bundled"] is False


async def test_rescan_emits_plugin_failed(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "bad", manifest={"version": "not-semver"})
    bus = EventBus()
    events = _record_events(bus)
    svc = _service(plugins_root, config_root, event_bus=bus)
    await svc.rescan()

    failed = [e for e in events if e.type == EventType.PLUGIN_FAILED.value]
    assert failed
    assert failed[0].payload["plugin_id"] == "bad"
    assert failed[0].payload["errors"]


async def test_activate_deactivate_emit_events(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    bus = EventBus()
    events = _record_events(bus)
    svc = _service(plugins_root, config_root, event_bus=bus)
    await svc.rescan()
    events.clear()

    await svc.deactivate("alpha")
    await svc.activate("alpha")
    types = [e.type for e in events]
    assert EventType.PLUGIN_DEACTIVATED.value in types
    assert EventType.PLUGIN_ACTIVATED.value in types


async def test_unload_emits_plugin_unloaded(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    bus = EventBus()
    events = _record_events(bus)
    svc = _service(plugins_root, config_root, event_bus=bus)
    await svc.rescan()
    events.clear()

    await svc.unload("alpha")
    assert any(e.type == EventType.PLUGIN_UNLOADED.value for e in events)


async def test_health_check_emits_event_only_on_level_change(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha")
    bus = EventBus()
    events = _record_events(bus)
    svc = _service(plugins_root, config_root, event_bus=bus)
    await svc.rescan()
    events.clear()

    # First probe: previous level was None, so the transition counts.
    await svc.health_check("alpha")
    first = [e for e in events if e.type == EventType.PLUGIN_HEALTH_CHANGED.value]
    assert len(first) == 1
    assert first[0].payload["after"] == HealthLevel.HEALTHY.value

    # Second probe with no change should be silent.
    events.clear()
    await svc.health_check("alpha")
    second = [e for e in events if e.type == EventType.PLUGIN_HEALTH_CHANGED.value]
    assert second == []
