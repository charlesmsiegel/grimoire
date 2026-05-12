"""Plugin lifecycle types: manifests, statuses, rescan reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .common import HealthStatus, Json, JsonSchema, PluginId


class PluginKind(StrEnum):
    LLM_PROVIDER = "llm_provider"
    EMBEDDING_PROVIDER = "embedding_provider"
    IMAGEGEN_BACKEND = "imagegen_backend"
    EXPORT_ADAPTER = "export_adapter"


class PluginLifecycle(StrEnum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    CONFIGURED = "configured"
    ACTIVE = "active"
    DEACTIVATED = "deactivated"
    FAILED = "failed"
    UNLOADED = "unloaded"


@dataclass
class PluginManifest:
    id: PluginId
    name: str
    version: str
    api_version: str
    implements: list[PluginKind] = field(default_factory=list)
    classes: dict[str, str] = field(default_factory=dict)
    config_schema: JsonSchema = field(default_factory=dict)
    requirements: list[str] = field(default_factory=list)
    author: str = ""
    homepage: str = ""
    description: str = ""
    isolated_venv: bool = False
    raw: Json = field(default_factory=dict)


@dataclass
class PluginStatus:
    id: PluginId
    lifecycle: PluginLifecycle
    health: HealthStatus | None = None
    error: str | None = None
    config_present: bool = False


@dataclass
class RescanReport:
    discovered: list[PluginId] = field(default_factory=list)
    loaded: list[PluginId] = field(default_factory=list)
    failed: list[tuple[PluginId, str]] = field(default_factory=list)
    removed: list[PluginId] = field(default_factory=list)
