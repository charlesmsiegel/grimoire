"""Configuration knobs for the Plugins module.

Mirrors the YAML structure from spec 15 §Configuration. Defaults are chosen
so a fresh install works without extra setup: discovery scans
`data/plugins/` at startup, health checks run on demand, and secrets prefer
the OS keyring when available (with a plaintext fallback that emits a
warning).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DiscoveryConfig:
    scan_on_startup: bool = True
    fail_on_invalid_manifest: bool = False


@dataclass(frozen=True)
class IsolationConfig:
    per_plugin_venv: bool = False
    venv_root: Path | None = None


@dataclass(frozen=True)
class HealthConfig:
    check_interval_minutes: int = 5
    timeout_seconds: int = 10


@dataclass(frozen=True)
class ConfigStoreConfig:
    root: Path
    encrypt_secrets_via_keyring: bool = True
    keyring_service: str = "grimoire-plugins"


@dataclass(frozen=True)
class PluginsConfig:
    root: Path
    config_store: ConfigStoreConfig
    bundled_root: Path | None = None
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    isolation: IsolationConfig = field(default_factory=IsolationConfig)
    health: HealthConfig = field(default_factory=HealthConfig)

    @classmethod
    def for_data_root(cls, data_root: Path) -> PluginsConfig:
        return cls(
            root=data_root / "plugins",
            bundled_root=_default_bundled_root(),
            config_store=ConfigStoreConfig(root=data_root / "config" / "plugins"),
        )


def _default_bundled_root() -> Path | None:
    """Locate the in-repo `bundled_plugins/` directory if it's present.

    Bundled plugins ship with the backend source tree at
    `backend/bundled_plugins/`. When the package is installed in-place
    (the normal dev layout) the directory is two levels above
    `grimoire/plugins/`. Returns `None` if the directory isn't there,
    leaving `PluginsConfig.bundled_root` unset.
    """
    here = Path(__file__).resolve()
    # backend/src/grimoire/plugins/config.py → backend / bundled_plugins
    candidate = here.parents[3] / "bundled_plugins"
    return candidate if candidate.is_dir() else None


__all__ = [
    "ConfigStoreConfig",
    "DiscoveryConfig",
    "HealthConfig",
    "IsolationConfig",
    "PluginsConfig",
]
