# Plugins — Remaining Work

> Everything from the original `specs/15-plugins.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-plugins-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-plugins-design.md`
**Module:** `backend/src/grimoire/plugins/`

## 1. Per-plugin venv isolation

Spec 15 §Discovery and loading step 2c and §Configuration `isolation.per_plugin_venv: true`: when a plugin ships a `requirements.txt`, the loader should create a per-plugin virtualenv at `data/plugins/.venvs/<plugin-id>/`, install the deps there, and import `plugin.py` against that venv.

Today none of this happens. `IsolationConfig.per_plugin_venv` defaults to `False`, the manifest field `isolated_venv` is parsed and stored on `PluginManifest` but never acted on, and the loader doc-comment (`loader.py:10-14`) explicitly notes this is deferred. Bundled plugins all have `requirements.txt` files (`anthropic`, `sentence-transformers`, `diffusers`, etc.) but those deps just have to be present in the main app environment.

Design needs: how to share the host interpreter's `sys.path` for the grimoire core packages (so `from grimoire.types.llm import CompletionRequest` still resolves inside the venv), whether to fork a subprocess per plugin or use importlib site-packages manipulation, what happens to `pip install` failures (skip the plugin? load it from the host env as a fallback?), and a TTL/cleanup story for `.venvs` directories that outlive the plugin they were created for.

## 2. Periodic health check loop

Spec 15 §Health checks: "The app runs them periodically (default: every 5 min for active plugins) and reports status".

`HealthConfig.check_interval_minutes = 5` exists; `PluginsService.health_check_all()` exists. The wiring that calls it on a timer does not. The HealthMonitor module (`backend/src/grimoire/observability/health.py:195-224`) already has a `start_periodic` pattern that could be the template — but it currently probes its own targets (LLM gateway, library indexer, etc.), not plugins.

Likely shape: subscribe `PluginsService.health_check_all` into the observability HealthHandler so plugin health rolls up into the unified health view; or run a dedicated `asyncio.create_task` from `_create_app` with stop/start aligned to FastAPI lifespan. Store the last result on `_PluginRecord.last_health` (the field exists; only `health_check` populates it today).

## 3. Stricter protocol satisfaction check

`_satisfies_protocol` (`loader.py:237-262`) only checks member *presence*, not signatures or types. A plugin can pass with a non-async `complete` or a `stream` that returns the wrong shape, and the breakage surfaces at first use rather than at load.

Tighten this with `inspect.signature` checks (correct arg names / count), `inspect.iscoroutinefunction` for the methods spec 15 declares async, and a typed protocol-conformance helper that walks `typing.get_type_hints(protocol)`. Goal: fail loudly at `rescan` time with a precise message rather than at first request.

This unlocks v2 protocol additions safely: today, adding a method to `LLMProvider` would silently degrade older plugins instead of marking them failed.

## 4. Per-plugin lifecycle events on the event bus

Spec 15 §Plugin lifecycle and `specs/01-orchestrator.md` §Event bus list `plugin_loaded`, `plugin_failed`, `plugin_unloaded`, `plugin_health_changed`. None are emitted today — `rescan` returns a report, the frontend re-fetches `/plugins/installed`, and nobody else hears about the change.

Wire `PluginsService` with an `event_bus` collaborator (the orchestrator already owns one) and emit:
- `plugin_loaded(plugin_id, manifest, bundled)` after each successful `_install`
- `plugin_failed(plugin_id, errors)` after each failed load
- `plugin_unloaded(plugin_id)` on `unload()` / removed-on-rescan
- `plugin_activated(plugin_id)` / `plugin_deactivated(plugin_id)`
- `plugin_health_changed(plugin_id, before, after)` from `health_check` when the level changes

Subscribers: the WebSocket push layer so the UI updates without polling; cost tracker / observability so health transitions are recorded.

## 5. Persistent activation state

Today `deactivate(plugin_id)` only edits in-memory state. Restart the app and every previously-deactivated plugin comes back active. Spec 15 §Plugin lifecycle treats deactivation as a first-class state ("I have llamacpp configured but I'm using anthropic today").

