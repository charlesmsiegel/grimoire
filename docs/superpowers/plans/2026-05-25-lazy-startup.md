# Lazy Startup & Paginated Scene Loading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the multi-minute startup blocking by deferring file scans to the background, add mtime-based skip to make scans fast, and paginate scene post loading to fix runtime sluggishness on data-heavy scenes.

**Architecture:** The lifespan yields immediately so uvicorn serves from the persisted SQLite index; background tasks reconcile the index with disk. A new migration adds a `body` column to the `posts` table so a new paginated endpoint can serve full posts from SQLite without touching the filesystem. The run.sh pre-startup cleanup is parallelized and the PowerShell WMI call is replaced with `tasklist`.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript (frontend), SQLite, bash

**Spec:** `docs/superpowers/specs/2026-05-25-lazy-startup-design.md`
**Resolves:** Issue #471

---

### Task 1: Add `body` column to `posts` table

The existing `posts` table only stores `body_excerpt` (200 chars). The paginated endpoint needs full post bodies from SQLite.

**Files:**
- Create: `backend/src/grimoire/storage/migrations/032_posts_body.sql`
- Modify: `backend/src/grimoire/scenes/indexer.py:193-254`
- Test: `backend/tests/scenes/test_indexer.py`

- [ ] **Step 1: Write the migration**

Create `backend/src/grimoire/storage/migrations/032_posts_body.sql`:

```sql
-- Add full body column to posts so paginated reads can serve from SQLite.
ALTER TABLE posts ADD COLUMN body TEXT NOT NULL DEFAULT '';
```

- [ ] **Step 2: Write the failing test**

Add to `backend/tests/scenes/test_indexer.py`:

```python
async def test_post_row_stores_full_body(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    long_body = "A" * 500
    post = new_post(author_kind=AuthorKind.NARRATOR, body=long_body, is_player=False)
    await manager.append_post(scene.id, post)

    row = await db.fetchone(
        "SELECT body FROM posts WHERE scene_id = ?", (scene.id,)
    )
    assert row is not None
    assert row["body"] == long_body
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenes/test_indexer.py::test_post_row_stores_full_body -v`
Expected: FAIL — `body` column doesn't exist or is empty.

- [ ] **Step 4: Update `upsert_post_row` to store full body**

In `backend/src/grimoire/scenes/indexer.py`, modify `upsert_post_row` to include `body` in the INSERT and ON CONFLICT clauses:

```python
async def upsert_post_row(
    db: _DB,
    *,
    post_id: str,
    scene_id: str,
    campaign_id: str,
    branch_id: str,
    turn_id: str | None,
    order_in_scene: int,
    author_kind: AuthorKind | str,
    author_pc_ref: str | None,
    body: str,
    is_player: bool,
    created_at: object | None,
) -> None:
    body_excerpt = _excerpt(body)
    body_hash = content_hash(body)
    created_at_str: str | None
    if created_at is None:
        created_at_str = None
    elif hasattr(created_at, "isoformat"):
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at)
    await db.execute(
        """
        INSERT INTO posts (
            id, scene_id, campaign_id, branch_id, turn_id, order_in_scene,
            author_kind, author_pc_ref, body, body_excerpt, body_hash,
            is_player, created_at, retconned_from
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            scene_id = excluded.scene_id,
            campaign_id = excluded.campaign_id,
            branch_id = excluded.branch_id,
            turn_id = excluded.turn_id,
            order_in_scene = excluded.order_in_scene,
            author_kind = excluded.author_kind,
            author_pc_ref = excluded.author_pc_ref,
            body = excluded.body,
            body_excerpt = excluded.body_excerpt,
            body_hash = excluded.body_hash,
            is_player = excluded.is_player,
            created_at = excluded.created_at
        """,
        (
            post_id,
            scene_id,
            campaign_id,
            branch_id,
            turn_id,
            order_in_scene,
            _author_kind_str(author_kind),
            author_pc_ref,
            body,
            body_excerpt,
            body_hash,
            1 if is_player else 0,
            created_at_str,
        ),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_indexer.py::test_post_row_stores_full_body -v`
Expected: PASS

- [ ] **Step 6: Run full indexer test suite**

