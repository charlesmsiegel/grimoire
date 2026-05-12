# Grimoire — task breakdown

Derived from `specs/`. Dependencies are kept minimal so as much work as possible can happen in parallel. Each task lists its direct blockers (transitive blockers omitted).

Critical path (roughly): **1 → 5 → 8 → 9 → 10 → 11 → 12 → 20 → 22 → 31 → 33** — about eleven sequential steps from empty repo to playable UI.

## Wave 1 — Foundation

After `#1` lands, everything else in this wave can run in parallel.

### 1. [x] Scaffold backend + frontend + data layout
**Blocks:** everything
Python FastAPI backend skeleton, TypeScript React+Vite frontend skeleton, and `data/` directory conventions (`data/library/`, `data/campaigns/`, `data/mechanics/`, `data/plugins/`, `data/config/`). Includes lint/format configs, package management (uv/poetry + pnpm), basic CI workflow.

### 2. [x] Define shared types and protocol stubs
**Blocked by:** 1
Cross-module dataclasses and Protocols referenced throughout the specs: `EntityRef`, `InGameTime`, `Duration`, `Composition` + `SettingRef`, `ResolvedEntity`, `StateDelta` + `AppliedDelta`, `Post`, `Scene`, `Fact`, `Commitment`, `Capability`, etc. Pure types only — no behavior. Lives in a shared package both backend and tests import.

### 3. [x] Implement in-process event bus
**Blocked by:** 1
Async pub/sub bus owned by the Orchestrator (spec 01). `subscribe(event_type, handler) → Subscription`; `emit(Event)`. Used by Frontend WS relay, Continuity, Time Engine, ImageGen, Characters drift scheduler, Observability. Core event list documented in spec 00 §Communication and spec 01 §Event bus.

### 4. [x] Build file parsing helpers
**Blocked by:** 1
Markdown + YAML frontmatter parser (used by every entity card kind), `content_hash` computation, slug generator for scene filenames (`NNNN-slug.md`), YAML-only loader for `setting.yaml` / `image-preset.yaml` / sheet files / `campaign.yaml`. Encoding fixed to UTF-8.

### 5. [x] Set up SQLite migrations + sqlite-vec
**Blocked by:** 1
Migration runner with `schema_version` table, WAL enabled, FTS5 + sqlite-vec extension loading at connection open, async connection pool. Apply pending migrations on startup. Provides the substrate the State Store schema task fills in.

### 6. [x] Add JSON Schema validation helpers
**Blocked by:** 1
Wrapper around a JSON Schema validator for: mechanics sheet schemas (per entity kind), plugin manifest `config_schema` forms, mechanics manifest validation, plugin manifest validation. Same validator surfaces consistent errors for the UI.

### 7. [ ] Implement plugin discovery and manifest validation
**Blocked by:** 1, 6
Spec 15. Scan `data/plugins/` for subdirs with `manifest.yaml`, validate manifest, dynamic-import `plugin.py`, instantiate declared classes, validate against the protocol for each implemented kind, register into per-kind registries. Per-plugin venv (optional, configurable). Rescan API. Config storage at `data/config/plugins/<id>.yaml` with keyring secret encryption. Health checks. No specific provider implementations yet — just the loader and registries.

### 29. [x] Set up frontend shell, navigation, WebSocket client
**Blocked by:** 1
Spec 14. SPA shell with Library / Campaigns top-level nav. Routing. Theme + dark mode. WebSocket client with reconnect, subscribing to per-campaign event streams. Client-side state store (optimistic-for-safe, pessimistic-for-consequential). Markdown rendering. Accessibility primitives. Status bar (campaign, model, token budget, queue, drift alerts). Keyboard nav.

## Wave 2 — Storage and infra

