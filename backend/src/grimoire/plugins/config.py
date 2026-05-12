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
            config_store=ConfigStoreConfig(root=data_root / "config" / "plugins"),
        )


__all__ = [
    "ConfigStoreConfig",
    "DiscoveryConfig",
    "HealthConfig",
    "IsolationConfig",
    "PluginsConfig",
]
