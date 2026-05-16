# Scene Manager — Design (Shipped)

> Captures the Scene Manager design as actually built. The matching "remaining" spec at `2026-05-16-scene-manager-remaining-design.md` covers everything from the original `specs/10-scene-manager.md` that did **not** land in this work.

**Commit:** `0fc5b53` — "Build Scene Manager (task #17)"
**Module:** `backend/src/grimoire/scenes/`
**Tests:** `backend/tests/scenes/{test_boundary.py,test_manager.py,test_storage.py}`

## Purpose

The Scene Manager owns the play history on disk. It creates scenes, appends posts to them, maintains sidecar metadata (present cast, threads, running summary), implements the multi-PC advance trigger, and exposes a heuristic `is_scene_break` decision for the Orchestrator's pre-LLM check. Scene files are the source of truth; the manager is the only writer of them.

## Module surface

`SceneManager` (`scenes/manager.py`) is constructed with duck-typed collaborators so tests can wire in fakes:

- `data_root: Path` — campaign data root (`data/`)
- `config: SceneManagerConfig` (optional; see §Configuration)
- `event_bus: EventBus` (optional; defaults to a `_NullEventBus` no-op)
- `summarizer: Callable[[str | None, list[Post]], Awaitable[str]]` (optional; running-summary LLM hook)
- `final_summarizer: Callable[[Scene, list[Post]], Awaitable[tuple[str, list[str]]]]` (optional; scene-close LLM hook)
- `clock: Callable[[], datetime]` (injected for deterministic tests)

The bundled `InMemoryEventBus` in `scenes/events.py` is a tests/bootstrap stub — the real bus arrives via DI from `main.py` (`container.event_bus`, `main.py:138`).

## Public API

```python
class SceneManager:
    # CRUD / read-only
    async def list_scenes(campaign_id, branch_id="main") -> list[Scene]
    async def get_scene(scene_id) -> Scene
    async def get_scene_file_path(scene_id) -> Path
    async def load_scene_body(scene_id) -> str

    # Active scene tracking
    async def active_scene_for_campaign(campaign_id, branch_id="main") -> Scene | None
    async def active_scene_for_pc(campaign_id, pc_ref) -> Scene | None

    # Scene lifecycle
    async def start_scene(init: SceneInit) -> Scene
    async def close_scene(scene_id, *, closed_at_turn=None) -> SceneCloseReport

    # Posts
    async def append_post(scene_id, post: Post) -> None
    async def get_posts(scene_id, range=None) -> list[Post]
    async def posts_since_last_advance(scene_id) -> list[Post]
    async def recent_posts(scene_id, n=10) -> list[Post]

    # Presence
    async def add_present_character(scene_id, character_ref) -> None
    async def remove_present_character(scene_id, character_ref) -> None
    async def add_present_pc(scene_id, pc_ref) -> None
    async def set_pov(scene_id, character_ref) -> None

    # Decisions
    async def is_scene_break(scene_id, player_input, *,
                             now_in_game=None,
                             proposed_present_cast=None,
                             proposed_location_ref=None) -> SceneBreakDecision
    async def on_post_submitted(scene_id, post) -> AdvanceDecision
    async def on_advance_requested(scene_id) -> AdvanceResult

    # Summarization
    async def update_running_summary(scene_id) -> str

    # Threads
    async def add_thread(scene_id, thread: Thread, kind: str) -> None   # kind: "introduced"|"paid_off"
    async def list_threads(scene_id) -> SceneThreads

    # Editing
    async def edit_post(post_id, new_body, source: str) -> None
    async def delete_post(post_id, source: str) -> None

    # Fork (copy-on-write)
    async def fork_scenes_for_branch(campaign_id, new_branch_id, *,
                                     from_branch_id="main") -> list[Scene]

    # File watcher hook
    async def reindex_from_disk(scene_id) -> Scene
```

`new_post(...)` in `scenes/manager.py:694` mints `id`, `turn_id`, and `created_at` for callers (the Orchestrator uses this from `submit_post` and `_run_turn`).

## Storage: markdown + YAML sidecar pairs

Each scene is two files under `data/campaigns/<campaign_id>/scenes/` (or `branches/<branch_id>/scenes/` for non-main branches — see `scenes/storage.py:43`):

```
0001-elysium-opening.md       # prose, posts in order
0001-elysium-opening.yaml     # metadata sidecar
```

