# Lazy Startup & Paginated Scene Loading

**Date:** 2026-05-25
**Resolves:** Issue #471
**Status:** Draft

## Problem

The backend blocks on two expensive I/O operations during the FastAPI lifespan before uvicorn can serve any request:

1. **`file_watcher.scan_now()`** — walks the entire data root (`~/.grimoire/`), reads every file, parses frontmatter, computes SHA-256 hashes, and upserts into SQLite. With 500+ files this takes minutes.
2. **`scene_indexer.backfill()`** — walks every campaign's `scenes/` directory (plus branches), reads each YAML sidecar twice, reads the markdown, deletes and re-inserts all post rows. Sequential, no parallelism.

Additionally, loading a scene with many posts is slow at runtime because `GET /{campaign_id}/scenes/{scene_id}` reads the entire markdown file from disk and returns all posts in a single unpaginated response.

The `run.sh` startup script adds 5-9 seconds of overhead before uvicorn even launches, due to sequential `kill_port` calls and a PowerShell/WMI query to find orphaned uvicorn workers.

## Design

### 1. Background Startup Reconciliation

**Current:** `main.py` lifespan awaits `scene_indexer.backfill()` (line 296) and `file_watcher.scan_now()` (line 614) sequentially before yielding control to uvicorn.

**Change:** Move both to `asyncio.create_task()` calls that run after the lifespan yields. The server starts immediately, serving data from the persisted SQLite index (`library_index`, `campaign_content_index`, `scenes`, `posts` tables all survive restarts).

**Readiness state:** Add a `sync_status` field to `ServiceContainer` with values `"syncing"` | `"ready"`. The field starts as `"syncing"` (before the lifespan yields, uvicorn is not serving requests, so no API-visible "starting" state is needed). It transitions to `"ready"` when both background scan tasks complete. Exposed via `/api/health` so the frontend can display a sync indicator.

**First-run behavior:** On a brand-new install the SQLite index is empty, so listings return `[]` until the background scan populates them. The setup wizard runs during this window, buying time. The existing `library_indexed` event (already emitted by `scan_now()`) signals completion.

**Error handling:** If the background scan fails, log the error and set `sync_status` to `"ready"` with an error flag. A failed scan is no worse than the current behavior where failure would prevent the server from starting at all.

### 2. mtime-Based Scan Optimization

**Current:** `_reindex()` in `watcher.py` always calls `_parse_file()` which reads the entire file, then computes a SHA-256 hash. On startup, the in-memory `_known_hashes` dict is empty, so every file is treated as new — every byte of every file is read, parsed, hashed, and upserted even if nothing changed since the last run.

**Change:** Add an mtime-based fast path at the top of `_reindex()` during scan mode (not live filesystem events, where mtime alone is not reliable for dedup):

1. Before calling `_parse_file()`, `stat()` the file to get its mtime.
2. Compare against the stored `file_mtime` in the existing index row (already persisted in both `library_index` and `campaign_content_index`).
3. If mtime matches, skip the file entirely — no read, no parse, no hash, no upsert.
4. If mtime differs or no prior row exists, proceed with the full pipeline.

**Why this is safe:** mtime is a reliable change signal on NTFS (Windows) and ext4/APFS. The only failure case — a file modified with its mtime explicitly reset — does not happen in normal usage. The full hash comparison remains authoritative for live watchdog events.

**Bootstrapping `_known_hashes`:** As a side benefit, loading stored `content_hash` values from SQLite at scan start populates the in-memory dedup cache, so it is warm from the first live filesystem event after the scan completes. Currently it starts cold and only fills as events arrive.

**Implementation detail:** At the start of `scan_now()`, bulk-load `{path: (file_mtime, content_hash)}` from the relevant index table(s) into a local dict. For each file in the walk, compare its `os.stat().st_mtime` against the cached mtime. On match, add the path to the `seen_*` set (for orphan cleanup) and populate `_known_hashes`, but skip all I/O. On mismatch or miss, fall through to the existing `_reindex()` pipeline.

**Performance impact:** For 500+ files where only a handful changed between restarts, the scan drops from reading and hashing every file (minutes) to stating every file (sub-second) plus full processing of only the changed files.

### 3. Paginated Scene Post Loading

**Current:** `GET /{campaign_id}/scenes/{scene_id}` calls `scenes.get_posts(scene_id)` which reads the entire markdown file from disk (`storage.read_posts()` at `storage.py:456`), parses every post, hydrates each from the YAML sidecar, and returns all posts in a single response. `SceneManager.get_posts()` accepts a `range` parameter (line 690) but it still reads the full file first, then filters — no actual I/O savings. The frontend renders all posts in a flat `.map()` with no virtualization.

**Backend changes:**

1. **New endpoint:** `GET /{campaign_id}/scenes/{scene_id}/posts?limit=N&before=ORDER` — returns the most recent N posts (by `order_in_scene`), with cursor-based pagination going backwards (older posts). Default limit: 50.

2. **Read from SQLite, not filesystem:** The new endpoint queries the `posts` table directly:
   ```sql
   SELECT * FROM posts
   WHERE scene_id = ? AND order_in_scene < ?
   ORDER BY order_in_scene DESC
   LIMIT ?
   ```
   No markdown file read. The scene indexer keeps this table current — new posts written during play update both the markdown and SQLite atomically via the indexer's event subscriptions.

