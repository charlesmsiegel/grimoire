# PR 1: Container/DI Typing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Type all `ServiceContainer` fields with concrete types, promote all `extras` dict residents to typed fields, and remove the `extras` dict entirely.

**Architecture:** Mechanical refactor. No behavioral changes. Every `container.extras["name"]` becomes `container.name` with a concrete type. All imports are guarded under `TYPE_CHECKING` to avoid circular imports at runtime. The existing test suite verifies nothing breaks.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, dataclasses

---

### Task 1: Update ServiceContainer with typed fields

**Files:**
- Modify: `backend/src/grimoire/api/container.py`

- [ ] **Step 1: Add TYPE_CHECKING imports for all service types**

Replace the current file content with:

```python
"""Service container for the REST + WebSocket API surface.

Holds references to the long-lived service instances the routers need. Services
are wired in :func:`grimoire.main.create_app` and looked up by FastAPI
dependencies in each router. Services are optional so tests can populate only
what they need; an endpoint that requires an absent service returns ``503``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.api.stream import StreamManager
    from grimoire.characters import CharactersService
    from grimoire.characters.integration import CharactersIntegration
    from grimoire.context.builder import ContextBuilderService
    from grimoire.context.inspector import ContextInspector
    from grimoire.continuity import ContinuityRegistry
    from grimoire.event_bus import EventBus
    from grimoire.export.service import ExportService
    from grimoire.expressions.service import ExpressionStateService
    from grimoire.extractor.service import ExtractorService
    from grimoire.hud.config import HudConfigService
    from grimoire.hud.service import HudService
    from grimoire.imagegen import (
        ImageGenHealthProber,
        ImageGenIntegration,
        ImageGenService,
    )
    from grimoire.library import LibraryService
    from grimoire.llm_gateway.gateway import LLMGatewayService
    from grimoire.mechanics import MechanicsService
    from grimoire.observability.service import ObservabilityService
    from grimoire.orchestrator.service import OrchestratorService
    from grimoire.plugins import PluginsService
    from grimoire.scenes import SceneManager
    from grimoire.scenes.indexer import SceneIndexer
    from grimoire.scenes.summary_jobs import RunningSummaryWorker
    from grimoire.state_store import (
        BackupScheduler,
        BodySummarizer,
        EmbeddingWorker,
        RetentionSweeper,
        StateStore,
        StateStoreConfig,
    )
    from grimoire.storage import Database
    from grimoire.time_engine.service import TimeEngineService
    from grimoire.time_engine.subscriber import TimeEngineSubscriber
    from grimoire.transient_state import TransientStateService
    from grimoire.watcher.watcher import FileWatcher
    from grimoire.world import WorldService
    from grimoire.world.calendar_service import CalendarService
    # ExtrasService — avoid name collision with the old ``extras`` dict
    from grimoire.extras import ExtrasService as _ExtrasService


@dataclass
class ServiceContainer:
    """Bag of services available to API routers."""

    # Infrastructure
    db: Database | None = None
    event_bus: EventBus | None = None
    stream: StreamManager | None = None

    # Core domain services
    library: LibraryService | None = None
    world: WorldService | None = None
    characters: CharactersService | None = None
    scenes: SceneManager | None = None
    continuity: ContinuityRegistry | None = None
    time_engine: TimeEngineService | None = None
    imagegen: ImageGenService | None = None
    export: ExportService | None = None
    mechanics: MechanicsService | None = None
    plugins: PluginsService | None = None
    state_store: StateStore | None = None
    orchestrator: OrchestratorService | None = None
    observability: ObservabilityService | None = None
    hud: HudService | None = None
    hud_config: HudConfigService | None = None
    transient_state: TransientStateService | None = None
    extras_service: _ExtrasService | None = None
    """``grimoire.extras.ExtrasService`` -- narrative extras CRUD + search."""
    calendar: CalendarService | None = None
    """``grimoire.world.calendar_service.CalendarService``: multi-calendar + holiday surface."""

    # LLM-adjacent services (previously in extras dict)
    llm_gateway: LLMGatewayService | None = None
    extractor: ExtractorService | None = None
    context_builder: ContextBuilderService | None = None

    # Background workers (previously in extras dict)
    file_watcher: FileWatcher | None = None
    scene_indexer: SceneIndexer | None = None
    embedding_worker: EmbeddingWorker | None = None
    body_summarizer: BodySummarizer | None = None
    retention_sweeper: RetentionSweeper | None = None
    backup_scheduler: BackupScheduler | None = None
    scene_summary_worker: RunningSummaryWorker | None = None

    # Integration subscribers (previously in extras dict)
    imagegen_integration: ImageGenIntegration | None = None
    imagegen_health_prober: ImageGenHealthProber | None = None
    characters_integration: CharactersIntegration | None = None
    time_engine_subscriber: TimeEngineSubscriber | None = None

    # Config / diagnostics (previously in extras dict)
    state_store_config: StateStoreConfig | None = None
    mechanics_rescan_error: str | None = None
    plugins_rescan_error: str | None = None

    # Lazy-init services (previously in extras dict)
    expressions: ExpressionStateService | None = None
    context_inspector: ContextInspector | None = None


__all__ = ["ServiceContainer"]
```