### 8. [ ] Build State Store schema and write APIs
**Blocked by:** 2, 5
Spec 03. Migrations for every table: `library_index` (+ FTS), `campaign_content_index`, `library_snapshots`, `campaigns` + `campaign_setting_refs` + `campaign_pcs`, `branches`, `character/location/faction_state`, `scenes` + `posts`, `facts` + `commitments` + `relationships` + `knowledge_state`, `calendar`, `images`, `deltas` + `review_queue`, `embeddings` (sqlite-vec), `llm_requests`, `embedding_cache`, `turn_audits` + `cost_records` + `metric_samples` + `log_events` + `error_records`. Implement read APIs (resolve cascade, `vector_search`, `keyword_search`, `get_delta_log`) and write APIs (`apply_delta`, `reverse_delta`, `queue_for_review`, file-write mediators that update indexes synchronously). Snapshot writing on bind, undo/retcon/branch fork copy-on-write.

### 9. [ ] Wire watchdog file watcher
**Blocked by:** 4, 8
Spec 03/18. Monitor `data/library/` and `data/campaigns/` with Python `watchdog`. On change: parse, upsert into appropriate index (`library_index` or `campaign_content_index`), queue embedding, emit `library_file_changed` / `campaign_file_changed` / `scene_file_changed` / `sheet_file_changed`. Uses `content_hash` to detect actual content changes; tolerates last-write-wins races with a conflict warning.

### 13. [ ] Build LLM Gateway core
**Blocked by:** 2, 7, 8
Spec 05. Routing per task (`main` / `drift_check` / `extractor` / `npc_tick` / `scene_summary` / `running_summary` / `validation` + per-task embedding routes). Provider/embedding lookup via the Plugins module. `complete`/`stream`/`embed` with retries + timeouts + fallback. Token + cost tracking written to `llm_requests`. Embedding cache by `(text_hash, model_id)`. Health monitoring. Estimation API. Per-campaign route overrides.

### 16. [ ] Build Mechanics API surface and module loader
**Blocked by:** 2, 4, 6, 8
Spec 06. Define `MechanicsModule` protocol (`sheet_schema`, `validate_sheet`, `initialize_sheet`, `content_schema`, `capabilities_of`, `power_definitions`, `evaluate_pre_roll`, `resolve_roll`, `validate_narrated_event`, `character_creation_steps`, `time_tick`, `system_summary`). Manifest validation + discovery under `data/mechanics/`. Façade `Mechanics` callable from other modules (`active_module`/`sheet_schema`/`get_sheet`/`update_sheet`/`capabilities_of`/`evaluate_pre_roll`/`resolve_roll`/`validate_narrated_event`/`time_tick`). Sheet read/write delegates to State Store (campaign-local YAML files). Deterministic RNG per branch. Trivial empty results when `mechanics: null`. No mechanics modules ship by default — this is the API contract only.

### 30. [x] Build sheet widget library
**Blocked by:** 29
Spec 14 §Sheet widget library. TypeScript React components: `text`, `textarea`, `number`, `select`, `multi-select`, `boolean`, `dot-rating`, `dice-pool`, `health-track`, `power-list`, `grid-rating`, `slot-list`, `keyword-list`, `nested-section`. Renderer that reads JSON Schema produced by a mechanics module and dispatches each property to the named widget; fallback to generic editor with warning for unknown widgets. Per-mechanics CSS isolation via `.mechanics-<module-id>` wrapper + PostCSS scope-prefixing plugin so themes can't leak between systems.

## Wave 3 — Domain modules

### 10. [ ] Implement Library indexer and Library protocol
**Blocked by:** 4, 8, 9
Spec 18. `Library` protocol: `list_settings`/`get_setting`/`list_in_setting`/`get_entity`/`list_style_guides`/`list_image_presets`/`list_greetings`/`variants_of`/`create_entity`/`update_entity`/`delete_entity`/`promote_to_library`/`get_composition`/`set_composition`/`upgrade_setting_ref`/`resolve`/`dependents`. Initial scan on startup populates `library_index` from `data/library/`. Snapshot writing for pinned setting refs. Override file write/read at `campaigns/<id>/overrides/settings/<setting>/<kind>/<id>.yaml`. Read cascade: campaign emergent → campaign override → library snapshot (pinned) or library index (`track_latest`) → fail.

