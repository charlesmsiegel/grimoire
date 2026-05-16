# Plugins — Design (Shipped)

> Captures the Plugins module as actually built. The matching "remaining" spec at `2026-05-16-plugins-remaining-design.md` covers everything from the original `specs/15-plugins.md` that did **not** land in this work.

**Commit:** `495bd3f` — "Implement plugin discovery and manifest validation (task 7)" (followed by `d78434e`, `9cf6367`, `5f6651b`, `7706dc3`, `eff4e6f`, `7067a73`, `38afe76`, `8d80e72`)
**Module:** `backend/src/grimoire/plugins/`
**Bundled plugins:** `backend/bundled_plugins/`
**Tests:** `backend/tests/plugins/`

## Purpose

Plugins are the *shallow-adapter* extension surface of Grimoire: LLM providers, embedding providers, ImageGen backends, and export adapters. A plugin is a directory under `data/plugins/` (or `backend/bundled_plugins/` for shipped defaults) containing a `manifest.yaml` and a `plugin.py`. The Plugins module discovers them at startup, validates each manifest, dynamically imports the entry module, instantiates the declared classes, and exposes them to the rest of the app through per-kind registries.

Mechanics modules are deliberately *not* plugins — they have their own loader (`grimoire.validation.manifests.validate_mechanics_manifest`) and a much richer protocol. The ImageGen `diffusers` backend is also a regular plugin (`backend/bundled_plugins/imagegen-diffusers/`), not a core implementation — that's the one departure from the original spec wording.

## Module surface

```
backend/src/grimoire/plugins/
├── __init__.py        # public re-exports
├── config.py          # PluginsConfig + sub-configs
├── config_store.py    # PluginConfigStore + KeyringBackend / InMemoryKeyring
├── discovery.py       # walks plugin roots, parses manifest.yaml
├── loader.py          # dynamic import + protocol validation
├── registry.py        # PluginRegistry (per-kind registries)
└── service.py         # PluginsService — the Plugins protocol implementation
```

Wiring lives in `backend/src/grimoire/main.py:33` (import) and the `_create_app` factory at `main.py:129-134`: the service is constructed via `PluginsConfig.for_data_root(data_root)` and `rescan()` is awaited at startup. Consumers — `LLMGatewayService` (`main.py:165`) and `ExportService` (`main.py:211`) — receive either the `PluginsService` itself or one of its per-kind lists.

## Configuration (`PluginsConfig`)

`plugins/config.py` provides frozen dataclasses mirroring `specs/15-plugins.md` §Configuration:

```python
PluginsConfig(
    root=data_root / "plugins",                       # user plugins
    bundled_root=<repo>/backend/bundled_plugins,      # auto-detected; may be None
    config_store=ConfigStoreConfig(
        root=data_root / "config" / "plugins",
        encrypt_secrets_via_keyring=True,
        keyring_service="grimoire-plugins",
    ),
    discovery=DiscoveryConfig(scan_on_startup=True,
                              fail_on_invalid_manifest=False),
    isolation=IsolationConfig(per_plugin_venv=False, venv_root=None),
    health=HealthConfig(check_interval_minutes=5, timeout_seconds=10),
)
```

`PluginsConfig.for_data_root(...)` is the convenience constructor used at startup. `_default_bundled_root` (`config.py:59`) resolves `backend/bundled_plugins/` relative to the installed package; it returns `None` when the directory is missing so dev and production paths agree.

`isolation.per_plugin_venv` is recognised by the manifest schema (`isolated_venv` flag) but **not enforced** — see remaining doc §1.

## Manifest format (as validated)

`backend/src/grimoire/validation/manifests.py` owns the JSON Schema. Required: `id`, `name`, `version` (strict semver), `api_version` (currently only `"1"`), `implements` (non-empty subset of `llm_provider | embedding_provider | imagegen_backend | export_adapter`), `classes` (one class name per implemented kind). Optional: `author`, `homepage`, `description`, `config_schema` (must itself be valid JSON Schema), `capabilities`, `requirements`, `shares_secrets_with`, `notes`. The `additionalProperties: True` makes the schema forward-compatible.

Two cross-field checks live in `_cross_check_plugin` (`manifests.py:121`): every entry in `implements` must have a matching `classes` entry, and `classes` cannot declare a kind that is not implemented.

The loader applies one more rule the JSON Schema cannot express: `manifest.id` must match the directory name (`loader.py:99`).

`PluginManifest` (`backend/src/grimoire/types/plugins.py`) is the typed projection of the validated dict. It also keeps the original `raw` mapping so consumers can read schema extensions the typed model does not surface (e.g. `capabilities`).

## Discovery (`discovery.py`)