- [ ] **Step 2: Run ruff to verify syntax**

Run: `cd backend && uv run ruff check src/grimoire/api/container.py`
Expected: No errors (TYPE_CHECKING guards prevent circular imports)

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/api/container.py
git commit -m "refactor(container): type all ServiceContainer fields with concrete types"
```

---

### Task 2: Update deps.py with concrete return types

**Files:**
- Modify: `backend/src/grimoire/api/deps.py`

- [ ] **Step 1: Replace the full deps.py content**

```python
"""FastAPI dependency helpers.

Each helper pulls a service from the app's :class:`ServiceContainer` and
raises ``503 Service Unavailable`` when it's not wired up. Routers use the
``Annotated`` aliases at the bottom (``library: LibraryDep``) rather than
``Depends(...)`` in default positions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from grimoire.api.container import ServiceContainer

if TYPE_CHECKING:
    from grimoire.api.stream import StreamManager
    from grimoire.characters import CharactersService
    from grimoire.context.builder import ContextBuilderService
    from grimoire.continuity import ContinuityRegistry
    from grimoire.export.service import ExportService
    from grimoire.extractor.service import ExtractorService
    from grimoire.hud.service import HudService
    from grimoire.imagegen import ImageGenService
    from grimoire.library import LibraryService
    from grimoire.llm_gateway.gateway import LLMGatewayService
    from grimoire.mechanics import MechanicsService
    from grimoire.observability.service import ObservabilityService
    from grimoire.orchestrator.service import OrchestratorService
    from grimoire.plugins import PluginsService
    from grimoire.scenes import SceneManager
    from grimoire.state_store import StateStore
    from grimoire.time_engine.service import TimeEngineService
    from grimoire.transient_state import TransientStateService
    from grimoire.watcher.watcher import FileWatcher
    from grimoire.world import WorldService
    from grimoire.world.calendar_service import CalendarService
    # ExtrasService — avoid name collision with the old ``extras`` dict
    from grimoire.extras import ExtrasService as _ExtrasService


def get_container(request: Request) -> ServiceContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service container not initialised",
        )
    return container


def _require(container: ServiceContainer, name: str) -> Any:
    service = getattr(container, name, None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} service not configured",
        )
    return service


def get_library(request: Request) -> LibraryService:
    return _require(get_container(request), "library")


def get_world(request: Request) -> WorldService:
    return _require(get_container(request), "world")


def get_characters(request: Request) -> CharactersService:
    return _require(get_container(request), "characters")


def get_scenes(request: Request) -> SceneManager:
    return _require(get_container(request), "scenes")


def get_continuity(request: Request) -> ContinuityRegistry:
    return _require(get_container(request), "continuity")


def get_time_engine(request: Request) -> TimeEngineService:
    return _require(get_container(request), "time_engine")


def get_imagegen(request: Request) -> ImageGenService:
    return _require(get_container(request), "imagegen")


def get_export(request: Request) -> ExportService:
    return _require(get_container(request), "export")


def get_mechanics(request: Request) -> MechanicsService:
    return _require(get_container(request), "mechanics")


def get_plugins(request: Request) -> PluginsService:
    return _require(get_container(request), "plugins")


def get_state_store(request: Request) -> StateStore:
    return _require(get_container(request), "state_store")


def get_orchestrator(request: Request) -> OrchestratorService:
    return _require(get_container(request), "orchestrator")


def get_observability(request: Request) -> ObservabilityService:
    return _require(get_container(request), "observability")


def get_stream(request: Request) -> StreamManager:
    return _require(get_container(request), "stream")


def get_transient_state(request: Request) -> TransientStateService:
    return _require(get_container(request), "transient_state")


def get_extras_service(request: Request) -> _ExtrasService:
    return _require(get_container(request), "extras_service")


def get_calendar(request: Request) -> CalendarService:
    return _require(get_container(request), "calendar")


def get_llm_gateway(request: Request) -> LLMGatewayService:
    return _require(get_container(request), "llm_gateway")


def get_file_watcher(request: Request) -> FileWatcher:
    return _require(get_container(request), "file_watcher")


ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
LibraryDep = Annotated[LibraryService, Depends(get_library)]
WorldDep = Annotated[WorldService, Depends(get_world)]
CharactersDep = Annotated[CharactersService, Depends(get_characters)]
ScenesDep = Annotated[SceneManager, Depends(get_scenes)]
ContinuityDep = Annotated[ContinuityRegistry, Depends(get_continuity)]
TimeEngineDep = Annotated[TimeEngineService, Depends(get_time_engine)]
ImageGenDep = Annotated[ImageGenService, Depends(get_imagegen)]
ExportDep = Annotated[ExportService, Depends(get_export)]
MechanicsDep = Annotated[MechanicsService, Depends(get_mechanics)]
PluginsDep = Annotated[PluginsService, Depends(get_plugins)]
StateStoreDep = Annotated[StateStore, Depends(get_state_store)]
OrchestratorDep = Annotated[OrchestratorService, Depends(get_orchestrator)]
ObservabilityDep = Annotated[ObservabilityService, Depends(get_observability)]
StreamDep = Annotated[StreamManager, Depends(get_stream)]
TransientStateDep = Annotated[TransientStateService, Depends(get_transient_state)]
ExtrasServiceDep = Annotated[_ExtrasService, Depends(get_extras_service)]
LLMGatewayDep = Annotated[LLMGatewayService, Depends(get_llm_gateway)]
FileWatcherDep = Annotated[FileWatcher, Depends(get_file_watcher)]
CalendarDep = Annotated[CalendarService, Depends(get_calendar)]


__all__ = [
    "CalendarDep",
    "CharactersDep",
    "ContainerDep",
    "ContinuityDep",
    "ExportDep",
    "ExtrasServiceDep",
    "FileWatcherDep",
    "ImageGenDep",
    "LLMGatewayDep",
    "LibraryDep",
    "MechanicsDep",
    "ObservabilityDep",
    "OrchestratorDep",
    "PluginsDep",
    "ScenesDep",
    "StateStoreDep",
    "StreamDep",
    "TimeEngineDep",
    "TransientStateDep",
    "WorldDep",
    "get_calendar",
    "get_characters",
    "get_container",
    "get_continuity",
    "get_export",
    "get_extras_service",
    "get_file_watcher",
    "get_imagegen",
    "get_library",
    "get_llm_gateway",
    "get_mechanics",
    "get_observability",
    "get_orchestrator",
    "get_plugins",
    "get_scenes",
    "get_state_store",
    "get_stream",
    "get_time_engine",
    "get_transient_state",
    "get_world",
]
```

- [ ] **Step 2: Run ruff to verify**

Run: `cd backend && uv run ruff check src/grimoire/api/deps.py`
Expected: No errors

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/api/deps.py
git commit -m "refactor(deps): return concrete types from dependency helpers"
```

---

### Task 3: Update main.py — replace all extras access in lifespan

**Files:**
- Modify: `backend/src/grimoire/main.py`

- [ ] **Step 1: Replace all `container.extras[...]` assignments in `lifespan()`**

Apply these replacements throughout the `lifespan()` function (lines ~149-670). Each is a mechanical find-and-replace:

| Find | Replace |
|------|---------|
| `container.extras["state_store_config"] = state_store_config` | `container.state_store_config = state_store_config` |
| `container.extras["mechanics_rescan_error"] = None` | `container.mechanics_rescan_error = None` |
| `container.extras["mechanics_rescan_error"] = f"{type(exc).__name__}: {exc}"` | `container.mechanics_rescan_error = f"{type(exc).__name__}: {exc}"` |
| `container.extras["plugins_rescan_error"] = None` | `container.plugins_rescan_error = None` |
| `container.extras["plugins_rescan_error"] = f"{type(exc).__name__}: {exc}"` | `container.plugins_rescan_error = f"{type(exc).__name__}: {exc}"` |
| `container.extras.get("scene_indexer") is None` | `container.scene_indexer is None` |
| `container.extras["scene_indexer"] = scene_indexer` | `container.scene_indexer = scene_indexer` |
| `container.extras.get("imagegen_integration") is None` | `container.imagegen_integration is None` |
| `container.extras["imagegen_integration"] = integration` | `container.imagegen_integration = integration` |
| `container.extras.get("imagegen_health_prober") is None` | `container.imagegen_health_prober is None` |
| `container.extras["imagegen_health_prober"] = prober` | `container.imagegen_health_prober = prober` |
| `container.extras.get("llm_gateway") is None` | `container.llm_gateway is None` |
| `container.extras["llm_gateway"] = LLMGatewayService(` | `container.llm_gateway = LLMGatewayService(` |
| `await container.extras["llm_gateway"].register_with_health_monitor()` | `await container.llm_gateway.register_with_health_monitor()` |
| `await container.extras["llm_gateway"].register_provider_defaults()` | `await container.llm_gateway.register_provider_defaults()` |
| `llm_gateway = container.extras["llm_gateway"]` | `llm_gateway = container.llm_gateway` |
| `container.extras.get("scene_summary_worker") is None` | `container.scene_summary_worker is None` |
| `container.extras["scene_summary_worker"] = worker` | `container.scene_summary_worker = worker` |
| `container.extras.get("extractor") is None` | `container.extractor is None` |
| `container.extras["extractor"] = ExtractorService(` | `container.extractor = ExtractorService(` |
| `extractor = container.extras["extractor"]` | `extractor = container.extractor` |
| `container.extras.get("context_builder") is None` | `container.context_builder is None` |
| `container.extras["context_builder"] = ContextBuilderService(` | `container.context_builder = ContextBuilderService(` |
| `context_builder = container.extras["context_builder"]` | `context_builder = container.context_builder` |
| `container.extras.get("time_engine_subscriber") is None` | `container.time_engine_subscriber is None` |
| `container.extras["time_engine_subscriber"] = subscriber` | `container.time_engine_subscriber = subscriber` |
| `container.extras.get("characters_integration") is None` | `container.characters_integration is None` |
| `container.extras["characters_integration"] = chars_integration` | `container.characters_integration = chars_integration` |
| `container.extras["file_watcher"] = file_watcher` | `container.file_watcher = file_watcher` |
| `container.extras["embedding_worker"] = embedding_worker` | `container.embedding_worker = embedding_worker` |
| `container.extras["body_summarizer"] = body_summarizer` | `container.body_summarizer = body_summarizer` |
| `container.extras["retention_sweeper"] = retention_sweeper` | `container.retention_sweeper = retention_sweeper` |
| `container.extras["backup_scheduler"] = backup_scheduler` | `container.backup_scheduler = backup_scheduler` |

Also replace `extras=container.extras_service,` with just `extras=container.extras_service,` on line ~380 — this one is already correct since it references the `extras_service` field, not the dict. Verify it reads `container.extras_service` and not `container.extras`.

- [ ] **Step 2: Replace all `container.extras.get(...)` in `_shutdown()`**

In the `_shutdown()` function (lines ~673-777), replace every `container.extras.get("name") if container.extras else None` with `container.name`:

| Find | Replace |
|------|---------|
| `container.extras.get("scene_summary_worker") if container.extras else None` | `container.scene_summary_worker` |
| `container.extras.get("scene_indexer") if container.extras else None` | `container.scene_indexer` |
| `container.extras.get("time_engine_subscriber") if container.extras else None` | `container.time_engine_subscriber` |
| `container.extras.get("backup_scheduler") if container.extras else None` | `container.backup_scheduler` |
| `container.extras.get("retention_sweeper") if container.extras else None` | `container.retention_sweeper` |
| `container.extras.get("body_summarizer") if container.extras else None` | `container.body_summarizer` |
| `container.extras.get("embedding_worker") if container.extras else None` | `container.embedding_worker` |
| `container.extras.get("imagegen_integration") if container.extras else None` | `container.imagegen_integration` |
| `container.extras.get("characters_integration") if container.extras else None` | `container.characters_integration` |
| `container.extras.get("imagegen_health_prober") if container.extras else None` | `container.imagegen_health_prober` |

Each shutdown block previously had a guard `if container.extras else None` because `extras` could be `None`. With typed fields, the default is already `None`, so `container.name` returns `None` if unset — no guard needed.

- [ ] **Step 3: Run ruff to verify**

Run: `cd backend && uv run ruff check src/grimoire/main.py`
Expected: No errors

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/main.py
git commit -m "refactor(main): replace container.extras dict with typed field access"
```

---

### Task 4: Update API routers that access extras directly

**Files:**
- Modify: `backend/src/grimoire/api/expressions.py`
- Modify: `backend/src/grimoire/api/context.py`

- [ ] **Step 1: Update expressions.py lazy-init pattern**

In `backend/src/grimoire/api/expressions.py`, replace `_get_expression_service` (lines 56-66):

```python
def _get_expression_service(request: Request) -> ExpressionStateService:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(status_code=503, detail="container not initialised")
    svc = container.expressions
    if svc is None:
        if container.db is None:
            raise HTTPException(status_code=503, detail="database not initialised")
        svc = ExpressionStateService(container.db)
        container.expressions = svc
    return svc
```

- [ ] **Step 2: Update context.py inspector getter**

In `backend/src/grimoire/api/context.py`, replace `_get_inspector` (lines 28-36):

```python
def _get_inspector(request: Request) -> ContextInspector:
    container: ServiceContainer = get_container(request)
    inspector = container.context_inspector
    if inspector is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="context inspector service not configured",
        )
    return inspector
```

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/api/expressions.py backend/src/grimoire/api/context.py
git commit -m "refactor(api): replace extras dict access in expressions and context routers"
```

---

### Task 5: Update testing/scenario.py

**Files:**
- Modify: `backend/src/grimoire/testing/scenario.py`

- [ ] **Step 1: Replace extras assignment**

In `backend/src/grimoire/testing/scenario.py`, line 134, replace:

```python
self.container.extras["llm_gateway"] = self._llm
```

with:

```python
self.container.llm_gateway = self._llm
```

Also update the comment on line ~124 from `container.extras.get("llm_gateway")` to `container.llm_gateway`.

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/testing/scenario.py
git commit -m "refactor(testing): use typed llm_gateway field in ScenarioApp"
```

---

### Task 6: Update test fixtures

**Files:**
- Modify: `backend/tests/api/test_campaign_settings_routes.py`
- Modify: `backend/tests/api/test_campaigns_routes.py`
- Modify: `backend/tests/api/test_context_routes.py`
- Modify: `backend/tests/api/test_library_routes.py`

- [ ] **Step 1: Update test_campaign_settings_routes.py**

Replace lines 67-69:

```python
    if container.extras is None:
        container.extras = {}
    container.extras["llm_gateway"] = LLMGatewayService(
```

with:

```python
    container.llm_gateway = LLMGatewayService(
```

- [ ] **Step 2: Update test_campaigns_routes.py**

Replace line 818:
```python
    container.extras["file_watcher"] = fw
```
with:
```python
    container.file_watcher = fw
```

Replace line 826:
```python
    container.extras.pop("file_watcher", None)
```
with:
```python
    container.file_watcher = None
```

- [ ] **Step 3: Update test_context_routes.py**

Replace line 48:
```python
    container.extras["context_inspector"] = inspector
```
with:
```python
    container.context_inspector = inspector
```

- [ ] **Step 4: Update test_library_routes.py**

Replace line 490:
```python
    container.extras["file_watcher"] = fw
```
with:
```python
    container.file_watcher = fw
```

Replace line 498:
```python
    container.extras.pop("file_watcher", None)
```
with:
```python
    container.file_watcher = None
```

- [ ] **Step 5: Search for any remaining extras access in tests**

Run: `cd backend && grep -rn "container\.extras" tests/`
Expected: Zero hits. If any remain, apply the same pattern: `container.extras["name"]` → `container.name`, `container.extras.pop("name", None)` → `container.name = None`.

- [ ] **Step 6: Commit**

```
git add backend/tests/
git commit -m "refactor(tests): use typed container fields instead of extras dict"
```

---

### Task 7: Verify — grep, ruff, pytest

- [ ] **Step 1: Grep for any remaining extras references in source**

Run: `cd backend && grep -rn "container\.extras" src/grimoire/`
Expected: Zero hits. The only acceptable match is `container.extras_service` (the typed field for ExtrasService), which does NOT contain a bracket/dot access after `extras_service`.

If hits remain, fix them using the same pattern from Tasks 3-6.

- [ ] **Step 2: Grep for extras field in container.py**

Run: `cd backend && grep -n "extras" src/grimoire/api/container.py`
Expected: Only `extras_service` field references. No `extras: dict` or bare `extras` field.

- [ ] **Step 3: Run ruff check**

Run: `cd backend && uv run ruff check src/grimoire/ tests/`
Expected: Pass (pre-existing issues in tests may remain — those are addressed in PR 2)

- [ ] **Step 4: Run ruff format check**

Run: `cd backend && uv run ruff format --check src/grimoire/ tests/`
Expected: Pass. If formatting diffs exist, run `uv run ruff format src/grimoire/ tests/` and commit the result.

- [ ] **Step 5: Run full pytest suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All tests pass. This is the primary verification that the refactor didn't break anything.

- [ ] **Step 6: Final commit if any formatting changes were needed**

```
git add -A
git commit -m "style: apply ruff formatting after container typing refactor"
```
