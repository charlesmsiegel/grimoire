# BUGS.md

Audit of the grimoire repo. Findings come from three parallel passes — backend, frontend, cross-cutting — collated and de-duplicated. Severities reflect blast radius × likelihood, not how easy each is to fix.

This file is incremental: items are removed as they're fixed. The git log carries the per-item context. The original audit included all 7 CRITICAL items and most of the HIGH set; those are resolved and have been deleted here. The remaining items below are the open work.

Format: **Title** — file:line — one-line description.

---

## HIGH

- **`apply_delta` path leaves orphan files when SQL rolls back** — `backend/src/grimoire/state_store/store.py:154-264` — `write_library_file` / `delete_library_file` mutate disk *before* opening `_txn`. If the SQL transaction rolls back, the file is gone (or wrongly present) and the index reflects the prior state — split-brain forever, repaired only by a full rescan.
- **In-memory `ContinuityService` loses facts/commitments on restart** — `backend/src/grimoire/main.py:108-110` + `continuity/service.py:107` — Wired as `ContinuityService()` ⇒ `InMemoryContinuityStore`. The API exposes write endpoints that silently drop their data at next restart. `SqliteContinuityStore` exists in the codebase but isn't used.
- **`ImageGenService._results` and `_cache` grow without bound** — `backend/src/grimoire/imagegen/service.py:242-244,628-644` — Every seeded `GenerationResult` (which can include image bytes for `_inline_` results) is retained for the life of the service. No TTL, no LRU, `aclose()` doesn't clear. Long-running servers OOM.
- **`usePlayState`: `turn_complete` + `post_appended` race** — `frontend/src/routes/campaign/usePlayState.tsx:223-228` — On `turn_complete` we dispatch `stream-end` *and* call `refresh()`. Concurrent `post_appended` events between the two state updates dedupe against a stale `posts` snapshot; a slow refresh racing with a fast next-turn submit can overwrite newer posts with older ones.
- **`useApi` / `useResource` deps spread with hooks/exhaustive-deps disabled** — `frontend/src/api/useApi.ts:43`, `frontend/src/api/useResource.ts:41` — The fetcher closure isn't in deps; any state it reads must be in the caller's `deps` array. Easy to drift silently — one missing dep ⇒ stale data forever.

## MEDIUM

