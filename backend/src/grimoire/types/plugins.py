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


# --------------------------------------------------------------------------- #
# Manifest-level capability declarations
# --------------------------------------------------------------------------- #
#
# These mirror the per-kind capability classes used at runtime
# (``ProviderCapabilities`` in ``types.llm``, ``BackendCapabilities`` in
# ``types.imagegen``), but at the *manifest* layer — i.e. before the plugin
# has been imported. Consumers that need to pick a plugin without paying
# the import cost (the gateway when matching a campaign route, the UI when
# rendering an installed-plugins picker) read these instead.
#
# Each class lists only the fields that are meaningful to declare in the
# manifest. Plugins set them in `manifest.yaml` under a top-level
# ``capabilities`` key.


class LLMCapabilities(BaseModel):
    streaming: bool = False
    tools: bool = False
    vision: bool = False
    max_context: int = 0
    embeddings: bool = False


class EmbeddingCapabilities(BaseModel):
    dimensions: int = 0
    max_batch_size: int | None = None
    # Embedding model id this plugin is built around (the gateway uses
    # the live `EmbeddingProvider.model_id` at request time; this is the
    # manifest-level hint, useful for matching a route before load).
    model_id: str | None = None


class ImageGenCapabilities(BaseModel):
    text_to_image: bool = True
    image_to_image: bool = False
    inpainting: bool = False
    controlnet: bool = False
    lora: bool = False
    max_resolution: tuple[int, int] | None = None


class ExportCapabilities(BaseModel):
    extensions: list[str] = Field(default_factory=list)
    mime_type: str = ""


class PluginCapabilities(BaseModel):
    """Per-kind capability shapes, keyed by ``PluginKind`` value strings so a
    manifest can carry capabilities for each kind it implements.

    Example manifest fragment:

    .. code-block:: yaml

        capabilities:
          llm_provider:
            streaming: true
            max_context: 200000
          embedding_provider:
            dimensions: 1536
    """

    llm_provider: LLMCapabilities | None = None
    embedding_provider: EmbeddingCapabilities | None = None
    imagegen_backend: ImageGenCapabilities | None = None
    export_adapter: ExportCapabilities | None = None


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
    # Typed projection of the manifest's ``capabilities`` block. Empty when
    # the manifest omits the block. Consumers that need to pick a plugin
    # without instantiating it read this rather than `manifest.raw`.
    capabilities: PluginCapabilities = Field(default_factory=PluginCapabilities)
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