The basename is `f"{ordinal:04d}-{slug}"`. `next_ordinal` (`storage.py:59`) scans existing `*.yaml` files and returns `max + 1`, so gaps are preserved across deletions.

`render_body` formats posts as `## Post N — <author_label>` blocks separated by blank lines. `author_label` is `pc:<ref>` / `npc:<ref>` / `narrator` / `system` (see `types.py:28` and `storage.py:71`). `parse_body` is the inverse via the `POST_HEADING_RE` regex; non-matching `##` headings are ignored.

`append_post_to_body` (`storage.py:182`) appends a heading + body to the markdown without rewriting the file, so growth is O(1) per post.

Sidecar fields (`storage.py:88`): id, campaign_id, branch_id, ordinal, slug, title, location_ref, in_game_start, in_game_end, greeting_id, pov_character_ref, present_character_refs, present_pc_refs, mood, post_count, threads_introduced, threads_paid_off, tags, closed, closed_at_turn, last_advance_at_post, running_summary, final_summary, key_beats. All YAML serialization goes through `yaml.safe_dump(sort_keys=False, allow_unicode=True)`.

The markdown carries the prose; the sidecar carries everything else. Both are intended to be hand-editable; `reindex_from_disk` exists as the watcher hook (see §File watcher hook).

## Per-post identity (in-memory, not in markdown)

The markdown body only encodes `(order, author_kind, author_pc_ref|author_npc_ref, body)`. Per-post `id`, `turn_id`, `created_at`, and `is_player` live in an in-memory `_PostRecord` keyed by `(scene_id, str(order))` (`manager.py:115`). On `get_posts`, posts that lack a record (e.g., after restart) get a synthetic id `f"{scene_id}#post-{order}"`, `turn_id=""`, `created_at=epoch`, `is_player=False`. This is enough for current callers but means **per-post identity is not durable** across process restart.

## Scene id construction

`_scene_id(campaign_id, branch_id, ordinal, slug)` (`manager.py:130`) returns `f"{campaign_id}:{ordinal:04d}-{slug}"` on main and `f"{branch_id}:{campaign_id}:{ordinal:04d}-{slug}"` on a non-main branch.

## Start / close scene

`start_scene(init)`:
1. Resolve title (provided, or titled `location_ref`, or `"Scene"`) and slug (provided, or `slugify(title)`)
2. Allocate ordinal via `next_ordinal`, build the `Scene` with dedup'd present cast
3. Write an empty `.md` if missing and write the sidecar
4. Register the scene as active for `(campaign_id, branch_id)` and set `pc_current_scene[(campaign_id, pc_ref)] = scene_id` for each present PC
5. Emit `scene_started`

`close_scene(scene_id, *, closed_at_turn=None)`:
1. Acquire the per-scene lock (`_lock_for`)
2. Idempotent: closed scenes return their existing report
3. Build `(final_summary, key_beats)` via `_final_summary`: calls the injected `final_summarizer` if present, else derives a trivial summary as `running_summary` (or first/last post snippets joined with `…`)
4. Set `closed=True`, `closed_at_turn`, default `in_game_end := in_game_start` if missing
5. Persist sidecar, build `SceneCloseReport(scene, final_summary, key_beats, threads_resolved=threads_paid_off, threads_unresolved=introduced - paid_off)`
6. Emit `scene_ended` with the summary/beats/unresolved
7. Drop from `_active_scene` if it was the active one for the branch

## Append post

Per-scene `asyncio.Lock` serializes append/edit/delete/summary updates. After `await self.get_scene(scene_id)`:

1. Reject if `scene.closed` (`RuntimeError`)
2. If `post.order_in_scene == 0`, assign `scene.post_count + 1`; otherwise it must match exactly (otherwise `ValueError`)
3. Append heading + body to the `.md`
4. Bump `scene.post_count`; merge `author_pc_ref` into `present_pc_refs` + `present_character_refs` if PC; merge `author_npc_ref` into `present_character_refs` if NPC; update `pc_current_scene` for PC authors
5. Write sidecar
6. Record the `_PostRecord`
7. Emit `post_appended`; if PC, also emit `pc_post_appended`
8. If `config.running_summary_every_n_posts > 0` and `post_count % N == 0`, run `_update_running_summary_locked(scene)` inline (still under the per-scene lock)

