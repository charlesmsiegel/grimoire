# Container/DI Typing Refactor

Date: 2026-05-23
Status: Approved
PR: 1 of ~11 in the Grimoire code quality refactor series

## Context

This is the first PR in a series that addresses findings from two code reviews ([docs/code-review-refactor-plan.md](../../code-review-refactor-plan.md) and [docs/claude-code-review-refactor-plan.md](../../claude-code-review-refactor-plan.md)). The full refactor is decomposed by domain into ~10 PRs:

1. **Container/DI typing** (this spec)
2. Error handling (add `http_status` to exceptions, replace substring mapping)
3. `main.py` lifecycle extraction (ServiceGraphBuilder, LifecycleManager)
4. OrchestratorService split (TurnRunner, RetconCoordinator, ForkCoordinator, DeltaApplier)
5. ContextBuilderService split (providers, section timing, concurrent reads)
6. Other large service splits (CharactersService, LibraryService, LLMGatewayService, StateStore)
7. Router/API consistency (split campaigns.py, response models, pagination, URL naming)
8. Data layer hardening (consolidate DB access, cache invalidation, JSON encoding)
9. Event bus formalization (typed events, payload schemas, consistent adoption)
10. Frontend restructuring (route splits, usePlayState extraction, runtime validation)
11. Performance observability and optimization (metrics, context caching, frontend data flow)

Each PR is ordered so that earlier work provides the foundation for later work. Container/DI typing is first because every subsequent PR benefits from typed service access.

## Problem

`ServiceContainer` (`backend/src/grimoire/api/container.py`) stores 18 service fields as `Any` and a catch-all `extras: dict[str, Any]` that holds 17 additional services plus 2 diagnostic strings. This means:

- No IDE autocompletion or type checking through the API layer.
- Typos in `container.extras["llm_gatway"]` are silent runtime bugs.
- Test stubs can diverge from production contracts without type errors.
- `deps.py` has 15 nearly identical `get_X()` functions returning `Any`, plus 2 special-case functions that dig into the `extras` dict differently.
- New services require editing `main.py` to add an `extras["name"]` entry rather than a typed field.

## Solution

Promote all `extras` dict residents to typed optional fields on `ServiceContainer`, type the existing 18 `Any` fields with their concrete service classes, and remove the `extras` dict entirely.

## Detailed Design

### ServiceContainer Field Changes

**File:** `backend/src/grimoire/api/container.py`

All imports guarded under `TYPE_CHECKING` to avoid circular imports at runtime.

#### Existing fields: change type from `Any` to concrete

| Field | New Type |
|-------|----------|
| `library` | `LibraryService \| None` |
| `world` | `WorldService \| None` |
| `characters` | `CharactersService \| None` |
| `scenes` | `SceneManager \| None` |
| `continuity` | `ContinuityRegistry \| None` |
| `time_engine` | `TimeEngineService \| None` |
| `imagegen` | `ImageGenService \| None` |
| `export` | `ExportService \| None` |
| `mechanics` | `MechanicsService \| None` |
| `plugins` | `PluginsService \| None` |
| `state_store` | `StateStore \| None` |
| `orchestrator` | `OrchestratorService \| None` |
| `observability` | `ObservabilityService \| None` |
| `hud` | `HudService \| None` |
| `hud_config` | `HudConfigService \| None` |
| `transient_state` | `TransientStateService \| None` |
| `extras_service` | `ExtrasService \| None` |
| `calendar` | `CalendarService \| None` |

#### New fields: promoted from `extras` dict