`discover(roots, bundled_roots=None) -> (list[DiscoveredPlugin], list[DiscoveryError])` walks each root one level deep, looking for `manifest.yaml`. Each candidate becomes a `DiscoveredPlugin(plugin_dir, manifest_path, entry_path, raw_manifest, source_root, bundled)`. Discovery is read-only — no validation, no import.

Key rules:
- Hidden directories (`.venvs`, etc.) are skipped (`discovery.py:75`).
- Missing roots are silently ignored (`discovery.py:72`).
- Duplicate `id` across roots is rejected with a `DiscoveryError`; the first occurrence wins.
- User roots are scanned **before** bundled roots so a copy of `llm-anthropic` under `data/plugins/` overrides the bundled version (commit `c5c73cb`, "Fix: scan user plugin roots before bundled so user copies win").

`DiscoveryError(plugin_dir, message)` records YAML parse failures, non-mapping top levels, and duplicate-id collisions; the service exposes them via `discovery_errors()` for the UI.

## Loading (`loader.py`)

`load_plugin(discovered, config=None) -> LoadResult` does the full pipeline for one plugin:

1. Validate the raw manifest against `PLUGIN_MANIFEST_SCHEMA` + cross-checks. Any failure returns early with `manifest=None`.
2. Check `manifest.id == directory.name`; surface as a non-fatal error if not.
3. Build the typed `PluginManifest` from the raw dict (`_build_manifest`).
4. Ensure `plugin.py` exists; if not, fail with the missing-path message.
5. Import `plugin.py` under a synthetic module name (`grimoire_plugins._loaded.<id_with_underscores>`) so two plugins can both define `Provider` without clashing. Re-imports overwrite the previous entry in `sys.modules` so rescan picks up edits. A failed import removes the partial module entry rather than leaving it in `sys.modules`.
6. For each kind in `manifest.implements`, look up the named class on the imported module, instantiate it (`_instantiate`), and check it satisfies the protocol for that kind (`_satisfies_protocol`).

`_instantiate` (`loader.py:209`) inspects the constructor signature: a zero-arg constructor is called bare; a constructor whose first parameter is named `config | settings | options` receives the config dict as a keyword; otherwise the config dict is passed positionally. This means a plugin can write `def __init__(self, config: dict | None = None)` *or* `def __init__(self)` and both work.

`PROTOCOL_FOR_KIND` (`loader.py:40`) maps each plugin kind to the protocol the registered instance must satisfy:

| Kind | Protocol |
|---|---|
| `llm_provider` | `grimoire.types.protocols.LLMProvider` |
| `embedding_provider` | `EmbeddingProvider` |
| `imagegen_backend` | `ImageGenBackend` |
| `export_adapter` | `ExportAdapter` |

`_satisfies_protocol` checks that every attribute named in `protocol.__annotations__` is present on the instance, then that every non-private callable defined on the protocol is also callable on the instance. This is a runtime structural check, not full protocol coverage — see remaining doc §3.

Errors are accumulated into `LoadResult.errors` and returned to the caller; nothing raises out of `load_plugin`. `LoadResult.ok` is true iff `errors` is empty and `manifest` is non-null.

## Service (`PluginsService`)

`PluginsService` is the default implementation of the `Plugins` protocol (`backend/src/grimoire/types/protocols.py:287-310`). It owns:

- `_registry: PluginRegistry` — the four per-kind registries (see below)
- `_records: dict[PluginId, _PluginRecord]` — bookkeeping per loaded plugin (manifest, lifecycle, bundled flag, instances by kind, last health/error)
- `_failed: dict[PluginId, list[str]]` — last load errors per id (so `get_status` returns `FAILED`)
- `_discovery_errors: list[DiscoveryError]` — surfaced via `discovery_errors()` for the UI
- `_config_store: PluginConfigStore` — per-plugin config persistence

### Public API

```python
class PluginsService:
    # Discovery / lifecycle
    async def rescan() -> RescanReport
    async def load(plugin_id) -> None       # delegates to rescan()
    async def unload(plugin_id) -> None
    async def activate(plugin_id) -> None
    async def deactivate(plugin_id) -> None

    # Introspection
    async def list_installed() -> list[PluginManifest]
    async def get_manifest(plugin_id) -> PluginManifest | None
    async def get_status(plugin_id) -> PluginStatus
    def discovery_errors() -> list[DiscoveryError]
    def failed_plugins() -> dict[PluginId, list[str]]

    # Per-kind registries
    def llm_providers() / embedding_providers() / imagegen_backends() / export_adapters()
    def get_llm_provider(id) / get_embedding_provider(id) / ...

    # Config
    async def get_config(plugin_id) -> dict
    async def set_config(plugin_id, config) -> None
    async def validate_config(plugin_id, config) -> ValidationResult

    # Health
    async def health_check(plugin_id) -> HealthStatus
    async def health_check_all() -> dict[str, HealthStatus]
```