3. **Modify existing `get_scene` endpoint:** Return an empty `posts: []` in the response (preserving the field for backwards compatibility). The scene metadata already includes `post_count` so the frontend knows there are posts to fetch from the new endpoint.

4. **Keep `get_posts()` for internal use:** The orchestrator's context builder and `posts_since_last_advance()` still call the full `get_posts()` method internally. These are infrequent calls (once per turn) where reading the file is acceptable.

**Frontend changes:**

1. **Initial load:** Fetch scene metadata, then fetch the most recent N posts (e.g., 50) from the new endpoint. Render immediately. The user sees the latest conversation state without waiting for the full history.

2. **Scroll-up loading:** When the user scrolls near the top of the post list, fetch the next page of older posts and prepend them. Standard upward-infinite-scroll pattern (like chat apps). Preserve scroll position so the viewport does not jump.

3. **New posts during play:** No change — WebSocket `post_appended` / `stream-delta` events continue to append incrementally. This already works.

4. **Refresh on `turn_complete`:** Currently refetches the entire scene including all posts. Change to fetch only posts newer than the last known `order_in_scene` and append, rather than replacing the entire list.

### 4. Frontend Sync Indicator

**Current:** The frontend checks `/api/setup/status` on mount for the setup wizard. There is no concept of "server is up but still syncing data."

**Change:**

1. **Backend:** Add `sync_status` to the `/api/health` response. Values: `"syncing"` | `"ready"`. Optionally include `sync_progress: { files_scanned: int, total_estimated: int }` where `total_estimated` comes from the count of index rows from the previous run.

2. **Frontend:** When `sync_status === "syncing"`, show a subtle, non-blocking indicator (small spinner or "Syncing library..." text in the app shell). Do not block navigation or interaction — stale data is better than no data. When the `library_indexed` WebSocket event arrives (already emitted by `scan_now()`), refresh any currently-visible listing view and hide the indicator.

3. **First-run edge case:** On a brand-new install, the index is empty and `sync_status` is `"syncing"`. The setup wizard runs during this window, so the user does not see empty listings. By the time they finish the wizard, the background scan has likely completed. If not, the sync indicator becomes visible.

### 5. Pre-Startup Script Optimization

**Current:** `run.sh` runs three operations sequentially before spawning uvicorn:
- `kill_port "$BACKEND_PORT"` — netstat parse + up to 2s wait
- `kill_port "$FRONTEND_PORT"` — netstat parse + up to 2s wait
- `kill_orphaned_uvicorn_workers` — spawns `powershell.exe` with `Get-CimInstance Win32_Process` (PowerShell cold-start is 1-3s on Windows, plus the WMI query)

Total: 5-9 seconds before uvicorn even starts.

**Change:**

1. **Parallelize:** Run all three `kill_*` calls as background subshells and `wait` for them. They are independent operations.

2. **Replace PowerShell with tasklist:** Swap the `Get-CimInstance Win32_Process` PowerShell call with `tasklist /FI "IMAGENAME eq python.exe"` piped through `findstr`. Avoids the PowerShell cold-start overhead entirely. Same result, roughly 10x faster on Windows.

## Impact Summary

| Section | Problem | Fix | Expected improvement |
|---|---|---|---|
| 1. Background reconciliation | Server blocked until scan finishes | Defer to background tasks | Startup: minutes to sub-second |
| 2. mtime skip | Background scan reads every file | Skip unchanged files by mtime | Scan: minutes to seconds |
| 3. Paginated posts | Large scenes slow to load | Cursor-paginated SQLite reads | Scene load: proportional to post count to constant |
| 4. Sync indicator | No visibility into background sync | Expose sync_status + frontend indicator | UX: transparent sync state |
| 5. Script optimization | Pre-startup overhead on Windows | Parallelize + drop PowerShell | Script: 5-9s to ~1s |

## Files Affected

**Backend — lifespan / startup:**
- `backend/src/grimoire/main.py` — move scan_now/backfill to background tasks, add sync_status
- `backend/src/grimoire/api/container.py` — add sync_status field to ServiceContainer
- `backend/src/grimoire/api/health.py` — expose sync_status in health response

**Backend — mtime optimization:**
- `backend/src/grimoire/watcher/watcher.py` — mtime check in scan_now, bulk-load mtime cache
- `backend/src/grimoire/state_store/store.py` — query for bulk mtime/hash lookup

**Backend — post pagination:**
- `backend/src/grimoire/api/campaigns/scenes.py` — new paginated posts endpoint, modify get_scene
- `backend/src/grimoire/scenes/manager.py` — add paginated post query from SQLite
- `backend/src/grimoire/scenes/indexer.py` — verify posts table has all needed fields

**Frontend:**
- `frontend/src/api/campaign/api.ts` — add paginated posts API call
- `frontend/src/api/campaign/types.ts` — update SceneDetail type
- `frontend/src/routes/campaign/usePlayDataLoader.ts` — paginated initial load
- `frontend/src/routes/campaign/ScenePane.tsx` — scroll-up pagination trigger
- `frontend/src/routes/campaign/playReducer.ts` — prepend older posts action
- `frontend/src/routes/campaign/usePlayStreamEvents.ts` — incremental refresh on turn_complete
- App shell component — sync indicator UI

**Scripts:**
- `scripts/run.sh` — parallelize kill_port, replace PowerShell with tasklist
- `scripts/_lib.sh` — update kill_orphaned_uvicorn_workers