### 14. [ ] Implement bundled LLM provider plugins
**Blocked by:** 7, 13
Spec 05 + 15. `llm-anthropic` (cloud, streaming, tools-capable) and `llm-llamacpp` (local via `llama-cpp-python` pointing at a GGUF file). Each plugin includes `manifest.yaml` + `plugin.py` + `config_schema` + `requirements.txt`. Both implement the `LLMProvider` protocol (`complete`/`stream`/`list_models`/`health_check`). Ship under `data/plugins/` (or bundled-plugins root).

### 15. [ ] Implement bundled embedding provider plugins
**Blocked by:** 7, 13
Spec 05 + 15. `embed-sentence-transformers` (local, default `all-mpnet-base-v2`, runs in thread pool) and `embed-openai` (cloud, configurable model and dimensions). Implement `EmbeddingProvider` protocol (`embed`/`health_check`). Ship under `data/plugins/`.

### 17. [x] Build Scene Manager
**Blocked by:** 4, 8, 9
Spec 10. Scene markdown + YAML sidecar pair storage at `data/campaigns/<id>/scenes/NNNN-slug.{md,yaml}`. Append posts with author labeling. Active scene per campaign + per-PC. Scene boundary detection (time gap / location / cast / tonal / explicit / user signal) with confidence thresholds. Multi-PC advance decision (auto-respond if ≤1 PC, wait for advance if 2+). `on_advance_requested` marks `last_advance_at_post`. Running summary regen every N posts. Scene close + key beats + threads. Thread tracking (introduced/paid_off). Edit/delete post triggering retcon. Fork copy-on-write of scenes for branches.

### 18. [x] Build Continuity module
**Blocked by:** 8, 13
Spec 11. Fact ledger (`add`/`retire`/`update`/`get`/`facts_about`/`search`/`recent` — vector + keyword). Contradiction detection (top-K similar + LLM judgment) with explicit resolution flow. Commitment ledger (`add`/`resolve`/`get`/`open`/`overdue`/`stale`) with status lifecycle (OPEN→PAID/BROKEN/OVERDUE/STALE). Knowledge state per character per fact. Aging (commitments come due / go stale) when Time Engine calls `age()`.

### 28. [ ] Build testing infrastructure
**Blocked by:** 7, 13, 16
Spec 17. `TestApp` harness with fixture loading. `MockLLMGateway` with per-task response queues that fail-loud on exhaustion. `RecordReplayLLM` (record/replay/passthrough) writing fixtures to `tests/fixtures/llm/by_hash/`. Plugin conformance suites per kind (mechanics, llm_provider, embedding_provider, imagegen_backend, export_adapter) with per-kind test lists. Frozen-campaign harness (load anonymized SQLite snapshot, run N turns, assert invariants). Performance regression benchmark suite with 20% threshold. CI pipeline definition (lint → unit → conformance → integration → frozen-campaign → perf).

## Wave 4 — Content + Integrators

### 11. [ ] Build Setting module
**Blocked by:** 10
Spec 09. CRUD for items/locations/lore/factions/greetings within a setting (delegates to Library writes). Per-campaign composition resolution with `include` filters. Location adjacency / `path_between` / `locations_within`. Cross-setting variant lookup by shared `asset_id`. Lore keyword search for archive-tier triggers. Procedural weather (seeded per campaign, deterministic). Calendar / season / holiday queries. Faction state CRUD (campaign-scoped, SQLite). `promote_to_library` for non-character kinds. `setting.yaml` CRUD + `fork_setting` (directory copy).

