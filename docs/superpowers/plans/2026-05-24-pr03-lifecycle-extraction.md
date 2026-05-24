# PR 3: Lifecycle Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract service construction and lifecycle management out of `main.py`'s 500-line `lifespan()` function. Introduce `QueueBundle` (decouples workers from file watcher), setter methods (replaces private-attribute patching), `LifecycleManager` (replaces 12 try/except/log shutdown blocks), and phase-based build functions.

**Architecture:** New `lifecycle.py` module contains `Stoppable`, `SyncStoppable`, `LifecycleManager`, and `QueueBundle`. The `lifespan()` function delegates to `build_storage()`, `build_content_services()`, `build_llm_services()`, `build_play_services()`, and `start_background_workers()`. Shutdown becomes `lifecycle.stop_all()`. Services that were previously patched via private attributes get explicit `set_X()` methods.

**Tech Stack:** Python 3.12+, FastAPI, asyncio

---

### Task 1: Create lifecycle.py with Stoppable protocols and LifecycleManager

**Files:**
- Create: `backend/src/grimoire/lifecycle.py`
- Test: `backend/tests/test_lifecycle.py`

- [ ] **Step 1: Write tests for LifecycleManager**

```python
"""Tests for lifecycle management."""

import asyncio
import pytest
from grimoire.lifecycle import LifecycleManager


class _AsyncStopper:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _SyncStopper:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FailingStopper:
    async def stop(self):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_stop_all_stops_in_reverse_order():
    lm = LifecycleManager()
    order = []
    class A:
        async def stop(self):
            order.append("a")
    class B:
        def stop(self):
            order.append("b")
    lm.register_async("a", A())
    lm.register_sync("b", B())
    await lm.stop_all()
    assert order == ["b", "a"]


@pytest.mark.asyncio
async def test_stop_all_continues_after_failure():
    lm = LifecycleManager()
    good = _AsyncStopper()
    lm.register_async("failing", _FailingStopper())
    lm.register_async("good", good)
    await lm.stop_all()
    assert good.stopped


@pytest.mark.asyncio
async def test_stop_all_handles_mixed_async_sync():
    lm = LifecycleManager()
    a = _AsyncStopper()
    s = _SyncStopper()
    lm.register_async("async", a)
    lm.register_sync("sync", s)
    await lm.stop_all()
    assert a.stopped
    assert s.stopped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_lifecycle.py -v`
Expected: FAIL — `grimoire.lifecycle` doesn't exist yet

- [ ] **Step 3: Implement lifecycle.py**

```python
"""Lifecycle management for background workers and services.

Provides ``LifecycleManager`` for orderly shutdown and ``QueueBundle``
for decoupling background workers from the file watcher.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Stoppable(Protocol):
    async def stop(self) -> None: ...


@runtime_checkable
class SyncStoppable(Protocol):
    def stop(self) -> None: ...


class LifecycleManager:
    """Tracks background workers/subscribers and stops them in reverse order."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, object, bool]] = []  # (name, obj, is_async)

    def register_async(self, name: str, stoppable: Stoppable) -> None:
        self._entries.append((name, stoppable, True))

    def register_sync(self, name: str, stoppable: SyncStoppable) -> None:
        self._entries.append((name, stoppable, False))

    async def stop_all(self) -> None:
        for name, obj, is_async in reversed(self._entries):
            try:
                if is_async:
                    await obj.stop()  # type: ignore[union-attr]
                else:
                    obj.stop()  # type: ignore[union-attr]
            except Exception:
                logger.exception("%s stop failed during shutdown", name)
        self._entries.clear()


@dataclass
class QueueBundle:
    """Owns embedding and summary queues independently of the file watcher.

    Created unconditionally at startup so background workers always have
    queues to drain, even when file watching is disabled.
    """

    embedding: asyncio.Queue = field(default_factory=asyncio.Queue)
    summary: asyncio.Queue = field(default_factory=asyncio.Queue)
```

Note: The actual `QueueBundle` queue types may need to match whatever the existing `EmbeddingQueue`/`SummaryQueue` types are in the codebase. Read `backend/src/grimoire/watcher/watcher.py` and `backend/src/grimoire/state_store/embedding_worker.py` to find the exact queue class used, and use that instead of bare `asyncio.Queue`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_lifecycle.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/lifecycle.py backend/tests/test_lifecycle.py
git commit -m "feat: add LifecycleManager and QueueBundle"
```

---

### Task 2: Add setter methods to services that are currently patched

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py` (add `set_continuity`, `set_summarizer`, `set_final_summarizer`, `set_metrics`)
- Modify: `backend/src/grimoire/state_store/store.py` (add `set_metrics`)
- Modify: `backend/src/grimoire/imagegen/service.py` (add `set_metrics`)
- Modify: `backend/src/grimoire/world/service.py` (add `set_gateway`)
- Modify: `backend/src/grimoire/continuity/__init__.py` (add `set_embedder`, `set_judge`)

