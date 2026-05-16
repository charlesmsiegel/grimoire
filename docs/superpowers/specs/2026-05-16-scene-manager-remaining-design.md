# Scene Manager — Remaining Work

> Everything from the original `specs/10-scene-manager.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-scene-manager-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-scene-manager-design.md`
**Module:** `backend/src/grimoire/scenes/`

## 1. SQLite indexing of scenes and posts

Spec 10 §Storage states "SQLite indexes them (`scenes` and `posts` tables in `03-state-store.md`) but doesn't own them." The schema exists (`backend/src/grimoire/storage/migrations/004_scenes_posts.sql` defines both tables with their indexes), and `StateStoreService.list_scenes` / `get_scene_metadata` read from them (`state_store/store.py:532`, `:548`), but **nothing writes to them**: `grep -r "INSERT INTO scenes" backend/` returns no matches. As a result the SQLite tables are permanently empty and any caller that uses `state_store.list_scenes` instead of `scene_manager.list_scenes` gets `[]`.

Needs:
- An indexer/upsert pathway (likely a `SceneIndexer` parallel to the existing State Store indexers) that maps every Scene Manager event (`scene_started`, `scene_ended`, `post_appended`, `post_edited`, `post_deleted`, `scene_file_changed`) to an `INSERT OR REPLACE` against `scenes` / `posts`
- Subscribed at app wiring time in `main.py` after both `state_store` and `scenes` exist
- A backfill path that walks `data/campaigns/*/scenes/*.yaml` on startup and reconciles the SQLite indexes with disk (handles direct-edit-while-down)
- Per-post columns to populate: `id`, `scene_id`, `turn_id`, `order_in_scene`, `author_kind`, `author_pc_ref`, `body_excerpt`, `body_hash`, `is_player`, `created_at`, `retconned_from`

## 2. Durable per-post identity

Today `(post.id, post.turn_id, post.created_at, post.is_player)` live in an in-memory `_PostRecord` dict (`scenes/manager.py:115`). The markdown body has no place for these — `parse_body` only recovers `(order, author_kind, author_pc_ref|npc_ref, body)`. After process restart, `get_posts` synthesizes `id = f"{scene_id}#post-{order}"`, `turn_id=""`, `created_at=epoch`, `is_player=False`. This is invisible until something queries by post id (Orchestrator retcon, Continuity backlinks) and silently breaks.

Options:
- (a) Persist `_PostRecord` alongside the sidecar (e.g., a `.posts.json` companion or a `posts:` list in the YAML)
- (b) Persist via the `posts` SQLite table from §1 and rebuild the record cache on startup
- (c) Encode the fields inline in the markdown heading as HTML comments (`<!-- id=... turn=... -->`)

Pick one and add a startup hydration path so `get_posts` returns durable records.

## 3. Watcher integration for direct edits

Spec 10 §File watcher integration calls for: watcher detects change → Scene Manager re-parses → updates SQLite indexes → emits `scene_file_changed` → Frontend refreshes. Today the `reindex_from_disk(scene_id)` hook exists on the manager but nothing calls it: `backend/src/grimoire/watcher/watcher.py` does not import `SceneManager` and has no scenes branch. The hash-based conflict detection mentioned in spec 10 ("last-write-wins with hash-based conflict detection — see `03-state-store.md`") is not present either; `storage.py:content_hash` exists but is unused.

Needs:
- Subscribe the watcher to `data/campaigns/*/scenes/*.{md,yaml}` (and `branches/*/scenes/...`)
- On change, resolve the `scene_id` from the file path and call `reindex_from_disk`
- Add conflict detection: compare `body_hash` on disk vs the last known hash; if the file changed during an in-flight Scene Manager write, surface a warning event (e.g., `scene_file_conflict`) rather than silently accepting the disk version

## 4. Running summary as a background job

Spec 10 §Running summary says "Updated every N posts (default 5) by a **background job** calling the LLM with the scene's recent posts and the previous running summary." Today the update runs **inline** in `append_post` under the per-scene lock (`manager.py:381`). That means a slow LLM call blocks the next post append and pushes the running summary onto the user's critical path.

Needs:
- Detach the cadence check from `append_post`: emit (or enqueue) a `running_summary_due` job
- A background worker — likely subscribed to `post_appended` on the event bus — that drains pending jobs (per-scene FIFO) and calls the summarizer outside the append lock
- Coalesce: if N posts arrive while a summary is running, run once on the latest state, not N times
- Preserve the existing `update_running_summary(scene_id)` explicit-trigger API for tests / admin calls

## 5. Final scene summary via LLM

`close_scene` derives `final_summary` and `key_beats` via the injected `final_summarizer` if present, else falls back to "first line … last line" (`manager.py:316`). No `final_summarizer` is wired in `main.py`, so production scenes close with the trivial fallback.

Spec 10 §Scene close calls for: generate a final summary, extract 3-5 key beats, identify resolved/unresolved threads. Threads-resolved/unresolved is already wired; the LLM-driven summary + beats are not. Add a default `final_summarizer` (likely using `llm_gateway` with a Haiku task) and inject it in `main.py`.

## 6. LLM-assisted thread detection

Spec 10 §Threads: "Threads are detected by the Extractor (LLM-assisted) or marked manually." Today `add_thread` and `list_threads` are pure CRUD — nothing detects threads. The Extractor (`backend/src/grimoire/extractor/`) does not currently emit thread deltas. Needs either:
- An extractor delta type for thread introduction / payoff that routes through the standard apply path and lands in `add_thread`
- Or a Scene-Manager-owned background pass that periodically scans recent posts for thread candidates