- **`promote_entity` uses `kind.rstrip("s")` instead of `removesuffix`** — `backend/src/grimoire/api/campaigns.py:599-620` — `rstrip("s")` strips *all* trailing s's, so `"glass"` ⇒ `"gla"`. Works for `"items"` and `"locations"` but is the wrong API.
- **`get_campaign` swallows composition errors and returns `composition: null`** — `backend/src/grimoire/api/campaigns.py:200-203` — Bare `except Exception: composition = None`. User cannot diagnose why composition is missing.
- **`put_sheet` silently falls back to `mechanics_id="null"`** — `backend/src/grimoire/api/campaigns.py:655-660` — After the recent fix that reads `mechanics_module` from the campaign row, the fallback to `"null"` when no value is set is intentional, but the case still goes unsurfaced when the campaign row is missing entirely (404 now, previously silent). Re-evaluate after a few real PUT-sheet flows.
- **`scan_now` blocks the event loop on the entire library walk** — `backend/src/grimoire/watcher/watcher.py:171-220` — Synchronous filesystem walk + sync file parsing on the loop. Tolerable at boot but stalls everything if called from a rescan endpoint.
- **`scan_now()` runs even when nothing changed; FileWatcher live observer is never started** — `backend/src/grimoire/main.py:127-133` — The constructed `FileWatcher` (and its `EmbeddingQueue`) is dropped after one scan. Live edits don't reindex; the embedding queue path is effectively dead code. Either start the observer or remove the unused wiring.
- **Lifespan re-uses a pre-existing container's `state_store` against a new `Database`** — `backend/src/grimoire/main.py:69-83` — `container.db = db` is unconditional, but `state_store` is only constructed if `None`. A test that passes a container with a `state_store` bound to a different `Database` ends up with split-brain.
- **Cursors leaked via double-await `await (await conn.execute(...)).fetchall()`** — `backend/src/grimoire/state_store/snapshots.py:37-54,117-125,136-144` — aiosqlite cursor finalizer is non-deterministic; on WAL these can hold reader locks briefly. Use `async with conn.execute(...) as cur:` consistently.
- **Continuity `add_fact`/`add_commitment` mutate caller's input** — `backend/src/grimoire/continuity/service.py:128-131,327-330` — `fact.tags.append(...)`. Callers reusing the same dataclass accumulate `src:` tags.
- **`delete_campaign` leaves the on-disk tree** — `backend/src/grimoire/api/campaigns.py:249-251` — Deletes the DB row only. Re-creating with the same id silently inherits stale scenes/images.
- **`_drop_orphan_library_rows` opens N pool connections** — `backend/src/grimoire/watcher/watcher.py:422-434` — One `await self.store.db.execute(...)` per orphan, unwrapped from a transaction. Slow on large rescans; partial failure leaves SQLite half-cleaned.
- **`mechanics`/`plugins` rescan exceptions logged but services still wired with no modules** — `backend/src/grimoire/main.py:88-102` — If rescan throws, the empty service is still installed. Endpoints will return empty/null behavior with no surface signal beyond a log line.
- **`useResource` flashes "Loading…" on every dep change** — `frontend/src/api/useResource.ts:18,41` — `loading` resets to `true` unconditionally; `AsyncBoundary` hides existing data. Paginated/filtered re-fetches flicker.
- **`InputArea` autofocus steals focus from sidepanel buttons on every busy→idle** — `frontend/src/routes/campaign/InputArea.tsx:60-62` — Effect with `[busy]` deps focuses the textarea after Regenerate/Undo/Skip — yanks focus from the user's next click target.
- **`ScenePane` smooth-scroll-per-token queues animations** — `frontend/src/routes/campaign/ScenePane.tsx:17-19` — Scrolls on every `streaming.text.length`. Long streamed responses queue dozens of animations and never settle.
- **`CampaignSettings.GeneralTab` has no `key={campaign.id}`** — `frontend/src/routes/CampaignSettings.tsx:137-138` — Switching campaigns keeps the prior draft state because the component instance isn't keyed; silent cross-campaign edit bleed.
- **`SidePanel`'s `SourceBadge` ternary is dead** — `frontend/src/routes/campaign/SidePanel.tsx:36-37` — `source={pc ? "library" : "library"}`. Both branches return the same value. Should distinguish library vs emergent.
- **`AppSettings` `load()` lives outside `useCallback`** — `frontend/src/routes/AppSettings.tsx:170-185,230-245` — `useEffect(() => { void load(); }, [])` with closure-captured `load`. Works today, will silently break if `load` ever needs to read fresh props.
- **`pluginsApi.configure` posts raw config as body** — `frontend/src/api/library.ts:256-258` — Sends the config dict as the top-level body; if the backend expects `{config: {...}}` the call silently 422s. Worth checking against the FastAPI route.
- **`ImageTile` URL construction uses `encodeURI` not `encodeURIComponent`** — `frontend/src/routes/campaign/ImagesView.tsx:91-94` — `encodeURI` doesn't encode `?`, `#`, `+`. A `+` in a filename breaks the URL.
- **`kill_stale` TOCTOU race** — `scripts/run.sh:46-94` — Enumerates PIDs, then kills. No re-check that the port is free before launching uvicorn/vite. A fresh process binding the port in the gap (or insufficient `taskkill` privilege) causes a less obvious "address already in use".
- **`kill_stale` doesn't match Windows under non-MSYS bash** — `scripts/run.sh:55` — Case label is `MINGW*|MSYS*|CYGWIN*`. WSL falls through to Linux branch (correct); a PowerShell→`bash` invocation may not match either branch and skip cleanup.
- **Wizard step numbering inconsistency** — `frontend/src/routes/CampaignCreate/CampaignCreate.tsx:140-176` — Comments say "step 5 (Starting scene)" / "step 6"; `StepStartingScene.tsx:53` heading says "Step 6". Off-by-one between zero- and one-indexed counting; misleading.
- **JsonField rewrites on every keystroke and discards in-progress edits** — `frontend/src/routes/library/FrontmatterEditor.tsx:148-158` — Calls `onChange` with the partial-parsed value when parse succeeds mid-typing; `useEffect` resets textarea from `value` on external change, blowing away user input.

## LOW