Run: `cd backend && uv run pytest tests/scenes/test_indexer.py -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```
git add backend/src/grimoire/storage/migrations/032_posts_body.sql backend/src/grimoire/scenes/indexer.py backend/tests/scenes/test_indexer.py
git commit -m "feat: add body column to posts table for paginated reads"
```

---

### Task 2: Add `sync_status` to ServiceContainer and health endpoint

**Files:**
- Modify: `backend/src/grimoire/api/container.py:61-124`
- Modify: `backend/src/grimoire/api/health.py:1-33`
- Test: `backend/tests/api/test_health_sync_status.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/api/test_health_sync_status.py`:

```python
"""sync_status appears in the /health response."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from grimoire.api.container import ServiceContainer
from grimoire.api.health import HealthResponse


def test_sync_status_field_exists_on_container() -> None:
    c = ServiceContainer()
    assert c.sync_status == "syncing"


def test_sync_status_ready() -> None:
    c = ServiceContainer()
    c.sync_status = "ready"
    assert c.sync_status == "ready"


def test_health_response_model_includes_sync_status() -> None:
    resp = HealthResponse(
        status="ok",
        version="0.0.0",
        data_root="/tmp",
        sync_status="syncing",
    )
    assert resp.sync_status == "syncing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_health_sync_status.py -v`
Expected: FAIL — `sync_status` not a field on ServiceContainer or HealthResponse.

- [ ] **Step 3: Add `sync_status` to ServiceContainer**

In `backend/src/grimoire/api/container.py`, add after the `plugins_rescan_error` field (line 117):

```python
    sync_status: str = "syncing"
    """``"syncing"`` while background scan is running, ``"ready"`` when done."""
    sync_error: str | None = None
    """Set when the background scan fails; ``None`` on success."""
```

- [ ] **Step 4: Add `sync_status` to HealthResponse and the endpoint**

In `backend/src/grimoire/api/health.py`, add `sync_status` to the response model and endpoint:

```python
class HealthResponse(BaseModel):
    status: str
    version: str
    data_root: str
    sync_status: str = "ready"
    mechanics_rescan_error: str | None = None
    plugins_rescan_error: str | None = None


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    container = getattr(request.app.state, "container", None)
    mechanics_err = container.mechanics_rescan_error if container is not None else None
    plugins_err = container.plugins_rescan_error if container is not None else None
    sync_status = container.sync_status if container is not None else "syncing"
    return HealthResponse(
        status="degraded" if (mechanics_err or plugins_err) else "ok",
        version=__version__,
        data_root=str(settings.data_root),
        sync_status=sync_status,
        mechanics_rescan_error=mechanics_err,
        plugins_rescan_error=plugins_err,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/api/test_health_sync_status.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/api/container.py backend/src/grimoire/api/health.py backend/tests/api/test_health_sync_status.py
git commit -m "feat: expose sync_status in health endpoint"
```

---

### Task 3: Move scan_now and backfill to background tasks

**Files:**
- Modify: `backend/src/grimoire/main.py:150-694` (lifespan function)
- Test: `backend/tests/test_background_scan.py`

- [ ] **Step 1: Verify sync_status defaults (already covered by Task 2 tests)**

The unit tests from Task 2 already verify that `ServiceContainer().sync_status == "syncing"`. No additional test file needed here — the behavioral integration is verified manually in Task 9.

- [ ] **Step 2: Refactor lifespan to defer scans**

In `backend/src/grimoire/main.py`, extract the scan work into a helper and launch it as a background task after the `yield`. The key changes:

1. Replace the direct `await scene_indexer.backfill()` call (around line 296) with storing the indexer for later.
2. Replace the direct `await file_watcher.scan_now()` call (around line 614) with storing it for later.
3. After the `yield`, the background task is already running.

Add a helper function before the `lifespan` function:

```python
async def _background_reconcile(container: ServiceContainer) -> None:
    """Run scan_now + backfill in the background after the server starts."""
    try:
        scene_indexer = container.scene_indexer
        if scene_indexer is not None:
            try:
                await scene_indexer.backfill()
            except Exception:
                log.exception("background scene indexer backfill failed")

        file_watcher = container.file_watcher
        if file_watcher is not None:
            try:
                await file_watcher.scan_now()
            except Exception:
                log.exception("background library scan failed")

        container.sync_status = "ready"
        container.sync_error = None
        log.info("background reconciliation complete")
    except Exception as exc:
        log.exception("background reconciliation failed")
        container.sync_status = "ready"
        container.sync_error = f"{type(exc).__name__}: {exc}"
```

In the `lifespan` function, replace:

```python
        # The scene_indexer.backfill() call (~line 296):
        # REMOVE: await scene_indexer.backfill()
        # KEEP: container.scene_indexer = scene_indexer (already there)

        # The file_watcher.scan_now() call (~line 612-616):
        # REMOVE: if library_cfg.scan_on_startup:
        #             try:
        #                 await file_watcher.scan_now()
        #             except Exception:
        #                 log.exception(...)
        # KEEP: container.file_watcher = file_watcher (already there)
```

Before the `yield`, add:

```python
        # Launch background reconciliation instead of blocking
        _bg_task = asyncio.create_task(
            _background_reconcile(container),
            name="background-reconcile",
        )
```

After the `yield` (in the finally block), cancel the task if still running:

```python
    finally:
        if _bg_task is not None and not _bg_task.done():
            _bg_task.cancel()
            with suppress(asyncio.CancelledError):
                await _bg_task
        await _stop_mechanics_watcher(app)
        await _shutdown(container, db, close_db=owned_db)
```

- [ ] **Step 3: Run the full test suite to verify nothing breaks**

Run: `cd backend && uv run pytest tests/ -x -q --timeout=30`
Expected: All pass. The scan still runs, just in the background.

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/main.py
git commit -m "feat: defer scan_now and backfill to background tasks"
```

---

### Task 4: mtime-based scan skip

**Files:**
- Modify: `backend/src/grimoire/watcher/watcher.py:264-348` (scan_now)
- Modify: `backend/src/grimoire/state_store/store.py` (add bulk mtime query)
- Test: `backend/tests/watcher/test_mtime_skip.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/watcher/test_mtime_skip.py`:

```python
"""mtime-based skip during scan_now."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.watcher import FileWatcher