## Multi-PC advance

```python
async def on_post_submitted(scene_id, post) -> AdvanceDecision:
    present = (await self.get_scene(scene_id)).present_pc_refs
    if len(present) <= 1 or not config.require_advance_with_multiple_pcs:
        return AdvanceDecision(auto_respond=True, reason="single_pc_scene")
    return AdvanceDecision(auto_respond=False, reason="multi_pc_pending_advance")
```

`on_advance_requested(scene_id)` takes the per-scene lock, computes `pending = posts_since_last_advance(scene_id)`, raises `NothingToAdvance` if empty, sets `scene.last_advance_at_post = scene.post_count`, persists the sidecar, emits `advance_requested`, and returns `AdvanceResult(scene, pending_posts)`.

`add_present_pc(scene_id, pc_ref)` emits `advance_disabled` when crossing 1→2 PCs; `remove_present_character(scene_id, pc_ref)` emits `advance_enabled` when dropping back to ≤1. There is **no** "flush pending posts via implicit advance" path — callers that want that semantics call `on_advance_requested` themselves.

## Active scene tracking

Two in-memory dicts:
- `_active_scene: dict[(campaign_id, branch_id), scene_id]` — set on `start_scene`, cleared on `close_scene`
- `_pc_current_scene: dict[(campaign_id, pc_ref), scene_id]` — set when a PC posts, is added as present, or the scene starts with them present

Both have on-disk fallbacks: `active_scene_for_campaign` walks `list_scenes` and picks the latest open scene; `active_scene_for_pc` walks scenes and picks the latest open one where the PC is present. The in-memory state is therefore a hot cache, not the SSOT — restart still finds the right scene by ordinal + `closed` flag.

## Heuristic scene-break detection

`is_scene_break` delegates to `detect_scene_break` in `scenes/boundary.py`. It is **pure heuristic** — no LLM call. Signals are scored 0..1 and the highest wins:

- `/end scene`, `/new scene`, `/scene end`, `/scene break`, `advance to ...`, `skip to ...`, `fast forward to ...` → confidence 1.0, reason `user_signal` (also produces a `proposed_new_scene`)
- Prose time-jump patterns (`hours later`, `the next morning`, `meanwhile, elsewhere`, etc.) → 0.85, reason `explicit`
- `proposed_location_ref` differs from `scene.location_ref` → 0.9, reason `location_change`. Falling back to prose location-transition patterns ("we adjourned to", "arrived at") → 0.65
- `now_in_game − scene.in_game_end ≥ config.time_gap_hours` → 0.85, reason `time_gap`. Fallback against `in_game_start` with 4× the threshold → 0.7
- `(before ∩ after) / before ≤ 1 − cast_change_ratio` → 0.75, reason `cast_change`

The decision returns `is_break = confidence >= config.confidence_threshold_prompt` (default 0.5). The Orchestrator's auto-vs-prompt cutoff is separate (`OrchestratorConfig.scene_break.auto_threshold`, default 0.8).

## Running summary

`config.running_summary_every_n_posts` (default 5) controls cadence; `append_post` calls `_update_running_summary_locked` inline when `post_count % N == 0`. With no `summarizer` injected the update returns early and the field stays unchanged. With a summarizer, exceptions are swallowed (no event emitted, no field update). Successful updates emit `running_summary_updated`. There is no separate background task today — the update runs on the post-append code path.

`update_running_summary(scene_id)` is the explicit-trigger entry point (e.g., for an admin call).

## Threads

`add_thread(scene_id, Thread(text), kind)` appends to `threads_introduced` or `threads_paid_off` on the sidecar (idempotent on `thread.text`) and emits `thread_introduced` / `thread_paid_off`. `list_threads(scene_id)` rebuilds `SceneThreads` from the sidecar lists; `Thread.introduced_at_post` / `paid_off_at_post` are kept on the dataclass but **not persisted** in the sidecar lists today.

The Extractor / LLM-assisted thread detection mentioned in spec 10 is not part of this module — Scene Manager just persists what callers tell it.

## Edit / delete post

`edit_post(post_id, new_body, source)`:
1. `_find_post` resolves `(Scene, Post)` by walking known scenes (`_active_scene` + scenes that have `_PostRecord` entries) then falling back to a filesystem walk
2. Under the per-scene lock, reload posts, replace the target's body in memory, and rewrite the entire `.md` via `write_body` (full rewrite — not append)
3. Emit `post_edited(order, source)`

