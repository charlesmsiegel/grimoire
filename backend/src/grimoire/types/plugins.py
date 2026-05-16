"""Plugin lifecycle types: manifests, statuses, rescan reports."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

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


class PluginManifest(BaseModel):
    id: PluginId
    name: str
    version: str
    api_version: str
    implements: list[PluginKind] = Field(default_factory=list)
    classes: dict[str, str] = Field(default_factory=dict)
    config_schema: JsonSchema = Field(default_factory=dict)
    requirements: list[str] = Field(default_factory=list)
    author: str = ""
    homepage: str = ""
    description: str = ""
    isolated_venv: bool = False
    # Plugin ids whose secret fields this plugin inherits when its own
    # are blank. Used to share an OpenRouter / OpenAI API key across
    # paired LLM and embedding plugins so the user only configures it
    # in one place.
    shares_secrets_with: list[PluginId] = Field(default_factory=list)
    raw: Json = Field(default_factory=dict)


class PluginStatus(BaseModel):
    id: PluginId
    lifecycle: PluginLifecycle
    health: HealthStatus | None = None
    error: str | None = None
    config_present: bool = False


class RescanReport(BaseModel):
    discovered: list[PluginId] = Field(default_factory=list)
    loaded: list[PluginId] = Field(default_factory=list)
    failed: list[tuple[PluginId, str]] = Field(default_factory=list)
    removed: list[PluginId] = Field(default_factory=list)
