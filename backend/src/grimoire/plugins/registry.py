"""Per-kind registries for loaded plugin instances.

The Plugins module hands consumers (LLM Gateway, ImageGen, Export) the
right instance by plugin id. We keep one registry per kind so a plugin
that implements multiple kinds (a vendor SDK that does both LLM and
embeddings, for example) appears in every relevant list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grimoire.types.common import PluginId
from grimoire.types.plugins import PluginKind


@dataclass(slots=True)
class _KindRegistry:
    """A single per-kind registry. Insertion order is preserved."""

    by_id: dict[PluginId, Any] = field(default_factory=dict)

    def register(self, plugin_id: PluginId, instance: Any) -> None:
        self.by_id[plugin_id] = instance

    def unregister(self, plugin_id: PluginId) -> None:
        self.by_id.pop(plugin_id, None)

    def get(self, plugin_id: PluginId) -> Any | None:
        return self.by_id.get(plugin_id)

    def list(self) -> list[Any]:
        return list(self.by_id.values())


class PluginRegistry:
    """Owns the per-kind registries plus a flat plugin-id ↔ kind index."""

    def __init__(self) -> None:
        self._registries: dict[PluginKind, _KindRegistry] = {
            kind: _KindRegistry() for kind in PluginKind
        }
        self._kinds_by_id: dict[PluginId, set[PluginKind]] = {}

    def register(self, plugin_id: PluginId, kind: PluginKind, instance: Any) -> None:
        self._registries[kind].register(plugin_id, instance)
        self._kinds_by_id.setdefault(plugin_id, set()).add(kind)

    def unregister_all(self, plugin_id: PluginId) -> None:
        kinds = self._kinds_by_id.pop(plugin_id, set())
        for kind in kinds:
            self._registries[kind].unregister(plugin_id)

    def kinds_for(self, plugin_id: PluginId) -> set[PluginKind]:
        return set(self._kinds_by_id.get(plugin_id, ()))

    def has(self, plugin_id: PluginId) -> bool:
        return plugin_id in self._kinds_by_id

    def ids(self) -> list[PluginId]:
        return list(self._kinds_by_id)

    def get(self, plugin_id: PluginId, kind: PluginKind) -> Any | None:
        return self._registries[kind].get(plugin_id)

    def list(self, kind: PluginKind) -> list[Any]:
        return self._registries[kind].list()

    def reset(self) -> None:
        for reg in self._registries.values():
            reg.by_id.clear()
        self._kinds_by_id.clear()


__all__ = ["PluginRegistry"]
