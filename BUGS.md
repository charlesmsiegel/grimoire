# BUGS.md

Audit of the grimoire repo. Findings come from three parallel passes — backend, frontend, cross-cutting — collated and de-duplicated. Severities reflect blast radius × likelihood, not how easy each is to fix.

This file is incremental: items are removed as they're fixed. The git log carries the per-item context. The original audit included all 7 CRITICAL items and the full HIGH set; those are resolved and have been deleted here. Re-verified 2026-05-20; the last four HIGH items were closed 2026-05-21.

Format: **Title** — file:line — one-line description.

---

## MEDIUM

- **`delete_campaign` leaves the on-disk tree** — `backend/src/grimoire/api/campaigns.py:327-328` — Deletes the DB row only. Re-creating with the same id silently inherits stale scenes/images.
- **`_drop_orphan_library_rows` opens N pool connections** — `backend/src/grimoire/watcher/watcher.py:672-684` — One `await self.store.db.execute(...)` per orphan, unwrapped from a transaction. Slow on large rescans; partial failure leaves SQLite half-cleaned.
- **`mechanics`/`plugins` rescan exceptions logged but services still wired with no modules** — `backend/src/grimoire/main.py:223-235` — If rescan throws, the empty service is still installed. Endpoints will return empty/null behavior with no surface signal beyond a log line.
- **`useResource` flashes "Loading…" on every dep change** — `frontend/src/api/useResource.ts:25` — `loading` resets to `true` unconditionally; `AsyncBoundary` hides existing data. Paginated/filtered re-fetches flicker.
- **`InputArea` autofocus steals focus from sidepanel buttons on every busy→idle** — `frontend/src/routes/campaign/InputArea.tsx:113-115` — Effect with `[busy]` deps focuses the textarea after Regenerate/Undo/Skip — yanks focus from the user's next click target.
- **`ScenePane` smooth-scroll-per-token queues animations** — `frontend/src/routes/campaign/ScenePane.tsx:19-21` — Scrolls on every `streaming.text.length`. Long streamed responses queue dozens of animations and never settle.
- **`CampaignSettings.GeneralTab` has no `key={campaign.id}`** — `frontend/src/routes/CampaignSettings.tsx:121` — Switching campaigns keeps the prior draft state because the component instance isn't keyed; silent cross-campaign edit bleed.
- **`SidePanel`'s `SourceBadge` ternary is dead** — `frontend/src/routes/campaign/SidePanel.tsx:59` — `source={pc ? "library" : "library"}`. Both branches return the same value. Should distinguish library vs emergent.
- **`AppSettings` `patch()` defined outside `useCallback`** — `frontend/src/routes/AppSettings.tsx:156` — `const patch = (next: Partial<AppConfig>) => { ... }` is a plain function recreated each render. (`load()` itself was refactored into `useAppConfig`; the surviving smell is the inline `patch`.)
- **`pluginsApi.configure` posts raw config as body** — `frontend/src/api/library.ts:548-549` — Sends the config dict as the top-level body; if the backend expects `{config: {...}}` the call silently 422s. Worth checking against the FastAPI route.
- **`ImageTile` URL construction uses `encodeURI` not `encodeURIComponent`** — `frontend/src/routes/campaign/ImagesView.tsx:98-99` — `encodeURI` doesn't encode `?`, `#`, `+`. A `+` in a filename breaks the URL.
- **`kill_stale` TOCTOU race** — `scripts/run.sh:67-72` + `scripts/_lib.sh:89-97` — Enumerates PIDs, then kills. No re-check that the port is free before launching uvicorn/vite. A fresh process binding the port in the gap (or insufficient `taskkill` privilege) causes a less obvious "address already in use".
- **`kill_stale` doesn't match Windows under non-MSYS bash** — `scripts/_lib.sh:10` — Case label is `MINGW*|MSYS*|CYGWIN*`. WSL falls through to Linux branch (correct); a PowerShell→`bash` invocation may not match either branch and skip cleanup (PLATFORM=unknown).
- **Wizard step numbering inconsistency** — `frontend/src/routes/CampaignCreate/CampaignCreate.tsx:206-223` + `StepStartingScene.tsx:58,176` — Steps indices run 0–5; `StepStartingScene` heading says "Step 6 — Starting scene" while the lazy-load comment at `:176` says "step 6" but the condition is `if (step !== 5)`. Off-by-one between zero- and one-indexed counting.
- **JsonField rewrites on every keystroke and discards in-progress edits** — `frontend/src/routes/library/FrontmatterEditor.tsx:149-158` — Calls `onChange` with the partial-parsed value when parse succeeds mid-typing; `useEffect` resets textarea from `value` on external change, blowing away user input.

## LOW