`delete_post(post_id, source)`:
1. Resolve `(Scene, Post)` as above
2. Under the lock, drop the post, decrement `order_in_scene` on every later post, rewrite the `.md`
3. Update `scene.post_count`; clamp `last_advance_at_post` if it overshoots
4. Re-key `_PostRecord` entries to match the new orders
5. Emit `post_deleted(order, source)`

Both operations rewrite the markdown wholesale, so post-record IDs survive but ordinals shift.

## Fork (copy-on-write)

`fork_scenes_for_branch(campaign_id, new_branch_id, *, from_branch_id="main")`:
1. Reject same-branch forks (`ValueError`)
2. Reject when the target dir already exists (`FileExistsError`)
3. `shutil.copytree(source_dir, target_dir)` (or just create the dir if the source has no scenes yet)
4. Rewrite every sidecar so `branch_id` and `id` reflect the new branch
5. Return the list of forked `Scene`s

The Orchestrator calls this from `fork(...)` and swallows `FileExistsError` so re-runs are safe.

## File-watcher hook

`reindex_from_disk(scene_id)` re-reads the sidecar from disk, recomputes `post_count` from the markdown, persists the refreshed sidecar, and emits `scene_file_changed(post_count)`. The actual watcher is not wired today (`watcher/watcher.py` does not import `SceneManager`); the hook exists for when the watcher subscribes.

## Events emitted

Via `_emit(type_, scene, **payload)` → `event_bus.emit(SceneEvent(type, campaign_id, scene_id, payload))`:

- `scene_started`, `scene_ended`
- `post_appended` (every post), `pc_post_appended` (PC posts)
- `post_edited`, `post_deleted`
- `advance_requested`, `advance_disabled`, `advance_enabled`
- `running_summary_updated`
- `thread_introduced`, `thread_paid_off`
- `scene_file_changed`

Event names are constants in `scenes/events.py` (re-exported from `scenes/__init__.py`).

## Configuration (`SceneManagerConfig`)

```python
SceneManagerConfig(
    running_summary_every_n_posts=5,
    boundary=BoundaryConfig(
        confidence_threshold_auto=0.8,
        confidence_threshold_prompt=0.5,
        time_gap_hours=6.0,
        cast_change_ratio=0.5,
    ),
    require_advance_with_multiple_pcs=True,
)
```

The spec's `running_summary.model`, `running_summary.max_tokens`, `thread_detection.*`, and `files.*naming_pattern` knobs are not in `SceneManagerConfig` — pattern strings are hard-coded in `storage.py` and the summarizer model is whatever the injected callable closes over.

## Error handling (as implemented)

- `append_post` to a closed scene: `RuntimeError`
- `append_post` with mismatched `order_in_scene`: `ValueError`
- `on_advance_requested` with no pending posts: `NothingToAdvance` (subclass of `RuntimeError`)
- `add_thread` with unknown `kind`: `ValueError`
- `fork_scenes_for_branch` with same branch: `ValueError`; with existing target: `FileExistsError`
- `get_scene` / `_find_post`: `KeyError` if the scene or post can't be located
- Running-summary exceptions inside `_update_running_summary_locked`: swallowed, no field update, no event

## Wiring

`backend/src/grimoire/main.py:138`:

```python
container.scenes = SceneManager(data_root, event_bus=container.event_bus)
```

No `summarizer` / `final_summarizer` are injected — running summaries are effectively a no-op in production until a callable is wired. `OrchestratorService` consumes the manager via `scene_manager=container.scenes` (`main.py:220`); `ContextBuilderService` consumes it via `scenes=container.scenes` (`main.py:177`); `DataSources` consumes it via `scenes=container.scenes` (`main.py:202`).

## Test wiring

`backend/tests/scenes/test_manager.py` builds the manager around a `tmp_path` data root and an `InMemoryEventBus`, exercises every lifecycle path (start, append, multi-PC trigger, advance, running summary cadence, threads, close, edit, delete, fork, reindex). `test_boundary.py` exercises the heuristic confidence bands. `test_storage.py` covers `slugify`, ordinal allocation, sidecar roundtrip, body render/parse, and trailing-newline behavior.