Add a tiny `data/config/plugins/.activations.yaml` (or a `_active: false` field inside each plugin's config file) that `rescan` consults when deciding the initial lifecycle. `activate()` / `deactivate()` write the change through.

## 6. Capabilities surfaced on the manifest

The manifest schema allows a free-form `capabilities: object` (`manifests.py:61`) and bundled plugins set it (e.g. `llm-anthropic` declares `streaming/tools/vision/max_context`), but `PluginManifest` does not project capabilities onto a typed field. Consumers that want them have to dig through `manifest.raw["capabilities"]`.

Define a discriminated-union shape per kind: `LLMCapabilities`, `EmbeddingCapabilities`, `ImageGenCapabilities`, `ExportCapabilities`. Validate them in `validate_plugin_manifest` based on `implements`. Project onto `PluginManifest.capabilities` so the LLM Gateway can pick a model by capability without instantiating the plugin.

## 7. Per-campaign routing configuration

Spec 15 §Routing defines `model_routing`, `embedding_routing`, `imagegen_routing` blocks on the campaign config that map task names (`main`, `drift_check`, `extractor`, `npc_tick`, etc.) to `<plugin_id>.<model_id>` references.

Grep across the backend (`model_routing|embedding_routing|imagegen_routing`) returns no hits in `grimoire/`. The LLM Gateway's `complete(task, ...)` API takes a task string but resolves it via gateway-level config rather than per-campaign overrides.

Needs: a routing schema on the campaign config, a resolver in the Gateway/ImageGen/Export modules that prefers campaign routing over global defaults, validation that the referenced plugin id is loaded and exposes the named model. UI exposure on the Campaign Settings screen.

**Implemented (2026-05-18):** `_load_campaign_routing` on the LLM Gateway now reads `embedding_routing` and `imagegen_routing` blocks alongside `model_routing`. Embedding routes are applied to the same `RouteResolver` (task-name uniqueness keeps them disjoint); when the referenced plugin is loaded, the gateway calls `list_models()` and logs a warning if the model id is not advertised (the route is still applied — providers can return dynamic lists). `imagegen_routing` is parsed and validated but **not yet acted on**; an explicit warning is logged per entry. `_persist_campaign_route` continues to write only to `model_routing`. Deferred future work: thread `imagegen_routing` through `ImageGenService` so per-task image backends actually take effect, persist `embedding_routing` / `imagegen_routing` from `set_route`, and surface all three blocks on the Campaign Settings screen.

## 8. Targeted single-plugin load / reload

`PluginsService.load(plugin_id)` (`service.py:171`) currently just calls `rescan()`. Spec 15 §Interface treats `load` as the per-plugin operation for retrying a failed plugin without re-importing everything.

Refactor `_install` to accept a single `DiscoveredPlugin`, expose `_rediscover_one(plugin_id)` that walks the roots looking for one directory, and have `load(plugin_id)` use them. This matters for plugins whose import takes seconds (e.g. `imagegen-diffusers`'s `diffusers` package): a config tweak shouldn't pay that cost.

## 9. UI surfacing of discovery errors

`PluginsService.discovery_errors()` exists but nothing in the API layer returns it. Failures during `discover` (e.g. a malformed `manifest.yaml`) silently disappear from the user's view — they appear in the rescan report's `failed` list keyed by directory name, but the actual parse error message lives only in `_discovery_errors`.

Either include them in `RescanReport.failed` consistently or add a dedicated `/api/library/plugins/discovery-errors` endpoint that the PluginsView can poll.

## 10. Failure surface on `set_config`

`set_config` (`service.py:278`) re-instantiates nothing — it saves the new config to disk and flips the lifecycle to `ACTIVE`, but the already-loaded instance keeps its stale config. The user has to hit "Rescan" before the new key takes effect.

Two options:
- (a) After a successful `set_config`, call `load_plugin(...)` for just this plugin to rebuild its instances with the fresh config. Needs §8 first.
- (b) Standardise a `Plugin.reconfigure(new_config)` optional hook so plugins can update in place. Most bundled plugins cache the API key on `__init__` and would need to be rewritten to support it.

Pick (a) once §8 lands.

## 11. Bundled-plugin signing/checksumming (v2; deferred)

Spec 15 §Security and trust: "Bundled plugins are signed/checksummed (v2)". Out of scope; recording here so it does not get re-litigated.

## 12. WASM / subprocess sandboxing (v2/v3; deferred)

Spec 15 §Security: "WASM or subprocess sandbox for untrusted plugins". v2/v3 work; do not pick up without an explicit threat-model exercise.

## 13. Plugin marketplace (v2; deferred)

Spec 15 §Open questions: a central index for browsing community plugins. Out of v1 scope.

## 14. Plugin auto-update (v2; deferred)

Spec 15 §Open questions: check `manifest.version` against an upstream feed. Not v1.

## 15. Cross-plugin dependencies (v2; deferred)

Spec 15 §Open questions: a plugin requires another plugin to function. The current `shares_secrets_with` mechanism is the closest thing today; a full dependency declaration is v2.

## 16. GUI plugin authoring helper (rejected)

Spec 15 §Open questions: "GUI for plugin authoring. A development helper that scaffolds a manifest + plugin.py from templates. Nice-to-have." The existing bundled plugins already serve as the template; treat as **rejected** unless concrete user demand emerges.

## 17. Plugin testing harness (v2; deferred)

Spec 15 §Open questions: "Run a plugin's protocol methods in isolation against mock data. v2." Defer.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §3 + §4 + §9 — tighten the failure surface end-to-end (stricter protocol checks, lifecycle events, discovery error surfacing). Mostly local to the Plugins module; no other modules need to change shape.
2. §6 — capability typing. Pure-additive on the manifest and types layer; unblocks the Gateway-side routing work that follows.
3. §7 — per-campaign routing. Needs §6 to pick by capability and reaches into Gateway/ImageGen/Export.
4. §8 + §10 — targeted load + reload on config change. §10 depends on §8.
5. §5 — persistent activation state. Small but visible; pair with the events from §4 so the UI stays in sync.
6. §2 — periodic health loop. Sequence after §4 so health transitions can ride the same event channel.
7. §1 — per-plugin venv isolation. The most invasive change; do last so the contract above is stable. Pair with a writing-plans pass on the sandbox question (§12) before deciding the runtime model.