- **CORS allowlist is host-hardcoded but `run.sh` exposes host/port overrides** — `backend/src/grimoire/main.py:739-744` + `scripts/run.sh:11` — Pointing Vite at a different host yields silent CORS failures with no hint that the backend allowlist needs updating.
- **`settings = Settings()` evaluates `Path.home()` at import** — `backend/src/grimoire/config.py:35` — Tests that monkey-patch `Path.home()` after import won't see the override unless they re-instantiate `Settings()` or set `GRIMOIRE_DATA_ROOT`.
- **`BackendRegistry.unregister` leaks the worker** — `backend/src/grimoire/imagegen/service.py:182-183` — `pop` removes from the registry dict, but `_handles[backend_id]` keeps the backend ref and its worker task. The worker awaits a queue tied to a backend the registry no longer knows.
- **`to_payload` doesn't handle `datetime`** — `backend/src/grimoire/api/util.py:11-33` — Falls through to `return obj` for nested datetimes. Pydantic encoders catch it at response time but the heterogeneous payload shape varies.
- **`_find_post` is O(scenes × posts)** — `backend/src/grimoire/scenes/manager.py:858-880` — Every `retcon_post` and `delete_post` walks every campaign dir reading every scene's posts. Quadratic on large campaigns.
- **`CharactersService._active_pc` is an unbounded process-local cache** — `backend/src/grimoire/characters/service.py:154` — One entry per campaign ever seen, never evicted; no lock on the dict.
- **`apply_delta`'s `_capture_current_row` interpolates `table` into f-string but is allowlist-gated** — `backend/src/grimoire/state_store/store.py:1380,2196-2214` — Not exploitable today because `primary_key_columns()` returns `None` for non-allowlisted tables. Still worth tightening to use bound params via a stable mapping; the audit nearly missed the allowlist.
- **`EventBus.emit` uses `gather(..., return_exceptions=False)` after `_invoke` already catches** — `backend/src/grimoire/event_bus.py:111` — Dead defensive code. Misleading; if `_invoke` ever stops catching, sibling handlers abort.
- **README + `.gitignore` still scaffold a repo-local `data/` tree** — `.gitignore:41-53` + tracked `data/**/.gitkeep` — The dir nothing reads anymore is still tracked. Either remove the .gitkeeps and stanzas or document `data/` as legacy.
- **`parseBody` swallows JSON parse failures on error responses** — `frontend/src/api/client.ts:55` — `.catch(() => null)` on parse — user sees `HTTP 500` with no `detail`. Log the parse failure.
- **`wait_then_open` background curl loop is orphaned on script exit** — `scripts/run.sh:133-142` + `:77-92` — Backgrounded `wait_for_url` block's PID is never captured; cleanup trap doesn't kill it. On quick failure the script can leave a curl loop running for up to 30s.
- **Wizard `update()` callback recreates `draft` identity every keystroke** — `frontend/src/routes/CampaignCreate/CampaignCreate.tsx:202-204` — Children aren't `React.memo`'d so today this is a non-issue, but `StepStartingScene`'s `useMemo(candidates, [draft.pcs, castByWorld])` re-runs on every keystroke anywhere in the wizard.
- **`JsonSchema` interface escape-hatches `[key: string]: unknown`** — `frontend/src/components/schemaForm.ts:17` — Index signature was upgraded from `any` to `unknown` (partial improvement), but the escape-hatch defeats the discriminator at use sites. Intentional but flagged.
- **`useCampaignEvent` wildcard mode fragile** — `frontend/src/state/useCampaignEvent.ts:30-36` — `typeKey` from `sort().join("|")`. Passing `["*"]` as an array becomes `"*"` after sort+join and is treated as wildcard. Undocumented edge.

---

## Resolved since the initial audit

All 7 CRITICAL items and most of the HIGH set were addressed. The git log between `d12db23` and the current HEAD carries the per-item context. Headline fixes:

- **CRITICAL**: WebSocket path mismatch, path traversal via `campaign_id`, scene/continuity IDOR, empty `include` semantics, WebSocket reconnect race, lifespan DB-pool leak, README health URL.
- **HIGH (backend)**: service wiring (orchestrator / time_engine / export are now live), **`ContinuityService` now backed by `ContinuityRegistry` + `SqliteContinuityStore`** (facts/commitments survive restart), `put_sheet` mechanics module, `map_lookup_errors` HTTPException passthrough, `imagegen` no-backend error type, `_seed_defaults` atomic copy, `set_active_pc` DB persistence, EventBus lock removal, DB pool transaction rollback on release, `_run_turn` `CancelledError` handling, `_seed_for` SHA-256 seed, campaign tags persisted.
- **HIGH (frontend)**: `/api` prefix audit, `ApiError` consolidation, `setActivePC` URL encoding, API client HTML rejection, `usePlayState` advance-reason preservation, wizard campaigns append, cast input qualification + restricted Enter.
- **HIGH (scripts/config)**: macOS bash 3.2 `wait -n` replacement, `$CLAUDE_PROJECT_DIR` for guard hook.
- **MEDIUM (backend)**: `FileWatcher` is now constructed only when `library_cfg.watch` is set, the instance is retained on `container.extras["file_watcher"]`, and `scan_now()` is awaited when `scan_on_startup` is set (was previously dropped after one scan).
- **LOW (frontend)**: `Markdown.tsx` confirmed safe — uses only `remarkPlugins={[remarkGfm]}`, no `rehype-raw`, so `rehype-sanitize` not required.

## Methodology notes

- Three audits ran in parallel against `main`. Severities were assigned by the audits and lightly re-leveled when collated for consistency.
- I spot-checked the CRITICAL claims (WebSocket URL, scene IDOR, path-traversal, include-empty semantics, imagegen default-backend, lifespan ordering) against the actual code. One CRITICAL claim was dropped (SQL injection in `apply_delta` — defused by allowlist).
- Style / format nits and "add tests" / "add docstrings" suggestions were excluded by design.
- Line numbers refreshed 2026-05-20 after a second audit pass.