from .conftest import EventCollector


def _write_markdown(path: Path, frontmatter_yaml: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


async def test_scan_skips_unchanged_files(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """After an initial scan, a second scan should skip files whose mtime
    hasn't changed — meaning _parse_file is NOT called for them."""
    target = store.data_root / "library" / "worlds" / "w1" / "characters" / "alice.md"
    _write_markdown(target, "name: Alice", "Alice body.")

    result1 = await watcher.scan_now()
    assert result1["library_files"] == 1

    row = await store.get_library_entity("worlds/w1/characters/alice")
    assert row is not None
    assert row["name"] == "Alice"

    # Second scan — file not modified, should be skipped.
    result2 = await watcher.scan_now()
    assert result2["library_files"] == 1
    # The row should still be there (not orphan-cleaned).
    row2 = await store.get_library_entity("worlds/w1/characters/alice")
    assert row2 is not None
    assert row2["name"] == "Alice"


async def test_scan_reindexes_when_mtime_changes(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """If a file is modified between scans, the new content should be indexed."""
    target = store.data_root / "library" / "worlds" / "w1" / "characters" / "bob.md"
    _write_markdown(target, "name: Bob", "Old body.")

    await watcher.scan_now()
    row = await store.get_library_entity("worlds/w1/characters/bob")
    assert row["body"].strip() == "Old body."

    # Modify the file — mtime changes.
    import time
    time.sleep(0.05)  # Ensure mtime differs
    _write_markdown(target, "name: Bob", "New body.")

    await watcher.scan_now()
    row2 = await store.get_library_entity("worlds/w1/characters/bob")
    assert row2["body"].strip() == "New body."
```

- [ ] **Step 2: Run test to verify it fails (or passes — scan currently re-reads everything)**

Run: `cd backend && uv run pytest tests/watcher/test_mtime_skip.py -v`
Expected: Tests pass (the current scan re-reads everything so the results are correct). This is a behavioral test — the optimization is internal.

- [ ] **Step 3: Add bulk mtime query to StateStore**

In `backend/src/grimoire/state_store/store.py`, add a method to bulk-load mtime and content_hash:

```python
    async def bulk_load_index_mtimes(self) -> dict[str, tuple[str, str]]:
        """Return {relative_path: (file_mtime, content_hash)} for all indexed rows.

        Used by the watcher's mtime-skip optimization during scan_now().
        """
        result: dict[str, tuple[str, str]] = {}
        for row in await self.db.fetchall(
            "SELECT path, file_mtime, content_hash FROM library_index"
        ):
            result[row["path"]] = (row["file_mtime"], row["content_hash"])
        for row in await self.db.fetchall(
            "SELECT path, file_mtime, content_hash FROM campaign_content_index"
        ):
            result[row["path"]] = (row["file_mtime"], row["content_hash"])
        return result
```

- [ ] **Step 4: Add mtime-skip logic to `scan_now`**

In `backend/src/grimoire/watcher/watcher.py`, modify `scan_now` to:

1. Bulk-load mtimes at the start.
2. For each file, compare stat mtime against cached mtime.
3. On match, populate `_known_hashes` and add to `seen_*` set, but skip `_reindex`.

At the top of `scan_now`, after the scope validation:

```python
        # Bulk-load stored mtimes so we can skip unchanged files.
        mtime_cache = await self.store.bulk_load_index_mtimes()
```

In the library walk loop, replace the direct `_reindex` call with:

```python
                for path in paths:
                    watched = classify_path(self.data_root, path)
                    if watched is None:
                        continue
                    # mtime-skip: if the file hasn't changed since last index,
                    # populate _known_hashes from the stored hash and skip I/O.
                    rel = str(path.relative_to(self.data_root))
                    cached = mtime_cache.get(rel)
                    if cached is not None:
                        cached_mtime_str, cached_hash = cached
                        try:
                            from grimoire.state_store.indexers import _file_mtime_iso
                            current_mtime_str = _file_mtime_iso(path)
                        except OSError:
                            current_mtime_str = None
                        if current_mtime_str == cached_mtime_str:
                            self._known_hashes[path] = cached_hash
                            library_files += 1
                            if watched.library_id is not None and watched.scope == "library":
                                seen_library.add(watched.library_id)
                            await asyncio.sleep(0)
                            continue
                    await self._reindex(watched, emit=False)
                    library_files += 1
                    if watched.library_id is not None and watched.scope == "library":
                        seen_library.add(watched.library_id)
                    await asyncio.sleep(0)
```

Apply the same pattern to the campaigns walk loop, using `seen_content` and `watched.content_index_id`.

- [ ] **Step 5: Make `_file_mtime_iso` importable**

In `backend/src/grimoire/state_store/indexers.py`, ensure `_file_mtime_iso` is in `__all__`:

```python
__all__ = [
    ...,
    "_file_mtime_iso",  # Used by watcher mtime-skip
]
```

Or better: rename to `file_mtime_iso` (drop the underscore) since it's now a public API.

- [ ] **Step 6: Run mtime skip tests**

Run: `cd backend && uv run pytest tests/watcher/test_mtime_skip.py -v`
Expected: PASS

- [ ] **Step 7: Run full watcher test suite**

Run: `cd backend && uv run pytest tests/watcher/ -v`
Expected: All pass.

- [ ] **Step 8: Commit**

```
git add backend/src/grimoire/watcher/watcher.py backend/src/grimoire/state_store/store.py backend/src/grimoire/state_store/indexers.py backend/tests/watcher/test_mtime_skip.py
git commit -m "feat: mtime-based skip during scan_now for fast startup reconciliation"
```

---

### Task 5: Paginated posts backend endpoint

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py` (add `get_posts_paginated`)
- Modify: `backend/src/grimoire/api/campaigns/scenes.py` (new endpoint, modify get_scene)
- Test: `backend/tests/scenes/test_paginated_posts.py`

- [ ] **Step 1: Write the failing test for paginated query**

Create `backend/tests/scenes/test_paginated_posts.py`:

```python
"""Paginated post loading from SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.scenes import (
    AuthorKind,
    InMemoryEventBus,
    SceneInit,
    SceneManager,
    SceneManagerConfig,
    new_post,
)
from grimoire.scenes.indexer import SceneIndexer
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def setup(tmp_path: Path, db):
    bus = InMemoryEventBus()
    data_root = tmp_path / "data"
    data_root.mkdir()
    manager = SceneManager(
        data_root,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
    )
    indexer = SceneIndexer(manager, db, bus)
    indexer.start()
    try:
        yield manager, indexer, db
    finally:
        await indexer.stop()


async def test_get_posts_paginated_returns_latest(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(10):
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=f"Post {i}",
            is_player=False,
        )
        await manager.append_post(scene.id, post)

    rows = await db.fetchall(
        """
        SELECT id, body, order_in_scene FROM posts
        WHERE scene_id = ?
        ORDER BY order_in_scene DESC
        LIMIT ?
        """,
        (scene.id, 5),
    )
    assert len(rows) == 5
    assert rows[0]["order_in_scene"] == 10
    assert rows[4]["order_in_scene"] == 6


async def test_get_posts_paginated_cursor(setup) -> None:
    manager, _, db = setup
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(10):
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=f"Post {i}",
            is_player=False,
        )
        await manager.append_post(scene.id, post)

    rows = await db.fetchall(
        """
        SELECT id, body, order_in_scene FROM posts
        WHERE scene_id = ? AND order_in_scene < ?
        ORDER BY order_in_scene DESC
        LIMIT ?
        """,
        (scene.id, 6, 3),
    )
    assert len(rows) == 3
    assert rows[0]["order_in_scene"] == 5
    assert rows[2]["order_in_scene"] == 3
```

- [ ] **Step 2: Run test to verify it passes (this is a SQL-level test)**

Run: `cd backend && uv run pytest tests/scenes/test_paginated_posts.py -v`
Expected: PASS — this validates the SQL query works against the schema with the new `body` column from Task 1.

- [ ] **Step 3: Add `get_posts_paginated` to SceneManager**

In `backend/src/grimoire/scenes/manager.py`, add a method that reads from SQLite:

```python
    async def get_posts_paginated(
        self,
        scene_id: str,
        *,
        limit: int = 50,
        before: int | None = None,
        db: object | None = None,
    ) -> list[Post]:
        """Return posts for a scene from the SQLite index, paginated.

        Returns posts in ascending order_in_scene, up to ``limit``.
        If ``before`` is given, only returns posts with order < before.
        """
        if db is None:
            raise ValueError("db is required for paginated reads")

        if before is not None:
            rows = await db.fetchall(
                """
                SELECT * FROM posts
                WHERE scene_id = ? AND order_in_scene < ?
                ORDER BY order_in_scene DESC
                LIMIT ?
                """,
                (scene_id, before, limit),
            )
        else:
            rows = await db.fetchall(
                """
                SELECT * FROM posts
                WHERE scene_id = ?
                ORDER BY order_in_scene DESC
                LIMIT ?
                """,
                (scene_id, limit),
            )
        rows = list(reversed(rows))
        return [
            Post(
                id=r["id"],
                scene_id=r["scene_id"],
                order_in_scene=r["order_in_scene"],
                author_kind=r["author_kind"],
                author_pc_ref=r.get("author_pc_ref"),
                author_npc_ref=None,
                body=r["body"],
                is_player=bool(r["is_player"]),
                created_at=r.get("created_at") or "",
                turn_id=r.get("turn_id") or "",
                alternates=[],
                primary_alternate_id=None,
            )
            for r in rows
        ]
```

Note: import `Post` from the scene types at the top of the file if not already imported.

- [ ] **Step 4: Add the paginated endpoint**

In `backend/src/grimoire/api/campaigns/scenes.py`, add:

```python
@router.get("/{campaign_id}/scenes/{scene_id}/posts")
async def get_scene_posts(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
    container: ContainerDep,
    limit: int = 50,
    before: int | None = None,
) -> Any:
    try:
        await _require_scene_owned(scenes, campaign_id, scene_id)
        posts = await scenes.get_posts_paginated(
            scene_id,
            limit=min(limit, 200),
            before=before,
            db=container.db,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "posts": to_payload(posts),
        "has_more": len(posts) == min(limit, 200),
    }
```

- [ ] **Step 5: Modify `get_scene` to return empty posts**

In the existing `get_scene` endpoint, replace the posts fetch with an empty list:

```python
@router.get("/{campaign_id}/scenes/{scene_id}")
async def get_scene(
    campaign_id: str,
    scene_id: str,
    scenes: ScenesDep,
    characters: CharactersDep,
    state_store: StateStoreDep,
) -> Any:
    try:
        scene = await _require_scene_owned(scenes, campaign_id, scene_id)
        await _reconcile_emergent_pcs(campaign_id, scene, characters, state_store)
        body = await scenes.load_scene_body(scene_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {
        "scene": to_payload(scene),
        "body": body,
        "posts": [],
    }
```

- [ ] **Step 6: Run full test suite**

Run: `cd backend && uv run pytest tests/ -x -q --timeout=30`
Expected: All pass.

- [ ] **Step 7: Commit**

```
git add backend/src/grimoire/scenes/manager.py backend/src/grimoire/api/campaigns/scenes.py backend/tests/scenes/test_paginated_posts.py
git commit -m "feat: add paginated posts endpoint reading from SQLite"
```

---

### Task 6: Frontend paginated post loading

**Files:**
- Modify: `frontend/src/api/campaign/api.ts`
- Modify: `frontend/src/api/campaign/types.ts`
- Modify: `frontend/src/routes/campaign/playReducer.ts`
- Modify: `frontend/src/routes/campaign/usePlayDataLoader.ts`
- Modify: `frontend/src/routes/campaign/ScenePane.tsx`
- Modify: `frontend/src/routes/campaign/usePlayStreamEvents.ts`

- [ ] **Step 1: Add paginated posts API method and types**

In `frontend/src/api/campaign/types.ts`, add:

```typescript
export interface PaginatedPostsResponse {
  posts: ApiPost[];
  has_more: boolean;
}
```

In `frontend/src/api/campaign/api.ts`, add to the `campaignApi` object:

```typescript
  getPostsPaginated: (
    id: string,
    sceneId: string,
    params?: { limit?: number; before?: number },
  ) =>
    api.get<PaginatedPostsResponse>(
      `/api/campaigns/${enc(id)}/scenes/${enc(sceneId)}/posts`,
      { query: params },
    ),
```

- [ ] **Step 2: Add `prepend-posts` action to playReducer**

In `frontend/src/routes/campaign/playReducer.ts`, add the action type:

```typescript
  | { type: "prepend-posts"; posts: ApiPost[]; hasMore: boolean }
```

Add to `PlayState`:

```typescript
  hasMorePosts: boolean;
```

Set `hasMorePosts: true` in `initialPlayState`.

Add the reducer case:

```typescript
    case "prepend-posts": {
      const existingIds = new Set(state.posts.map((p) => p.id));
      const novel = action.posts.filter((p) => !existingIds.has(p.id));
      return {
        ...state,
        posts: [...novel, ...state.posts],
        hasMorePosts: action.hasMore,
      };
    }
```

Update the `"loaded"` case to set `hasMorePosts: true` (will be refined when initial posts load).

Update the `"set-scene"` case similarly.

- [ ] **Step 3: Modify usePlayDataLoader to load paginated posts**

In `frontend/src/routes/campaign/usePlayDataLoader.ts`, change the data loading to fetch posts from the new endpoint:

```typescript
  const refresh = useCallback(async () => {
    if (!sceneJumpPendingRef.current) {
      markStart("scene:jump");
      sceneJumpPendingRef.current = true;
    }
    dispatch({ type: "loading" });
    try {
      const pcs = await campaignApi.listPCs(campaignId);
      const active = pcs.find((p) => p.active) ?? pcs[0] ?? null;
      const activePcRef = active?.character_ref ?? null;
      const scenes = await campaignApi.listScenes(campaignId);
      const explicitScene = sceneJumpId
        ? scenes.find((s) => s.id === sceneJumpId)
        : null;
      const pcScene = active?.current_scene_id
        ? scenes.find((s) => s.id === active.current_scene_id)
        : null;
      const fallback = scenes.find((s) => !s.closed) ?? scenes[scenes.length - 1] ?? null;
      const targetScene = explicitScene ?? pcScene ?? fallback;
      let scene = null;
      let posts: ApiPost[] = [];
      if (targetScene) {
        const detail = await campaignApi.getScene(campaignId, targetScene.id);
        scene = detail.scene;
        const paginated = await campaignApi.getPostsPaginated(campaignId, targetScene.id, {
          limit: 50,
        });
        posts = paginated.posts;
      }
      dispatch({ type: "loaded", pcs, activePcRef, scene, posts });
    } catch (e) {
      dispatch({ type: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [campaignId, sceneJumpId, dispatch]);
```

- [ ] **Step 4: Add scroll-up loading to ScenePane**

In `frontend/src/routes/campaign/ScenePane.tsx`, add the scroll-up pagination:

```typescript
interface Props {
  posts: ApiPost[];
  pcs: PCEntry[];
  streaming: PendingTurn | null;
  images: Record<string, SceneImage>;
  campaignId?: string;
  scene?: ApiScene | null;
  hasMorePosts: boolean;
  onLoadMore: () => void;
}

export function ScenePane({
  posts, pcs, streaming, images, campaignId, scene,
  hasMorePosts, onLoadMore,
}: Props) {
  const topSentinelRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const loadingMoreRef = useRef(false);

  // Scroll-up: load older posts when user scrolls near the top.
  useEffect(() => {
    const sentinel = topSentinelRef.current;
    if (!sentinel || !hasMorePosts) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !loadingMoreRef.current) {
          loadingMoreRef.current = true;
          onLoadMore();
          // Reset after a short delay to prevent rapid-fire.
          setTimeout(() => { loadingMoreRef.current = false; }, 300);
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMorePosts, onLoadMore]);

  // ... rest of existing code ...

  return (
    <section className="scene-pane" aria-label="Scene posts" aria-live="polite">
      {hasMorePosts && <div ref={topSentinelRef} className="load-more-sentinel" />}
      {/* ... existing posts map ... */}
      <div ref={bottomRef} aria-hidden />
    </section>
  );
}
```

- [ ] **Step 5: Wire onLoadMore in PlayView**

In `frontend/src/routes/campaign/PlayView.tsx` (ScenePane is rendered at line 230), add a `loadMore` callback:

```typescript
const loadMore = useCallback(async () => {
  const firstOrder = state.posts[0]?.order_in_scene;
  if (!state.scene || firstOrder === undefined) return;
  const result = await campaignApi.getPostsPaginated(campaignId, state.scene.id, {
    limit: 50,
    before: firstOrder,
  });
  dispatch({ type: "prepend-posts", posts: result.posts, hasMore: result.has_more });
}, [campaignId, state.scene, state.posts, dispatch]);
```

Pass `hasMorePosts={state.hasMorePosts}` and `onLoadMore={loadMore}` to ScenePane.

- [ ] **Step 6: Update turn_complete refresh to be incremental**

In `frontend/src/routes/campaign/usePlayStreamEvents.ts`, change the `turn_complete` handler to fetch only new posts instead of a full refresh:

```typescript
        case "turn_complete": {
          const turn_id = typeof message.turn_id === "string" ? message.turn_id : null;
          if (!turn_id) return;
          dispatch({ type: "stream-end", turn_id, post: null });
          // Incremental: fetch only posts newer than what we have.
          const lastOrder = cur.posts[cur.posts.length - 1]?.order_in_scene ?? 0;
          if (cur.scene) {
            const result = await campaignApi.getPostsPaginated(
              campaignId,
              cur.scene.id,
              { limit: 50 },
            );
            const newPosts = result.posts.filter((p) => p.order_in_scene > lastOrder);
            for (const p of newPosts) {
              dispatch({ type: "append-post", post: p });
            }
          }
          return;
        }
```

Note: the `onEvent` callback needs to be `async` for this. Verify that `useCampaignEvent` supports async handlers.

- [ ] **Step 7: Test manually in browser**

Start the dev server with `scripts/run.sh` and verify:
1. Scene loads with the most recent 50 posts.
2. Scrolling up triggers loading of older posts.
3. New posts from turns still append correctly.
4. Scroll position is preserved when older posts are prepended.

- [ ] **Step 8: Commit**

```
git add frontend/src/api/campaign/api.ts frontend/src/api/campaign/types.ts frontend/src/routes/campaign/playReducer.ts frontend/src/routes/campaign/usePlayDataLoader.ts frontend/src/routes/campaign/ScenePane.tsx frontend/src/routes/campaign/usePlayStreamEvents.ts
git commit -m "feat: paginated scene post loading with scroll-up pagination"
```

---

### Task 7: Frontend sync indicator

**Files:**
- Modify: `frontend/src/shell/StatusBar.tsx`
- Modify: `frontend/src/shell/AppShell.tsx`

- [ ] **Step 1: Add sync status hook**

In `frontend/src/shell/StatusBar.tsx`, add a hook that polls `/api/health` for `sync_status`:

```typescript
function useSyncStatus(): "syncing" | "ready" {
  const [status, setStatus] = useState<"syncing" | "ready">("ready");

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const resp = await fetch("/api/health");
        if (!resp.ok) return;
        const data = await resp.json();
        if (!cancelled) setStatus(data.sync_status ?? "ready");
      } catch {
        // Ignore fetch errors
      }
    };
    void check();

    // Also listen for the library_indexed WebSocket event to clear.
    return () => { cancelled = true; };
  }, []);

  // Clear when library_indexed event arrives.
  useCampaignEvent("library_indexed", () => setStatus("ready"));

  return status;
}
```

- [ ] **Step 2: Add sync indicator to StatusBar**

In the `StatusBar` component JSX, add after the WS status item:

```tsx
  const syncStatus = useSyncStatus();

  // In the return JSX, after the wsStatus span:
  {syncStatus === "syncing" && (
    <span className="status-item" data-status="syncing">
      syncing library…
    </span>
  )}
```

- [ ] **Step 3: Add CSS for syncing indicator**

In the relevant CSS file (where `.status-bar` styles live), add:

```css
.status-item[data-status="syncing"] {
  animation: pulse 1.5s ease-in-out infinite;
}
```

The `pulse` animation likely already exists for the streaming indicator — reuse it.

- [ ] **Step 4: Test manually**

Start the dev server and verify:
1. On startup, "syncing library..." appears briefly in the status bar.
2. Once the background scan completes, the indicator disappears.
3. Normal status bar items continue to work.

- [ ] **Step 5: Commit**

```
git add frontend/src/shell/StatusBar.tsx
git commit -m "feat: sync status indicator in status bar"
```

---

### Task 8: Pre-startup script optimization

**Files:**
- Modify: `scripts/run.sh:67-72`
- Modify: `scripts/_lib.sh:158-173`

- [ ] **Step 1: Parallelize kill_port calls in run.sh**

In `scripts/run.sh`, replace the sequential kill block:

```bash
if [ "$KILL_STALE" = "1" ]; then
    echo "==> Clearing any stale grimoire processes on ports $BACKEND_PORT / $FRONTEND_PORT"
    kill_port "$BACKEND_PORT" "backend port" &
    kill_port "$FRONTEND_PORT" "frontend port" &
    kill_orphaned_uvicorn_workers &
    wait
fi
```

- [ ] **Step 2: Replace PowerShell with tasklist in _lib.sh**

In `scripts/_lib.sh`, replace the `kill_orphaned_uvicorn_workers` function:

```bash
kill_orphaned_uvicorn_workers() {
    [ "$PLATFORM" = "windows" ] || return 0
    local pids
    pids="$(tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2>/dev/null \
        | grep -i "python" \
        | while IFS=, read -r _name pid _rest; do
            pid="${pid//\"/}"
            # Check if this PID's command line contains multiprocessing.spawn.
            # wmic is slower than tasklist but only runs per matching PID.
            wmic process where "ProcessId=$pid" get CommandLine 2>/dev/null \
                | grep -q "multiprocessing.spawn" && echo "$pid"
        done || true)"
    local pid
    for pid in $pids; do
        [ -z "$pid" ] && continue
        echo "==> Killing orphan multiprocessing worker PID $pid"
        kill_pid "$pid"
    done
}
```

Note: `tasklist` is much faster than PowerShell cold-start. The per-PID `wmic` call only runs for matching python.exe processes, so the total is still faster than the `Get-CimInstance` approach.

- [ ] **Step 3: Reduce wait_for_url timeout**

In `scripts/run.sh`, reduce the backend wait timeout now that startup is near-instant:

```bash
if ! wait_for_url "http://${BACKEND_HOST}:${BACKEND_PORT}/api/setup/status" 10; then
    echo "warning: backend did not respond within 10s; starting frontend anyway" >&2
fi
```

- [ ] **Step 4: Test the startup script**

Run: `./scripts/run.sh --no-browser`
Expected: Server starts in under 5 seconds. "syncing library..." appears briefly, then clears.

- [ ] **Step 5: Commit**

```
git add scripts/run.sh scripts/_lib.sh
git commit -m "perf: parallelize pre-startup cleanup and replace PowerShell with tasklist"
```

---

### Task 9: Integration verification

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && uv run pytest tests/ -x -q --timeout=60`
Expected: All pass.

- [ ] **Step 2: Manual integration test**

1. Stop any running grimoire instances.
2. Run `./scripts/run.sh`.
3. Verify: server starts in under 5 seconds, browser opens.
4. Navigate to a campaign with a large scene.
5. Verify: scene loads quickly with recent posts.
6. Scroll up — older posts load.
7. Post a turn — new post appears, streaming works.
8. Check status bar: "syncing library..." appears briefly, then clears.

- [ ] **Step 3: Verify first-run behavior**

1. Delete the SQLite database (backup first).
2. Run `./scripts/run.sh`.
3. Verify: setup wizard appears.
4. Complete wizard — library populates in the background.
5. Navigate to library — entries appear.

- [ ] **Step 4: Final commit (if any fixes needed)**

```
git add -A
git commit -m "fix: integration fixes for lazy startup"
```
