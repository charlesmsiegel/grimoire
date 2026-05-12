# 15 — Plugins

## Purpose

Plugins are the *shallow-adapter* extension surface of Grimoire. They handle integrations with things outside the app: LLM providers, embedding providers, secondary ImageGen backends, and export formats. Each plugin kind has a small protocol — three to five methods — designed to be easy to write.

This is deliberately distinct from the Mechanics module, which is a first-class architectural concern with a much richer API and its own dedicated spec (`06-mechanics.md`). Mechanics is not a plugin. The plugin system here only covers adapter-shaped integrations.

Plugins are installed by dropping a directory into `data/plugins/<plugin-id>/`. At startup, the app scans for `manifest.yaml` files in each subdirectory and registers what it finds. Nothing is centralized; nothing requires a package manager.

## What is a plugin

A plugin is a directory under `data/plugins/` containing:

```
data/plugins/<plugin-id>/
├── manifest.yaml           # required
├── plugin.py               # required; implements one or more plugin protocols
├── requirements.txt        # optional; declares Python deps for this plugin
├── README.md               # optional; user-facing notes
└── (other files as needed by the implementation)
```

The app reads the manifest, validates it, dynamically imports `plugin.py`, instantiates the implementation class(es), and registers them under the appropriate plugin kind.

## Plugin kinds

Four kinds. A single plugin can implement multiple kinds if it makes sense (a vendor SDK that does both LLM and embeddings, for example).

| Kind | Module that uses it | What it does |
|---|---|---|
| `llm_provider` | LLM Gateway (`05`) | Adapt an LLM API: `complete(prompt)` → text |
| `embedding_provider` | LLM Gateway (`05`) | Adapt an embedding model: `embed(texts)` → vectors |
| `imagegen_backend` | ImageGen (`12`) | Adapt an image-generation backend (A1111, ComfyUI, DALL-E, etc.) |
| `export_adapter` | Export (`13`) | Render a campaign archive to an external format (EPUB, etc.) |

The integrated `diffusers` ImageGen backend is *not* a plugin — it's a core implementation. Plugins are the alternatives.

Bundled defaults (ship with the app under `data/plugins/`):
- `llm-anthropic`, `llm-llamacpp` (LLM providers)
- `embed-sentence-transformers`, `embed-openai` (embedding providers)
- `imagegen-a1111`, `imagegen-comfyui`, `imagegen-dalle` (ImageGen alternatives)
- `export-epub`, `export-markdown` (export adapters)

Users can add or remove any of these like any other plugin.

## Manifest format

```yaml
id: llm-anthropic                       # unique; matches directory name
name: "Anthropic LLM Provider"
version: "1.2.0"
api_version: "1"                        # which version of the plugin API this targets
author: "Grimoire core"
homepage: "https://..."
description: "Adapts the Anthropic Messages API for Grimoire's LLM Gateway."

# Which plugin kinds this plugin implements; the app routes calls accordingly.
implements:
  - llm_provider

# Plugin-kind-specific class names. The app imports plugin.py and instantiates these.
classes:
  llm_provider: AnthropicLLMProvider

# Plugin-specific config schema (JSON Schema). The Frontend renders a form for this.
config_schema:
  type: object
  properties:
    api_key: { type: string, secret: true }
    base_url: { type: string, default: "https://api.anthropic.com" }
    default_model: { type: string, default: "claude-opus-4-7" }
    max_retries: { type: integer, default: 3 }
  required: [api_key]

# Capabilities; LLM-providers declare which models they expose, capabilities supported, etc.
capabilities:
  llm_models:
    - id: claude-opus-4-7
      context_window: 200000
      supports_streaming: true
      supports_tools: true
    - id: claude-sonnet-4-6
      context_window: 200000
      supports_streaming: true

# Optional: dependencies for the implementation
requirements:
  - "anthropic>=0.40.0"

# Optional: notes shown in the install / activate UI
notes: |
  Requires an API key. Sign up at https://console.anthropic.com.
```

## Plugin protocols

The classes named in the manifest implement protocols defined in their respective module specs. The protocols are intentionally small.

### LLM provider

```python
class LLMProvider(Protocol):
    id: str
    name: str
    capabilities: ProviderCapabilities

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]: ...
    async def list_models(self) -> list[ModelInfo]: ...

    # Optional hooks
    async def estimate_tokens(self, text: str) -> int: ...
    async def health_check(self) -> HealthStatus: ...
```

See `05-llm-gateway.md` for the full data types.

### Embedding provider