Tie-in to Continuity (spec 11): unresolved threads from `SceneCloseReport.threads_unresolved` should hand off to the campaign-wide thread ledger.

## 7. LLM-assisted scene-break refinement

`scenes/boundary.py:1` notes "This is a heuristic-only implementation; a future LLM-assisted refinement can layer on top." Spec 10 §Scene boundary detection lists `tonal_shift` and the medium-confidence "prompt the user" UX, neither of which the regex heuristic can do well. The current detector covers `user_signal`, `time_gap`, `location_change`, `cast_change`, and an `explicit` time-jump prose pattern; it does not score tonal shift.

Add an optional `llm_classifier: Callable[[Scene, str, list[Post]], Awaitable[SceneBreakDecision]] | None` parameter that, when provided, runs after the heuristic and overrides borderline-confidence cases (e.g., 0.4..0.7). Keep the heuristic-only path as the default so the module stays usable in tests without an LLM.

## 8. Multi-PC PC-leave flush semantics

Spec 10 §Multi-PC advance trigger calls out: "2 PCs → 1 PC: auto-respond resumes; pending posts are flushed via implicit advance." Today `remove_present_character` emits `advance_enabled` when crossing back to ≤1 PC but does **not** flush pending posts — the next `on_post_submitted` call will return `auto_respond=True` and the previously-pending posts sit unadvanced (`scene.last_advance_at_post` is unchanged) until the next explicit `on_advance_requested`.

Needs: when `remove_present_character` drops a PC such that `len(present_pc_refs) ≤ 1`, atomically set `scene.last_advance_at_post = scene.post_count` so subsequent auto-responses don't accidentally treat old pending posts as fresh input. Decide whether to emit a synthetic `advance_requested` for symmetry with explicit advance.

## 9. Configuration knobs not modeled

`SceneManagerConfig` covers `running_summary_every_n_posts`, `boundary.*`, and `require_advance_with_multiple_pcs`. Spec 10 §Configuration lists more:

- `running_summary.model`, `running_summary.max_tokens` — currently captured by whatever closure the injected `summarizer` uses; needs surfacing if the default summarizer from §4 is added
- `thread_detection.enabled`, `thread_detection.model` — pending §6
- `files.scene_naming_pattern`, `files.post_heading_pattern` — pattern strings hard-coded in `storage.py` (`scene_basename`, `render_body`). Plumb through `SceneManagerConfig` and feed both helpers
- `multi_pc.show_pending_count_in_ui` — purely a Frontend flag; needs a place in either Frontend config or the WebSocket payload of `advance_disabled`

## 10. Missing `Thread` provenance persistence

`Thread.introduced_at_post` and `paid_off_at_post` exist on the dataclass (`types.py:38`) but `add_thread` only persists `thread.text` to the sidecar's flat `threads_introduced` / `threads_paid_off` string lists (`manager.py:548`). `list_threads` reconstructs `Thread` objects with both fields `None`.

To preserve provenance for Continuity, change the sidecar schema for threads to `[{text, introduced_at_post, paid_off_at_post}, ...]` with a migration that turns old string lists into objects with `None` post refs. Update `_scene_to_yaml` / `_yaml_to_scene` in `storage.py` accordingly.

## 11. `closed_at_turn` always nullable on `close_scene`

`close_scene(scene_id, *, closed_at_turn=None)` accepts an optional turn id but no orchestrator call site passes one today (the orchestrator's auto-break path closes scenes without a `closed_at_turn`). Either:
- Require `closed_at_turn` (drop the default) and update callers
- Default it to the current turn id, which means threading the orchestrator's `turn_id` through `_maybe_break_scene` → `close_scene`

Pick one before depending on `closed_at_turn` for audit queries.

## 12. Open questions from spec 10

Carried forward verbatim so they don't get re-litigated without intent:

- **Scene branching mid-scene** (v2; deferred): fork from a specific post inside a scene — supported by the State Store branching primitive but needs UX work
- **Cross-PC scene visibility** (v2; deferred): "Should PC A see PC B's scenes by default in the Frontend?" — Frontend UX decision; current answer is "no by default, campaign overview shows all"
- **Scene rename** (v2; deferred): renaming a slug renames the files — needs a UI affordance and an idempotent rename helper that updates filename, sidecar `slug`, and `_active_scene` / `_pc_current_scene` keys
- **Multi-PC ordering**: "if PC A and PC B both submit before Advance, who goes first in the LLM's response?" — orchestrator currently joins pending posts in temporal order; document as the answer or revisit if it produces bad responses
- **Idle PCs in a multi-PC scene** (rejected): adding an "afk" status to unblock auto-advance when a present PC has been silent for several rounds. Spec marks this as v2 and current implementation treats present-means-present until explicit removal. Do not add without re-brainstorming

---

## Suggested plan ordering

If picking this up, a reasonable order:
1. §1 + §2 + §3 together — durability + indexing + watcher land as one cohesive story; §2 unlocks reliable retcon and §1 unlocks any cross-module query against scenes/posts
2. §4 + §5 + §6 — LLM-driven summarization and thread detection; §4 first because §5 reuses the same summarizer plumbing
3. §10 — thread provenance, a sidecar schema bump; combine with §6's migration
4. §7 — LLM scene-break refinement (needs the same gateway plumbing as §4–§6)
5. §8 — multi-PC leave flush; tiny but UX-visible, easy to slot in
6. §9 + §11 — configuration cleanup and `closed_at_turn` discipline; mostly mechanical once the rest is done