### 12. [ ] Build Characters module
**Blocked by:** 11, 16
Spec 08. Behavior layer over Setting's character storage. Voice anchors + dialogue sample rotation. Drift detection (LLM call against recent dialogue; produces `drift_score` + corrective context). Context tier recommendation (lock-in / spotlight / background / archive) with user pins. PC role tracking + multi-PC coordination (`list_pcs`/`add_pc`/`remove_pc`/`set_active_pc`/`current_scene_for_pc`/`should_auto_respond`/`present_pcs_in_scene`). Cross-setting variant lookup. Compressed card views (full/compressed/voice-only/capsule). Campaign-scoped relationships + relationship state. Capability surfacing via Mechanics. `promote_to_library` wrapper. Imports: SillyTavern v2/v3 cards, charx, plaintext.

### 19. [ ] Build Extractor
**Blocked by:** 8, 13, 16, 18
Spec 04. Parallel strategies: rule-based (regex/patterns for time markers, inventory verbs, mechanical echoes), structured LLM (Haiku-tier with JSON output schema covering `facts`/`character_updates`/`new_characters`/`scene_changes`/`time_advances`/`commitments`/`inventory_changes`/`mechanical_events`/`relationship_changes`/`commitment_resolutions`), heuristic flags. Confidence scoring + auto-apply (≥0.85) / review queue (0.60–0.85) / drop (<0.60). Entity candidates default campaign-local (never library). Library-targeted change detection proposes campaign override, not library edit. Mechanical event validation via `Mechanics.validate_narrated_event`. Also processes user-authored text.

### 21. [ ] Build Time Engine
**Blocked by:** 11, 12, 13, 16, 18
Spec 07. `advance(duration, reason)` and `skip_to(target)`. NPC tick architecture: significance filter (major/spotlight/PC-commitment/household + recent appearances), shared-events pre-pass for inter-NPC coherence, per-NPC structured LLM tick (`npc_tick` task) with knowledge/secrecy split. Faction ticks at month granularity. Weather/atmosphere via Setting. Scheduled events (holidays, recurring schedules, plot beats). Commitment aging via `Continuity.age()`. `Mechanics.time_tick` fan-out per character. Digest generation (structured + optional narrative prose).

## Wave 5 — Top-level + Producers

### 20. [ ] Build Context Builder
**Blocked by:** 11, 12, 13, 16, 17, 18
Spec 02. `build(player_input, campaign_id, mechanics_results)` returns `AssembledPrompt` with `messages`, `params`, `budget_used`, `sources`, `summary`. Pipeline: resolve composition → scene state → cast (with tier promotion via Characters) → setting (location, adjacent, weather, factions via Setting) → continuity (facts, commitments via Continuity) → archive retrieval (vector + keyword, scoped to campaign-local + referenced library assets) → budget allocation per tier → canonical message ordering. Style guide + content boundaries from composition. Voice anchor injection for spotlighted speakers. Mechanics result injection as authoritative. Source attribution (scope, library asset id, override applied).

### 22. [ ] Build Orchestrator + turn loop
**Blocked by:** 3, 13, 16, 17, 19, 20
Spec 01. `submit_post(campaign_id, pc_ref, text)` and `advance(campaign_id, scene_id)` entry points. Per-campaign turn lock; multiple campaigns can run in parallel. Canonical turn flow: scene break check → mechanics `evaluate_pre_roll` → Context Builder `build` → LLM Gateway `stream` → Extractor `extract` (with Mechanics + Continuity checks) → State Store apply deltas → Scene Manager append response. Stream chunks forward to Frontend via WebSocket. Background work fan-out (ImageGen, time advance, drift checks, NPC ticks) after `turn_complete`. Undo/retcon/fork at turn level. Error handling per step with rollback. Owns the event bus.