- [ ] **Step 1: Add setter methods to each service**

For each service, add a public method that replaces the private-attribute patch. Example pattern:

```python
# In SceneManager:
def set_continuity(self, continuity: Any) -> None:
    self._continuity = continuity

def set_summarizer(self, summarizer: Any) -> None:
    self._summarizer = summarizer

def set_final_summarizer(self, summarizer: Any) -> None:
    self._final_summarizer = summarizer

def set_metrics(self, metrics: Any) -> None:
    self._metrics = metrics
```

Apply the same pattern to `StateStore.set_metrics()`, `ImageGenService.set_metrics()`, `WorldService.set_gateway()`, and `ContinuityRegistry.set_embedder()` / `ContinuityRegistry.set_judge()`.

Read each file to find the exact private attribute name and apply accordingly.

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/scenes/manager.py backend/src/grimoire/state_store/store.py backend/src/grimoire/imagegen/service.py backend/src/grimoire/world/service.py backend/src/grimoire/continuity/__init__.py
git commit -m "feat: add setter methods to replace private-attribute patching"
```

---

### Task 3: Extract build phase functions from lifespan

**Files:**
- Create: `backend/src/grimoire/bootstrap.py`
- Modify: `backend/src/grimoire/main.py`

- [ ] **Step 1: Create bootstrap.py with phase functions**

Extract the construction logic from `lifespan()` into named functions in a new `bootstrap.py`. Each function takes the container and settings and populates fields. Use the setter methods from Task 2 instead of private-attribute access.

The functions are:
- `async def build_storage(settings, container) -> Database`
- `async def build_content_services(settings, container, db) -> None`
- `async def build_llm_services(settings, container, db) -> None`
- `async def build_play_services(settings, container) -> None`
- `async def start_background_workers(settings, container, lifecycle) -> None`

Read `main.py` lines 149-600 carefully and move the construction logic into these functions. Replace every `container.scenes._continuity = ...` with `container.scenes.set_continuity(...)`. Replace every `container.extras["name"]` with `container.name` (already done in PR 1).

- [ ] **Step 2: Simplify lifespan() to call phase functions**

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = getattr(app.state, "container", None) or ServiceContainer()
    lifecycle = LifecycleManager()
    container.lifecycle = lifecycle

    if container.state_store is not None:
        db = container.state_store.db
        owned_db = False
    else:
        db = Database(
            settings.resolved_database_path,
            pool_size=settings.db_pool_size,
            enable_wal=settings.enable_wal,
        )
        await db.connect()
        owned_db = True

    try:
        await apply_migrations(db)
        container.db = db
        await build_content_services(settings, container, db)
        await build_llm_services(settings, container, db)
        await build_play_services(settings, container)
        await start_background_workers(settings, container, lifecycle)
        app.state.container = container
    except Exception:
        await lifecycle.stop_all()
        if owned_db:
            await db.close()
        raise

    try:
        yield
    finally:
        await lifecycle.stop_all()
        if owned_db:
            await db.close()
```

- [ ] **Step 3: Use concurrent startup for independent tasks in start_background_workers**

In `start_background_workers()`, group independent startup operations under `asyncio.gather()`:

```python
async def start_background_workers(settings, container, lifecycle):
    # ... create workers ...

    # Independent startup tasks can run concurrently
    await asyncio.gather(
        _safe_start("embedding_worker", embedding_worker.start()),
        _safe_start("scene_backfill", scene_indexer.backfill()),
        _safe_start("imagegen_reload", container.imagegen.reload_pending_jobs()),
        return_exceptions=True,
    )
```

- [ ] **Step 4: Run full test suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All tests pass

- [ ] **Step 5: Verify lifespan is small**

Run: `cd backend && grep -c "" src/grimoire/main.py`
Expected: Significantly fewer lines than before (~100-200 vs ~860)

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/bootstrap.py backend/src/grimoire/main.py
git commit -m "refactor(main): extract service construction into bootstrap phase functions"
```
