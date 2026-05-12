"""Plugins module — discovery, loading, and lifecycle for adapter plugins.

See `specs/15-plugins.md`. Public surface:

- `PluginsService`: default implementation of the `Plugins` protocol.
- `PluginsConfig`: configuration (paths, isolation, health, secrets).
- `PluginConfigStore`, `KeyringBackend`, `InMemoryKeyring`: per-plugin
  config persistence with optional OS-keyring secret storage.
- `PluginRegistry`: per-kind registries that consumers (LLM Gateway,
  ImageGen, Export) can consult directly.
- `discover`, `load_plugin`, `LoadResult`, `DiscoveredPlugin`: lower-level
  primitives exposed for testing and custom hosts.
"""

from grimoire.plugins.config import (
    ConfigStoreConfig,
    DiscoveryConfig,
    HealthConfig,
    IsolationConfig,
    PluginsConfig,
)
from grimoire.plugins.config_store import (
    InMemoryKeyring,
    KeyringBackend,
    PluginConfigStore,
    secret_property_names,
)
from grimoire.plugins.discovery import (
    DiscoveredPlugin,
    DiscoveryError,
    discover,
)
from grimoire.plugins.loader import (
    PROTOCOL_FOR_KIND,
    LoadedInstance,
    LoadResult,
    load_plugin,
)
from grimoire.plugins.registry import PluginRegistry
from grimoire.plugins.service import PluginsService

__all__ = [
    "PROTOCOL_FOR_KIND",
    "ConfigStoreConfig",
    "DiscoveredPlugin",
    "DiscoveryConfig",
    "DiscoveryError",
    "HealthConfig",
    "InMemoryKeyring",
    "IsolationConfig",
    "KeyringBackend",
    "LoadResult",
    "LoadedInstance",
    "PluginConfigStore",
    "PluginRegistry",
    "PluginsConfig",
    "PluginsService",
    "discover",
    "load_plugin",
    "secret_property_names",
]