### 23. [ ] Build ImageGen core with integrated diffusers backend
**Blocked by:** 10, 11, 12, 17
Spec 12. `IntegratedDiffusersBackend` (SDXL via HuggingFace `diffusers`, lazy weight download with user prompt on first use, GPU/CPU auto-detect, fp16 on CUDA). Generation job queue (per-backend serial, multiple backends parallel). Prompt composition from image preset + location + present cast image templates + scene visual extraction + mood. Image storage at `data/campaigns/<id>/images/<id>.png` + sidecar YAML; thumbnails 256×256 JPG. Cache by `(prompt_hash, negative_hash, params_hash, seed, model)`. Re-roll, variation (img2img), star/save, delete. Trigger config (`per_scene` / `per_post` / `every_n_posts` / `manual_only`).

### 25. [ ] Implement Export EPUB adapter
**Blocked by:** 7, 11, 12, 17, 18
Spec 13. EPUB 3 pipeline: front matter (title, copyright, TOC, dedication), scenes-as-chapters (with inline illustrations + post formatting + mechanics annotations), appendices (cast, setting, continuity ledger, calendar, image gallery — toggleable), bundle into EPUB package with stylesheet. Two style presets (Novel, Manuscript). EPUBCheck validation. Filter pipeline (strip OOC, mechanics, anonymize, content filter, POV consolidation). Per-scene and per-arc selection. Cover image support (user-provided or default).

### 26. [ ] Implement remaining export adapters
**Blocked by:** 7, 11, 12, 17, 18
Spec 13. `markdown` bundle (directory tree with scenes + characters + setting + continuity + images), `single_markdown` concatenated file, `json` structured dump (full state, optional embeddings, pretty-print), `transcript` plain-text prose-only, `html` standalone with relative assets. Each implements `ExportAdapter` protocol with `options_schema`. Shared transformation/filter pass.

## Wave 6 — Surface + Observability

### 24. [ ] Implement bundled ImageGen plugin backends
**Blocked by:** 7, 23
Spec 12 + 15. `imagegen-a1111` (HTTP client for Automatic1111), `imagegen-comfyui` (HTTP with workflow loaders for new model architectures), `imagegen-dalle` (OpenAI DALL-E API). Each implements `ImageGenBackend` protocol (`generate`/`list_models`/`list_samplers`/`health_check`) with manifest + `config_schema`. Same protocol as integrated backend so they're swappable via routing.

### 27. [ ] Build Observability module
**Blocked by:** 3, 8, 13, 22
Spec 16. `TurnAudit` record assembled by subscribing to Orchestrator events: composition snapshot, scene context, assembled prompt + budget, mechanics results, LLM call metadata (provider/model/tokens/cost/latency/retries), response text, extraction strategies/duration/deltas/flags, applied deltas, queued reviews, side effects, errors. `CostTracker` (`record`/`total`/`by_day`/`by_task`/`by_model`). Performance metrics per module with rolling window. Health monitor (`probe_all` + `subscribe`). Debug log (`LogEvent` with module/operation/turn_id/payload). `ErrorRecord` with attribution. Turn replayer (fork mode by default, optional model/temperature/prompt substitution). Retention policy + nightly maintenance.

### 31. [ ] Expose backend REST + WebSocket API
**Blocked by:** 22, 23, 25
Spec 14 §Backend contract. FastAPI endpoints surfacing every module: `/library/settings/{id}/{kind}`, `/library/variants`, `/library/style-guides`, `/library/image-presets`, `/mechanics/installed` (+ rescan), `/plugins/installed` (+ rescan); `/campaigns` CRUD, `/campaigns/{id}/composition` (+ refs + upgrade), `/campaigns/{id}/pcs`, `/campaigns/{id}/turns` (submit / advance / regenerate / undo / retcon), `/campaigns/{id}/forks`, `/campaigns/{id}/scenes`, `/campaigns/{id}/{characters,items,locations,lore,factions}`, `/campaigns/{id}/sheets/{kind}/{id}`, `/campaigns/{id}/facts`, `/campaigns/{id}/commitments`, `/campaigns/{id}/time/advance`, `/campaigns/{id}/images/generate` (+ list), `/campaigns/{id}/export`, `/campaigns/{id}/reviews/{id}` approve/reject. WS `/campaigns/{id}/stream` emitting `token`/`turn_complete`/`image_ready`/`drift_detected`/`contradiction_detected`/`review_item_added`/`npc_tick_complete`/`scene_started`/`scene_ended`/`library_file_changed`/`library_ref_upgraded`/`pc_post_appended`/`advance_requested`/`advance_disabled`.