- **CORS allowlist is host-hardcoded but `run.sh` exposes host/port overrides** — `backend/src/grimoire/main.py:152-156` + `scripts/run.sh:11` — Pointing Vite at a different host yields silent CORS failures with no hint that the backend allowlist needs updating.
- **`settings = Settings()` evaluates `Path.home()` at import** — `backend/src/grimoire/config.py:15,28` — Tests that monkey-patch `Path.home()` after import won't see the override unless they re-instantiate `Settings()` or set `GRIMOIRE_DATA_ROOT`.
- **`BackendRegistry.unregister` leaks the worker** — `backend/src/grimoire/imagegen/service.py:156-157` — `pop` removes from the registry dict, but `_handles[backend_id]` keeps the backend ref and its worker task. The worker awaits a queue tied to a backend the registry no longer knows.
- **`to_payload` doesn't handle `datetime`** — `backend/src/grimoire/api/util.py:11-33` — Falls through to `return obj` for nested datetimes. Pydantic encoders catch it at response time but the heterogeneous payload shape varies.
- **`_find_post` is O(scenes × posts)** — `backend/src/grimoire/scenes/manager.py:614-636` — Every `retcon_post` and `delete_post` walks every campaign dir reading every scene's posts. Quadratic on large campaigns.
- **`CharactersService._active_pc` is an unbounded process-local cache** — `backend/src/grimoire/characters/service.py:114,484-514` — One entry per campaign ever seen, never evicted; no lock on the dict.
- **`apply_delta`'s `_capture_current_row` interpolates `table` into f-string but is allowlist-gated** — `backend/src/grimoire/state_store/store.py:1380` — Not exploitable today because `primary_key_columns()` returns `None` for non-allowlisted tables. Still worth tightening to use bound params via a stable mapping; the audit nearly missed the allowlist.
- **`EventBus.emit` uses `gather(..., return_exceptions=False)` after `_invoke` already catches** — `backend/src/grimoire/event_bus.py:107` — Dead defensive code. Misleading; if `_invoke` ever stops catching, sibling handlers abort.
- **README + `.gitignore` still scaffold a repo-local `data/` tree** — `.gitignore:35-43` + tracked `data/**/.gitkeep` — The dir nothing reads anymore is still tracked. Either remove the .gitkeeps and stanzas or document `data/` as legacy.
- **`parseBody` swallows JSON parse failures on error responses** — `frontend/src/api/client.ts:55` — `.catch(() => null)` on parse — user sees `HTTP 500` with no `detail`. Log the parse failure.
- **`wait_then_open` background curl loop is orphaned on script exit** — `scripts/run.sh:144` — Cleanup trap doesn't track the waiter PID; on quick failure the script can leave a curl loop running for up to 30s.
- **`Markdown` component without `rehype-sanitize`** — `frontend/src/components/Markdown.tsx` — Currently safe (no `rehype-raw`), but a future `rehype-raw` addition becomes XSS. Worth a comment.
- **Wizard `update()` callback recreates `draft` identity every keystroke** — `frontend/src/routes/CampaignCreate/CampaignCreate.tsx:201-203` — Children aren't `React.memo`'d so today this is a non-issue, but `StepStartingScene`'s `useMemo(candidates, [draft.pcs, castBySetting])` re-runs on every keystroke anywhere in the wizard.
- **`JsonSchema` interface escape-hatches `[key: string]: any`** — `frontend/src/routes/library/PluginsView.tsx:268-270` — Explicit `any` index signature defeats the discriminator. Intentional but flagged.
- **`useCampaignEvent` wildcard mode fragile** — `frontend/src/state/useCampaignEvent.ts:36` — `typeKey` from `sort().join("|")`. Passing `["*"]` as an array looks like an explicit-type filter after sort+join. Undocumented edge.

---

## Resolved since the initial audit

All 7 CRITICAL items and most of the HIGH set were addressed. The git log between `d12db23` and `4b454aa` carries the per-item context. Headline fixes:

- **CRITICAL**: WebSocket path mismatch, path traversal via `campaign_id`, scene/continuity IDOR, empty `include` semantics, WebSocket reconnect race, lifespan DB-pool leak, README health URL.
- **HIGH (backend)**: service wiring (orchestrator / time_engine / export are now live), `put_sheet` mechanics module, `map_lookup_errors` HTTPException passthrough, `imagegen` no-backend error type, `_seed_defaults` atomic copy, `set_active_pc` DB persistence, EventBus lock removal, DB pool transaction rollback on release, `_run_turn` `CancelledError` handling, `_seed_for` SHA-256 seed, campaign tags persisted.
- **HIGH (frontend)**: `/api` prefix audit, `ApiError` consolidation, `setActivePC` URL encoding, API client HTML rejection, `usePlayState` advance-reason preservation, wizard campaigns append, cast input qualification + restricted Enter.
- **HIGH (scripts/config)**: macOS bash 3.2 `wait -n` replacement, `$CLAUDE_PROJECT_DIR` for guard hook.

## Methodology notes

- Three audits ran in parallel against `main` at `d12db23`. Severities were assigned by the audits and lightly re-leveled when collated for consistency.
- I spot-checked the CRITICAL claims (WebSocket URL, scene IDOR, path-traversal, include-empty semantics, imagegen default-backend, lifespan ordering) against the actual code. One CRITICAL claim was dropped (SQL injection in `apply_delta` — defused by allowlist).
- Style / format nits and "add tests" / "add docstrings" suggestions were excluded by design.
- Line numbers in the remaining entries are best-effort; files move. Re-grep the symbol before changing anything.