```python
class EmbeddingProvider(Protocol):
    id: str
    name: str
    dimensions: int
    model_id: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def health_check(self) -> HealthStatus: ...
```

See `05-llm-gateway.md` for how the LLM Gateway uses embedding providers.

### ImageGen backend

```python
class ImageGenBackend(Protocol):
    id: str
    name: str
    capabilities: BackendCapabilities

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    async def list_models(self) -> list[ModelInfo]: ...
    async def list_samplers(self) -> list[str]: ...
    async def health_check(self) -> HealthStatus: ...
```

See `12-imagegen.md`. The integrated `diffusers` backend is the default and uses the same protocol internally, but it's not loaded via the plugin system — it's part of the core ImageGen module.

### Export adapter

```python
class ExportAdapter(Protocol):
    id: str
    name: str
    output_format: str                  # 'epub', 'pdf', 'docx', 'json', 'html', ...
    file_extension: str

    async def export(self, archive: CampaignArchive, options: dict, output_path: Path) -> ExportResult: ...
    def options_schema(self) -> dict: ...
```

See `13-export.md`.

## Discovery and loading

At startup, the Plugins module:

```
1. Walk data/plugins/ for subdirectories containing manifest.yaml.
2. For each:
   a. Parse manifest.yaml; validate against the plugin manifest schema.
   b. Check api_version is supported.
   c. (Optional) Pip-install requirements into a per-plugin virtualenv if requirements.txt exists
      and isolation is enabled; otherwise install into the app's main environment.
   d. Dynamically import plugin.py.
   e. For each kind in manifest.implements:
        i.  Look up the class named in manifest.classes[kind].
        ii. Instantiate it (passing any required config).
        iii. Validate it against the protocol for that kind.
        iv. Register it in the appropriate registry (LLMRegistry, EmbeddingRegistry, ImageGenRegistry, ExportRegistry).
3. Build a global plugin registry by id.
```

Errors during load are logged and surfaced in the UI ("llm-anthropic failed to load: import error in plugin.py line 14"). The app continues without the failed plugin.

A "rescan" action in the UI re-runs discovery without restarting. New plugins detected; removed plugins unregistered.

## Per-plugin configuration

Plugins declare a `config_schema` in their manifest. The Frontend renders a form from the schema; user fills in values; the app stores them in `data/config/plugins/<plugin-id>.yaml` (one file per plugin, kept separate from the manifest so the manifest stays read-only).

Secrets (API keys) are stored encrypted at rest if the OS keyring is available; otherwise written to the config file with a permissions warning.

The plugin's class receives its config at instantiation:

```python
class AnthropicLLMProvider:
    def __init__(self, config: dict):
        self.api_key = config["api_key"]
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key, base_url=config.get("base_url"))
        ...
```

## Plugin lifecycle

```
discover → load → configure → activate → use → deactivate → unload
```

- **discover**: directory scanned, manifest parsed
- **load**: plugin.py imported, classes instantiated
- **configure**: user fills in config (or imports defaults)
- **activate**: registered with consumer modules; available for use
- **use**: consumer modules call protocol methods
- **deactivate**: removed from registries; instance kept for re-activation
- **unload**: instance discarded (only happens on restart or rescan)

A plugin can be deactivated without uninstalling — useful for "I have llamacpp configured but I'm using anthropic today."

## Routing

Different tasks can use different plugin instances of the same kind. Per-campaign routing config:

```yaml
# campaign-local config
model_routing:
  main: anthropic.claude-opus-4-7        # use llm-anthropic plugin's claude-opus-4-7 model
  drift_check: anthropic.claude-haiku-4-5
  extractor: anthropic.claude-haiku-4-5
  npc_tick: anthropic.claude-haiku-4-5

embedding_routing:
  posts: sentence-transformers.all-mpnet-base-v2
  characters: sentence-transformers.all-mpnet-base-v2

imagegen_routing:
  default: diffusers                      # core, not a plugin
  fallback: a1111
```

The Gateway / ImageGen / Export module reads the routing config and dispatches to the right plugin.

## Health checks

Each plugin exposes a `health_check()` method. The app runs them periodically (default: every 5 min for active plugins) and reports status:

- `healthy` — green
- `degraded` — yellow (slow, rate-limited)
- `unhealthy` — red (errors, network failures, missing credentials)
- `unconfigured` — grey

The UI shows status next to each plugin in the Installed Plugins view.

## Drop-in install workflow

User wants to add a new LLM provider plugin (say, an OpenAI adapter the user wrote themselves):