### Rescan flow

`rescan()` (`service.py:87`) is the single entry point used at startup and from the `/plugins/rescan` HTTP route:

1. Snapshot `_records` and `_failed` for delta computation.
2. `discover(roots=[config.root], bundled_roots=[config.bundled_root])` → store any parse errors in `_discovery_errors` and surface them in the report.
3. For each `DiscoveredPlugin`, derive the plugin id from `raw_manifest["id"]` (fallback to directory name), load the per-plugin config from disk, then call `load_plugin(d, config)`.
4. On success: unregister any previous record under that id (so re-import wins), then `_install(result)` — register the new instances and bookkeep the record.
5. On failure: unregister the previous record, append a `(plugin_id, reason)` to `failed`, and store the per-plugin error list in `_failed`.
6. After processing every discovered plugin, anything that was previously known but is no longer on disk is unregistered and removed from `_failed`.
7. Return `RescanReport(discovered, loaded, failed, removed)`.

`load(plugin_id)` is implemented as a full rescan (`service.py:171-175`) — there is no targeted single-plugin reload yet.

### Lifecycle (`_PluginRecord.lifecycle`)

| State | When |
|---|---|
| `ACTIVE` | Loaded and either config-file present or schema declares no required fields |
| `LOADED` | Loaded but config schema has required fields and no config file exists yet |
| `DEACTIVATED` | Explicit `deactivate(...)` call — instance kept, but unregistered from per-kind registries |
| `FAILED` | Surfaces from `get_status` when the id is only in `_failed`, not in `_records` |
| `UNLOADED` | Surfaces from `get_status` when the id is unknown after an `unload(...)` |
| `DISCOVERED` / `CONFIGURED` | Defined in the enum, not currently emitted |

`activate(plugin_id)` re-registers the cached instances with the per-kind registries; `deactivate(plugin_id)` calls `_registry.unregister_all(plugin_id)` but keeps the record. There is no on-disk "is this plugin enabled?" flag — deactivation lives in memory only.

### Per-plugin config (`PluginConfigStore`)

`PluginConfigStore` (`config_store.py`) reads and writes `data/config/plugins/<plugin-id>.yaml`. Secrets — fields whose JSON Schema property has `secret: true` — are routed through a `KeyringBackend`:

- `save(plugin_id, config, schema)` extracts secret fields, stores them under the keyring key `{service}:{plugin_id}:{field_name}`, and writes a `***` placeholder to the YAML file. Empty secret values are deleted from both the file and the keyring.
- `load(plugin_id, schema)` reads the YAML; for any secret field that is missing / empty / `***`, it consults the keyring and substitutes the recovered value.
- If the optional `keyring` dependency is not installed, secrets are saved to the YAML in plaintext and a WARNING is logged so the operator knows the trust posture dropped.

`InMemoryKeyring` is the test backend used by every plugin test (`conftest.py:24`).

### Shared secrets

A plugin manifest can declare `shares_secrets_with: [other_id]`. When `get_config` is called and a secret field on this plugin is blank, the service walks the listed sibling plugins, fetches their secret values from the keyring (using a synthetic schema covering just the desired field names), and fills them in. This is how `llm-openrouter` and `embed-openrouter` share a single API key — the user configures one side and the other auto-inherits (commits `38afe76`, `eff4e6f`).

`_load_config_for_load` (`service.py:306`) runs the same inheritance pass when the loader needs a config dict at instantiation time, so the inherited secret reaches the plugin's `__init__` and not just `get_config`.

### Health checks

`health_check(plugin_id)` (`service.py:374`) walks every loaded instance for the plugin and calls its `health_check()` coroutine under `asyncio.wait_for(..., timeout=health.timeout_seconds)`. Each instance becomes a `HealthStatus(target_id="<plugin_id>:<kind>")`; `_aggregate_health` reduces them to a single status keyed by the plugin id, with the worst level winning (`UNHEALTHY > DEGRADED > UNCONFIGURED > HEALTHY`).

Non-`HealthStatus` return values are tolerated and reported as healthy with a timing message. Timeouts become `UNHEALTHY` with `"health check timed out after Ns"`; raised exceptions become `UNHEALTHY` with the exception repr. Plugins missing a `health_check` method are reported `UNCONFIGURED`.

`health_check_all()` iterates every loaded plugin id. **The periodic loop from spec 15 (`check_interval_minutes`) is not running** — health checks happen on-demand only. See remaining doc §2.

## Registries (`PluginRegistry`)

