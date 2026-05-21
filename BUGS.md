# BUGS.md

Audit of the grimoire repo. Findings come from three parallel passes — backend, frontend, cross-cutting — collated and de-duplicated. Severities reflect blast radius × likelihood, not how easy each is to fix.

This file is incremental: items are removed as they're fixed. The git log carries the per-item context. CRITICAL, HIGH, and MEDIUM are all closed (2026-05-20/21); the LOW set below is what's left.

Format: **Title** — file:line — one-line description.

---

## LOW

- **`CharactersService._active_pc` is an unbounded process-local cache** — `backend/src/grimoire/characters/service.py:154` — One entry per campaign ever seen, never evicted; no lock on the dict.
- **`apply_delta`'s `_capture_current_row` interpolates `table` into f-string but is allowlist-gated** — `backend/src/grimoire/state_store/store.py:1380,2196-2214` — Not exploitable today because `primary_key_columns()` returns `None` for non-allowlisted tables. Still worth tightening to use bound params via a stable mapping; the audit nearly missed the allowlist.
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
