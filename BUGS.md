# BUGS.md

Audit of the grimoire repo. Findings come from three parallel passes — backend, frontend, cross-cutting — collated and de-duplicated.

**Status: all open items closed.** CRITICAL and HIGH cleared in earlier passes; MEDIUM + LOW closed 2026-05-21. The git log carries the per-item context.

Format: **Title** — file:line — one-line description.

---

## Resolved since the initial audit

Headline fixes (see git log for the full per-commit context):

- **CRITICAL**: WebSocket path mismatch, path traversal via `campaign_id`, scene/continuity IDOR, empty `include` semantics, WebSocket reconnect race, lifespan DB-pool leak, README health URL.
- **HIGH (backend)**: service wiring (orchestrator / time_engine / export are now live), **`ContinuityService` now backed by `ContinuityRegistry` + `SqliteContinuityStore`** (facts/commitments survive restart), `put_sheet` mechanics module, `map_lookup_errors` HTTPException passthrough, `imagegen` no-backend error type, `_seed_defaults` atomic copy, `set_active_pc` DB persistence, EventBus lock removal, DB pool transaction rollback on release, `_run_turn` `CancelledError` handling, `_seed_for` SHA-256 seed, campaign tags persisted, `apply_delta` disk-rollback on SQL failure, `ImageGenService` cache LRU + clear-on-aclose.
- **HIGH (frontend)**: `/api` prefix audit, `ApiError` consolidation, `setActivePC` URL encoding, API client HTML rejection, `usePlayState` advance-reason preservation + stale-refresh merge, wizard campaigns append, cast input qualification + restricted Enter, `useApi`/`useResource` driven off fetcher identity.
- **HIGH (scripts/config)**: macOS bash 3.2 `wait -n` replacement, `$CLAUDE_PROJECT_DIR` for guard hook.
- **MEDIUM (backend)**: `FileWatcher` lifecycle + retained instance; `scan_now()` off the event loop; lifespan reuses pre-wired container db; orphan-row deletes batched; cursor leaks via `async with`; `delete_campaign` cleans on-disk tree; `get_campaign` surfaces composition errors; mechanics/plugins rescan failures surface via `/health`.
- **MEDIUM (frontend)**: `useResource` no-flash on refetch, `InputArea` mount-only autofocus, `ScenePane` rAF-coalesced scroll, `CampaignSettings` panels keyed by id, `SourceBadge` distinguishes library/emergent, `AppSettings.patch` memoized, `ImageTile` per-segment URL encoding, JsonField in-progress edits preserved.
- **LOW**: CORS allowlist follows `FRONTEND_HOST`/`PORT`; settings defaults deferred; `BackendRegistry` worker cleanup; `to_payload` datetime handling; `_find_post` cached `post_id → scene_id`; `_active_pc` bounded LRU; `_capture_current_row` table allowlist local; `EventBus.emit` fault isolation; legacy `data/` tree removed; `parseBody` logs parse failures; `wait_then_open` orphan cleanup; `useCampaignEvent` wildcard hardened.

## Methodology notes

- Three audits ran in parallel against `main`. Severities were assigned by the audits and lightly re-leveled when collated for consistency.
- I spot-checked the CRITICAL claims (WebSocket URL, scene IDOR, path-traversal, include-empty semantics, imagegen default-backend, lifespan ordering) against the actual code. One CRITICAL claim was dropped (SQL injection in `apply_delta` — defused by allowlist).
- Style / format nits and "add tests" / "add docstrings" suggestions were excluded by design.
- Line numbers refreshed 2026-05-20 after a second audit pass.