One `_KindRegistry` per `PluginKind`, plus a flat `_kinds_by_id` index for "what does this plugin implement?". Insertion order is preserved within a kind. A plugin that implements multiple kinds (e.g. an LLM+embeddings adapter) appears in every relevant list.

`register(plugin_id, kind, instance)`, `unregister_all(plugin_id)`, `get(plugin_id, kind)`, `list(kind)`, `has(plugin_id)`, `kinds_for(plugin_id)`, `ids()`, `reset()` — that's the entire surface.

## HTTP surface

Plugin endpoints live in `backend/src/grimoire/api/library.py:362-484` rather than a dedicated `api/plugins.py`:

- `GET /api/library/plugins/installed` → manifests for every loaded plugin
- `POST /api/library/plugins/rescan` → calls `rescan()` and returns the report
- `GET /api/library/plugins/{id}/config` → returns `{values, secrets_set, configured}` with secrets redacted to a presence flag
- `POST /api/library/plugins/{id}/config` → full `set_config`
- `PATCH /api/library/plugins/{id}/config` → merge a partial config (used by the inline model picker), drops keys removed from the schema so renames are graceful
- `GET /api/library/plugins/{id}/health` → on-demand `health_check`
- `GET /api/library/plugins/{id}/models` → calls the loaded provider's `list_models()` for LLM or embedding providers; 404 if the plugin does not advertise a catalog, 409 if `list_models` raises (e.g. missing API key)

The frontend pieces are in `frontend/src/routes/library/PluginsView.tsx` and `frontend/src/components/PluginModelPicker.tsx`.

## Bundled plugins

Seventeen plugins ship under `backend/bundled_plugins/`:

| Kind | Plugins |
|---|---|
| `llm_provider` | `llm-anthropic`, `llm-llamacpp`, `llm-openai-compatible`, `llm-openrouter`, `llm-zhipu-glm` |
| `embedding_provider` | `embed-openai`, `embed-openrouter`, `embed-sentence-transformers` |
| `imagegen_backend` | `imagegen-a1111`, `imagegen-comfyui`, `imagegen-dalle`, `imagegen-diffusers` |
| `export_adapter` | `export-html`, `export-json`, `export-markdown`, `export-single-markdown`, `export-transcript` |

Each shipped under its own task — see the commit log for the chronology (`d78434e`, `9cf6367`, `5f6651b`, `7706dc3`, `eff4e6f`, `38afe76`, `8d80e72`).

Bundled plugins follow the exact same loading path as user plugins — they live under `backend/bundled_plugins/` instead of `data/plugins/` and the `DiscoveredPlugin.bundled` flag lets the UI label them. A user can drop a same-id directory into `data/plugins/` and it will win over the bundled copy (discovery shadowing rule above).

Bundled providers import their SDK lazily inside the relevant methods (e.g. `llm-anthropic/plugin.py:91-96`) so the plugin still loads and `list_installed()` still returns it even when the optional dep is missing — the missing dep surfaces through `health_check()` as `UNHEALTHY` rather than crashing the import.

## Error handling

- Discovery: per-directory YAML parse errors and non-mapping top levels become `DiscoveryError` records; the rescan continues.
- Manifest validation: returned as `LoadResult.errors`; the plugin is skipped but other plugins still load.
- Import failures: `_import_plugin_module` (`loader.py:188`) pops the half-loaded module out of `sys.modules` so a subsequent rescan can retry cleanly.
- Instantiation / protocol mismatches: per-kind errors; an `LLMProvider`-and-`EmbeddingProvider` plugin can succeed on one kind and fail the other.
- Config-store keyring errors: logged at WARNING, fall back to plaintext on disk.
- Health-check exceptions / timeouts: produce `UNHEALTHY` statuses instead of propagating.

The single end-to-end invariant: `rescan()` never raises for plugin-side issues. Caller exceptions inside `_install` or unrelated bugs are not caught — those would surface as unhandled exceptions out of `rescan()`.

## Test wiring

`backend/tests/plugins/conftest.py` exposes a `write_plugin(root, plugin_id, manifest=..., plugin_py=...)` fixture that builds a complete plugin directory on the fly with a default `Provider` class that satisfies the `LLMProvider` protocol. Tests pass an `InMemoryKeyring` so secret round-trips do not touch the host keyring.

Coverage exists for:
- `test_discovery.py` — manifest walking, hidden-dir skipping, duplicate-id collisions, bundled vs user shadowing
- `test_loader.py` — schema validation, id/dir mismatch, missing plugin.py, import failures, protocol checks
- `test_config_store.py` — secret round-trip via keyring, plaintext fallback warning
- `test_service.py` — rescan delta semantics, lifecycle transitions, shared-secret inheritance, health check aggregation, deactivate/activate round-trip
