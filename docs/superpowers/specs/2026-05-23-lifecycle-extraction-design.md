# main.py Lifecycle Extraction

Date: 2026-05-23
Status: Approved
PR: 3 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 2 (Error handling)

## Problem

`backend/src/grimoire/main.py` contains an 860-line module where `lifespan()` alone is ~520 lines. It constructs ~20 services, starts ~10 background workers, patches private attributes on already-constructed services, and shuts everything down. The shutdown function repeats the same try/except/log pattern 12+ times.

Specific issues:
- Private-field patching: `container.scenes._continuity`, `container.state_store._metrics`, `container.scenes._summarizer`, `container.world.gateway`
- Initialization order is implicit in code position, not declared
- `file_watcher` is created conditionally inside `if library_cfg.watch`, but `EmbeddingWorker` and `BodySummarizer` unconditionally reference `file_watcher.embedding_queue` and `file_watcher.summary_queue`
- Shutdown has 12 nearly identical try/except blocks

## Solution

1. Extract service construction into a `build_services()` function.
2. Extract a `LifecycleManager` that owns start/stop for background workers.
3. Replace private-attribute patching with explicit setter methods or constructor params.
4. Fix the `file_watcher` conditional bug by always creating queue objects.

## Detailed Design

### Step 1: Extract Queue Objects from FileWatcher

Create a standalone `QueueBundle` that owns the embedding and summary queues. `FileWatcher` receives the bundle; `EmbeddingWorker` and `BodySummarizer` receive it independently. This breaks the coupling where workers depend on the watcher existing.

```python
@dataclass
class QueueBundle:
    embedding: EmbeddingQueue
    summary: SummaryQueue

    @classmethod
    def create(cls) -> QueueBundle:
        return cls(embedding=EmbeddingQueue(), summary=SummaryQueue())
```

`FileWatcher.__init__` takes `queues: QueueBundle` instead of owning the queues. Workers take `queue: EmbeddingQueue` / `queue: SummaryQueue` directly.

### Step 2: Replace Private-Attribute Patching

Add explicit setter methods to services that currently get patched:

| Current Patch | Replacement |
|---------------|-------------|
| `container.scenes._continuity = registry` | `SceneManager.set_continuity(registry)` |
| `container.scenes._summarizer = ...` | `SceneManager.set_summarizer(summarizer)` |
| `container.scenes._final_summarizer = ...` | `SceneManager.set_final_summarizer(summarizer)` |
| `container.scenes._metrics = obs.metrics()` | `SceneManager.set_metrics(metrics)` |
| `container.state_store._metrics = obs.metrics()` | `StateStore.set_metrics(metrics)` |
| `container.imagegen._metrics = obs.metrics()` | `ImageGenService.set_metrics(metrics)` |
| `container.world.gateway = llm_gateway` | `WorldService.set_gateway(gateway)` |
| `registry._embedder = llm_gateway` | `ContinuityRegistry.set_embedder(embedder)` |
| `registry._judge_gateway = llm_gateway` | `ContinuityRegistry.set_judge(gateway, factory)` |

Each setter validates the argument type and is safe to call multiple times (idempotent).

### Step 3: Extract LifecycleManager

Define a `Stoppable` protocol and a manager that handles start/stop:

```python
class Stoppable(Protocol):
    async def stop(self) -> None: ...

class SyncStoppable(Protocol):
    def stop(self) -> None: ...

class LifecycleManager:
    def __init__(self) -> None:
        self._async_stoppables: list[tuple[str, Stoppable]] = []
        self._sync_stoppables: list[tuple[str, SyncStoppable]] = []

    def register_async(self, name: str, stoppable: Stoppable) -> None: ...
    def register_sync(self, name: str, stoppable: SyncStoppable) -> None: ...

    async def stop_all(self) -> None:
        # Stops in reverse registration order, each wrapped in try/except/log
```

Services that need lifecycle management:
- **Async stop:** `scene_summary_worker`, `scene_indexer`, `retention_sweeper`, `body_summarizer`, `embedding_worker`, `observability`, `imagegen_health_prober`, `plugins.stop_periodic_health`, `imagegen.aclose`, `stream.aclose`
- **Sync stop:** `time_engine_subscriber`, `backup_scheduler`, `imagegen_integration`, `characters_integration`

`LifecycleManager` becomes a typed field on `ServiceContainer` (from PR 1).

### Step 4: Extract build_services()

Split the lifespan function into phases:

```python
async def build_storage(settings, container) -> Database: ...
async def build_content_services(settings, container, db) -> None: ...
async def build_llm_services(settings, container, db) -> None: ...
async def build_play_services(settings, container) -> None: ...
async def start_background_workers(settings, container, lifecycle) -> None: ...
```

Each function takes the container and populates its fields. The `lifespan()` function becomes:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = getattr(app.state, "container", None) or ServiceContainer()
    lifecycle = LifecycleManager()
    db = await build_storage(settings, container)
    try:
        await build_content_services(settings, container, db)
        await build_llm_services(settings, container, db)
        await build_play_services(settings, container)
        await start_background_workers(settings, container, lifecycle)
        container.lifecycle = lifecycle
        app.state.container = container
    except Exception:
        await lifecycle.stop_all()
        await db.close()
        raise
    try:
        yield
    finally:
        await lifecycle.stop_all()
        await db.close()
```

### File Organization

New file: `backend/src/grimoire/lifecycle.py` containing `Stoppable`, `SyncStoppable`, `LifecycleManager`, `QueueBundle`.

Build functions can live in `main.py` or be extracted to `backend/src/grimoire/bootstrap.py` if `main.py` is still too large after the split.

## Scope

### In scope
- Extract `QueueBundle` to decouple workers from file watcher
- Add setter methods to 6 services (replacing private-attribute patches)
- Extract `LifecycleManager` with `Stoppable`/`SyncStoppable` protocols
- Split `lifespan()` into phase functions
- Fix the `file_watcher` conditional queue bug
- Run independent startup tasks concurrently within `start_background_workers()` (health registrations, non-critical rescans, backfills can run via `asyncio.gather` since they don't depend on each other)

### Not in scope
- Changing service construction signatures (deferred to service split PRs)
- Adding dependency graph validation
- Lazy service construction
- Changing how tests wire containers

## Verification

1. `ruff check` and `ruff format --check` pass.
2. `pytest` full suite passes.
3. Zero private attribute access (`_continuity`, `_summarizer`, `_metrics`, etc.) from `main.py`.
4. `lifespan()` is under 50 lines.
5. `_shutdown()` function is removed (replaced by `LifecycleManager.stop_all()`).
6. Starting the app with `library_cfg.watch = false` does not crash.