| Field | Type | Was |
|-------|------|-----|
| `llm_gateway` | `LLMGatewayService \| None` | `extras["llm_gateway"]` |
| `extractor` | `ExtractorService \| None` | `extras["extractor"]` |
| `context_builder` | `ContextBuilderService \| None` | `extras["context_builder"]` |
| `file_watcher` | `FileWatcher \| None` | `extras["file_watcher"]` |
| `scene_indexer` | `SceneIndexer \| None` | `extras["scene_indexer"]` |
| `embedding_worker` | `EmbeddingWorker \| None` | `extras["embedding_worker"]` |
| `body_summarizer` | `BodySummarizer \| None` | `extras["body_summarizer"]` |
| `retention_sweeper` | `RetentionSweeper \| None` | `extras["retention_sweeper"]` |
| `backup_scheduler` | `BackupScheduler \| None` | `extras["backup_scheduler"]` |
| `scene_summary_worker` | `RunningSummaryWorker \| None` | `extras["scene_summary_worker"]` |
| `imagegen_integration` | `ImageGenIntegration \| None` | `extras["imagegen_integration"]` |
| `imagegen_health_prober` | `ImageGenHealthProber \| None` | `extras["imagegen_health_prober"]` |
| `characters_integration` | `CharactersIntegration \| None` | `extras["characters_integration"]` |
| `time_engine_subscriber` | `TimeEngineSubscriber \| None` | `extras["time_engine_subscriber"]` |
| `state_store_config` | `StateStoreConfig \| None` | `extras["state_store_config"]` |
| `mechanics_rescan_error` | `str \| None` | `extras["mechanics_rescan_error"]` |
| `plugins_rescan_error` | `str \| None` | `extras["plugins_rescan_error"]` |

#### Removed

- `extras: dict[str, Any]` field is removed entirely.

### deps.py Changes

**File:** `backend/src/grimoire/api/deps.py`

- Each `get_X()` function returns the concrete service type instead of `Any`.
- `get_llm_gateway()` and `get_file_watcher()` switch from `container.extras.get("name")` to standard `_require(container, "name")` pattern, since those services are now typed fields.
- All `Annotated` aliases use concrete types: e.g. `LibraryDep = Annotated[LibraryService, Depends(get_library)]`.
- New aliases added for promoted services that routers need (e.g. `LLMGatewayDep`, `FileWatcherDep` -- these already exist but their types change from `Any` to concrete).

### main.py Changes

**File:** `backend/src/grimoire/main.py`

Mechanical search-and-replace throughout `lifespan()` and `_shutdown()`:

- `container.extras["name"] = value` becomes `container.name = value`
- `container.extras.get("name")` becomes `container.name`
- `container.extras.get("name") is None` becomes `container.name is None`

No behavioral changes. The initialization order, conditional construction, and error handling remain identical.

### Router Changes

Routers access services through `deps.py` `Depends()` functions and never reference `container.extras` directly. Router files should need **zero changes**. The type improvement flows through deps.py transparently.

Verify with a grep for `container.extras` in `backend/src/grimoire/api/` to confirm no router directly accesses the dict.

### Test Changes

Test files that pre-wire a `ServiceContainer` currently set `container.extras["name"] = mock_value`. These become `container.name = mock_value`. Search-and-replace across test fixtures.

Files likely affected:
- `backend/tests/conftest.py`
- `backend/tests/api/conftest.py`
- Any test that constructs a `ServiceContainer` directly

### Migration Strategy

This is a single atomic commit. There is no incremental migration path needed because:

1. All `extras` access is internal to the grimoire package (no external consumers).
2. The field names match the dict keys exactly.
3. Every access site is findable by grepping for `extras`.

### Verification

1. `ruff check` passes on all changed files.
2. `ruff format --check` passes.
3. `pytest` full suite passes (unit, integration, conformance, golden).
4. Grep for `container.extras` returns zero hits in `backend/src/grimoire/`.
5. Grep for `extras` in `container.py` returns zero hits (field removed).
6. IDE autocompletion works on `container.llm_gateway` (manual check).

## Scope

### In scope

- Type all ServiceContainer fields with concrete types
- Remove `extras` dict entirely
- Update deps.py return types and type aliases
- Update main.py lifespan and shutdown
- Update test fixtures that set extras

### Not in scope (deferred to later PRs)

- Reducing the number of container fields (PR 3: lifecycle extraction)
- Extracting ServiceGraphBuilder from main.py (PR 3)
- Adding Protocols for service interfaces
- Changing how services are constructed
- Any changes to service implementations themselves

## Risks

**Low risk.** This is a mechanical refactor with no behavioral changes. The main risk is missing an `extras` access site, which would manifest as an `AttributeError` at runtime. The grep-based verification step catches this.

The `TYPE_CHECKING` import guard pattern is well-established in the codebase (already used in `container.py` for `Database`, `EventBus`, `StreamManager`). Extending it to more types follows the existing pattern.