```
1. User creates a directory: data/plugins/llm-openai/
2. Adds manifest.yaml, plugin.py, requirements.txt
3. Restarts the app or hits "Rescan plugins" in the UI
4. App discovers the new plugin, validates, loads it
5. User opens the plugin's config form and fills in the API key
6. Plugin is now selectable in model routing
```

For sharing a plugin: zip the directory; receiver unzips into their `data/plugins/`.

## Plugin manifest validation

The manifest schema is strict:

- `id` matches directory name
- `api_version` is a known version
- All `implements` are recognized plugin kinds
- All `classes` have corresponding entries for `implements`
- `config_schema` is valid JSON Schema
- `capabilities` follows the schema for the kinds declared

Failures during validation prevent registration with a clear error message.

## Interface (for the Plugins module itself)

```python
class Plugins(Protocol):
    # Discovery
    async def rescan(self) -> RescanReport: ...
    async def list_installed(self) -> list[PluginManifest]: ...
    async def get_manifest(self, plugin_id: str) -> Optional[PluginManifest]: ...
    async def get_status(self, plugin_id: str) -> PluginStatus: ...

    # Lifecycle
    async def load(self, plugin_id: str) -> None: ...
    async def unload(self, plugin_id: str) -> None: ...
    async def activate(self, plugin_id: str) -> None: ...
    async def deactivate(self, plugin_id: str) -> None: ...

    # Configuration
    async def get_config(self, plugin_id: str) -> dict: ...
    async def set_config(self, plugin_id: str, config: dict) -> None: ...
    async def validate_config(self, plugin_id: str, config: dict) -> ValidationResult: ...

    # Per-kind registries
    def llm_providers(self) -> list[LLMProvider]: ...
    def embedding_providers(self) -> list[EmbeddingProvider]: ...
    def imagegen_backends(self) -> list[ImageGenBackend]: ...
    def export_adapters(self) -> list[ExportAdapter]: ...

    def get_llm_provider(self, id: str) -> Optional[LLMProvider]: ...
    def get_embedding_provider(self, id: str) -> Optional[EmbeddingProvider]: ...
    def get_imagegen_backend(self, id: str) -> Optional[ImageGenBackend]: ...
    def get_export_adapter(self, id: str) -> Optional[ExportAdapter]: ...

    # Health
    async def health_check(self, plugin_id: str) -> HealthStatus: ...
    async def health_check_all(self) -> dict[str, HealthStatus]: ...
```

## What plugins cannot do (v1)

- **Hot-swap during a turn.** Plugin changes take effect on rescan / restart, not mid-conversation.
- **Inject UI.** Plugins are backend adapters; they don't ship Frontend code in v1. (Mechanics modules can ship `theme.css` and v2 JS bundles — see `06-mechanics.md`. Plugins don't.)
- **Modify other plugins.** No plugin-to-plugin communication.
- **Access campaign data directly.** Plugins receive only the data they need via their protocol methods; they don't read the State Store.

## Security and trust

Plugins are user-installed Python code; they run with the app's privileges. Grimoire assumes the user trusts plugins they install. There is no sandboxing in v1.

Recommendations:
- Bundled plugins are signed/checksummed (v2)
- Per-plugin virtualenv isolation for dependencies (configurable, on by default for non-bundled plugins)
- Future: WASM or subprocess sandbox for untrusted plugins (v2/v3)

## Configuration

```yaml
plugins:
  root: ./data/plugins
  bundled_root: ./bundled-plugins         # plugins shipped with the app

  discovery:
    scan_on_startup: true
    watch: false                          # don't auto-reload during development by default
    fail_on_invalid_manifest: false       # log and skip rather than crash

  isolation:
    per_plugin_venv: true                 # create a venv per plugin's requirements.txt
    venv_root: ./data/plugins/.venvs

  health:
    check_interval_minutes: 5
    timeout_seconds: 10

  config_store:
    root: ./data/config/plugins
    encrypt_secrets_via_keyring: true
```

## Open questions (deferred)

- **Plugin marketplace.** A central index for browsing community plugins. Out of v1 scope; design space allows for it (the manifest format is shareable).
- **Signing.** Cryptographic signing of bundled plugins to prevent tampering. v2.
- **Sandboxing.** WASM or subprocess isolation for untrusted plugins. v2/v3.
- **Auto-update.** Plugins ship with a manifest version; a future feature could check for updates. Not v1.
- **Cross-plugin dependencies.** A plugin requires another plugin to function. v2.
- **GUI for plugin authoring.** A development helper that scaffolds a manifest + plugin.py from templates. Nice-to-have.
- **Plugin testing harness.** Run a plugin's protocol methods in isolation against mock data. v2.
