"""In-memory registry of loaded mechanics modules.

The service consults the registry to find the active module for a
campaign; the loader pushes modules into it after a successful load.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.types.mechanics import ModuleManifest
from grimoire.types.protocols import MechanicsModule


@dataclass
class RegisteredModule:
    manifest: ModuleManifest
    instance: MechanicsModule


class MechanicsRegistry:
    """Keyed by manifest id. ``register`` replaces an existing entry."""

    def __init__(self) -> None:
        self._modules: dict[str, RegisteredModule] = {}

    def register(self, manifest: ModuleManifest, instance: MechanicsModule) -> None:
        self._modules[manifest.id] = RegisteredModule(manifest=manifest, instance=instance)

    def unregister(self, module_id: str) -> None:
        self._modules.pop(module_id, None)

    def get(self, module_id: str) -> RegisteredModule | None:
        return self._modules.get(module_id)

    def list(self) -> list[RegisteredModule]:
        return list(self._modules.values())

    def ids(self) -> list[str]:
        return sorted(self._modules)

    def has(self, module_id: str) -> bool:
        return module_id in self._modules

    def clear(self) -> None:
        self._modules.clear()


__all__ = ["MechanicsRegistry", "RegisteredModule"]