## Wave 7 — Frontend assembly

### 32. [ ] Build frontend Library views
**Blocked by:** 29, 31
Spec 14. Settings list and detail (with tabs for each entity kind + Meta + Dependent campaigns). Per-kind editors (frontmatter form + markdown body; characters get voice editor, image prompt template, capabilities tab). Edit-with-dependents warning. Cross-setting variants tab. Style guides + image presets editors (with sample preview). Installed mechanics view (per-module manifest summary, sheet schemas, `theme.css` preview, load errors). Installed plugins view per kind (LLM / embedding / ImageGen / export) with config forms (rendered from each plugin's `config_schema`).

### 33. [ ] Build frontend Campaign Play view
**Blocked by:** 29, 30, 31
Spec 14. Top bar with active PC switcher. Scene header (location, in-game time, present cast, source badges). Scene pane: posts in order with author labels, inline generated images, mechanical event chips, source badges on entity references, drift warning banners. Side panel: present cast, active threads, capabilities (active PCs), mechanics rolls/slots, quick actions (regen, undo, end scene, skip time, manual fact). Input area with PC selector + Submit + Advance button (only when scene has 2+ PCs and there's something to advance). Real-time updates via WS (token streaming, `advance_disabled`/`enabled`, `drift_detected`, contradictions, `image_ready`).

### 34. [ ] Build frontend Cast/World/Timeline/Mechanics/Composition/Images views
**Blocked by:** 29, 30, 31
Spec 14. Cast view: resolved characters by tier or source, filters, character detail (resolved card, source chain, voice anchor with samples, mechanical sheet rendered via widget library, capabilities, relationships, recent scenes, edit override / library / promote actions). World view: items / locations / lore / factions / greetings tabs. Timeline view: scenes as cards along in-game timeline with threads as lines. Mechanics view: active module info, sheet list, missing-sheets panel, roll log, combat tracker hook, content browser. Composition view: editable refs with priority/include/`track_latest` + upgrade-available banner with diff preview. Images view: gallery + queue + per-character prompt templates.

### 35. [ ] Build frontend campaign creation flow + settings
**Blocked by:** 29, 31
Spec 14. Six-step creation wizard: identity → composition (multi-setting picker with priority + include filters) → mechanics (installed module or `mechanics: null`, with bulk-create-sheets offer) → PCs (pick or create) → style & content (style guide ref or inline, image preset, content boundaries) → starting scene (greeting picker, confirm location/time/cast). Per-campaign settings tabs: General, Model routing (LLM + embedding per task), ImageGen (backend + preset + sampler), Mechanics (active module + module-specific options), Storage (backup), Advanced (per-task prompts + debug log). App-level settings: library path, provider configs, mechanics/plugin scan paths, backup policy, appearance.

## Dependency graph (compact)

```
1 ──┬──> 2 ──> 8 ──> 9 ──> 10 ──> 11 ──> 12 ──> 20 ──> 22 ──> 27
    │                                                  │
    ├──> 3 ───────────────────────────────────> 22 ────┴──> 31 ──> 32/33/34/35
    │
    ├──> 4 ──> 9
    │
    ├──> 5 ──> 8
    │
    ├──> 6 ──> 7 ──> 13 ──> 14, 15, 18, 19, 28
    │              └──> 16 ──> 12, 19, 20, 21, 28
    │
    ├──> 17 ──> 20, 21, 23, 25, 26
    ├──> 18 ──> 19, 20, 21, 25, 26
    │
    └──> 29 ──> 30 ──> 33, 34
              └────> 32, 33, 34, 35

23 ──> 24, 31
25 ──> 31
```
