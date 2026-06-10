# State Store — Design (Shipped)

> Captures the State Store design as actually built. The matching "remaining" spec at `2026-05-16-state-store-remaining-design.md` covers everything from the original `specs/03-state-store.md` that did **not** land in this work.

**Commits:** `556fd48` — "Set up SQLite migrations + sqlite-vec (task 5)", `908dbdd` — "Build State Store schema and write APIs (task 8)" (followed by `03bcd23`, `8c94a01`, `44cb1c3`, `ff9d5c9`, `f01de38`, `659b6cd`, `3231ad9`, `ad29fee`, `3c97701`, `cd15e0e`, `c8872d1`, `87e0643`)
**Modules:**
- `backend/src/grimoire/storage/` — SQLite connection pool + migration runner
- `backend/src/grimoire/state_store/` — write-side coordinator
- `backend/src/grimoire/watcher/` — file watcher that reindexes on disk changes
**Tests:** `backend/tests/state_store/`, `backend/tests/test_storage.py`

## Purpose

The State Store is the authoritative persistence layer. Files under `data/library/` and `data/campaigns/` are SSOT for content; SQLite at `data/campaigns.sqlite` holds derived indexes, version-pinned snapshots, embeddings, the delta log, and the per-branch structured campaign state (facts, commitments, transient entity state) that doesn't make sense as files. Every domain write goes through `StateStore` so the two halves stay coherent and every change is reversible via the delta log.

## Module surface

```
storage/
  db.py                       # async aiosqlite pool with WAL, FKs, sqlite-vec
  migrations.py               # discover + apply NNN_*.sql migrations in order
  migrations/001..014_*.sql   # full schema (every spec table)

state_store/
  __init__.py                 # exports StateStore, LibraryRef, path helpers
  errors.py                   # StateStoreError, NotFoundError, ConflictError, InvalidRefError
  paths.py                    # safe id validation + library/campaign path math
  indexers.py                 # upsert_library_index / upsert_campaign_content_index
  snapshots.py                # write / remove / upgrade library_snapshots
  delta_log.py                # insert/list/reverse deltas + registered upsert tables
  search.py                   # vector + FTS5 keyword search
  store.py                    # StateStore facade

watcher/
  classifier.py               # path → WatchedFile (scope, kind, ids)
  watcher.py                  # FileWatcher (scan + watchdog) + EmbeddingQueue
```

`StateStore` is constructed with an already-connected `Database` and a `data_root` path. Callers are responsible for `db.connect()` + `apply_migrations(db)` before construction (the FastAPI lifespan in `main.py` does this once at startup).

## On-disk layout

Matches spec 03 verbatim:

```
data/
├── library/
│   ├── worlds/<id>/
│   │   ├── world.yaml
│   │   ├── characters/*.md
│   │   ├── items/*.md
│   │   ├── locations/*.md
│   │   ├── lore/*.md
│   │   ├── factions/*.md
│   │   └── greetings/*.md
│   ├── style-guides/*.md
│   └── image-presets/*.yaml
├── campaigns/<id>/
│   ├── campaign.yaml
│   ├── scenes/NNNN-<slug>.{md,yaml}
│   ├── overrides/worlds/<world>/<kind>/<id>.yaml
│   ├── emergent/<kind>/<id>.md
│   ├── sheets/<kind>/<id>.<mechanics-id>.yaml
│   └── images/<id>.{png,yaml}
└── campaigns.sqlite
```

All variable path components run through `_validate_path_component` (`paths.py:54`) — a strict allowlist (`^[A-Za-z0-9][A-Za-z0-9._-]*$`) that rejects `..`, slashes, and dotfiles so HTTP-supplied ids cannot escape the data root.

## SQLite schema (migrations 001–014)

Every table named in the spec is created by the migration runner; see `storage/migrations/`:

- `001_indexes.sql` — `library_index`, `library_index_fts` (FTS5 with sync triggers), `campaign_content_index`, `library_snapshots`
- `002_campaigns.sql` — `campaigns`, `campaign_setting_refs` (renamed to `campaign_world_refs` in `014`), `campaign_pcs`, `branches`
- `003_transient_state.sql` — `character_state`, `location_state`, `faction_state`
- `004_scenes_posts.sql` — `scenes`, `posts`
- `005_continuity.sql` — `facts` + `facts_fts`, `commitments`, `relationships`, `knowledge_state`, `calendar`
- `006_images.sql` — `images`
- `007_deltas_review_embeddings.sql` — `deltas`, `review_queue`, `embeddings` (sqlite-vec BLOB)
- `008`–`013` — observability tables, scheduled events, campaign tags (out of scope for spec 03 but live in the same database)
- `012_setting_refs_include_nullable.sql` — allow NULL `include` to mean "include all kinds"
- `014_rename_setting_to_world.sql` — rename `setting_id`/`campaign_setting_refs` to world terminology

Migrations apply transactionally one at a time (`migrations.py:101`); each statement is split with `sqlite3.complete_statement` so triggers and FTS DDL run as single statements, and `schema_version` records the applied version atomically with the migration body. The `Database` pool (`storage/db.py`) opens each connection in autocommit isolation, turns on foreign keys, optionally enables WAL + `synchronous=NORMAL`, and loads `sqlite-vec`.

`Database.acquire()` rolls back any open transaction before returning a connection to the pool (`db.py:84`) so a leaked `BEGIN` from a previous caller can't poison the next consumer.

## Path conventions (`paths.py`)

- `parse_library_id(library_id) -> LibraryRef` parses `worlds/<w>/<kind>/<id>`, `worlds/<w>/world`, `style-guides/<id>`, `image-presets/<id>` — singular kinds for entities (`character`, `item`, ...), `world` / `style_guide` / `image_preset` for top-level.
- `library_path(data_root, library_id) -> Path` resolves the on-disk file. Markdown for prose entities + style guides; YAML for world cards and image presets.
- `override_path` / `emergent_path` / `sheet_path` / `image_metadata_path` produce campaign-scoped paths with the same validation.
- `KIND_TO_DIR` / `DIR_TO_KIND` map singular kinds to plural directory names.
- `campaign_id_for_path` extracts the campaign id from any path under `data/campaigns/`.

`make_library_id` in `indexers.py` is the inverse of `parse_library_id` and uses the 3-segment form (`worlds/<id>/world`) for world cards so direct id lookups against `library_index` match the watcher's canonical row id.

## Read API

Library + campaign content:

```python
await store.get_library_entity(library_id)                          # → dict | None
await store.list_library_in_world(world_id, kind=None)              # → list[dict]
await store.list_character_variants(world_id, base_id)              # variant overlays (files only)
await store.get_character_variant(world_id, base_id, variant_id)
await store.get_campaign_variant_selections(campaign_id)            # campaign.yaml `variants:` map
await store.get_override(campaign_id, library_id)                   # → dict | None (yaml patch)
await store.get_emergent(campaign_id, kind, entity_id)              # → {frontmatter, body} | None
await store.list_emergent(campaign_id, kind)
await store.get_sheet(campaign_id, kind, entity_id, mechanics_id)
await store.list_scenes(campaign_id, branch_id=None)
await store.get_scene_metadata(scene_id)
```

Composition-aware resolution:

```python
await store.resolve_entity(
    campaign_id=..., branch_id=..., kind=..., asset_id=..., world_id=None
)
```

Cascade implemented in `store.py:556`:

1. `world_id is None` → look up campaign emergent and return it with `source="campaign-emergent"`, else `None`
2. Otherwise check campaign override; if present, merge it over the base via `_resolve_world_base`
3. Base lookup: if the campaign's `campaign_world_refs.track_latest = 0` (pinned), consult `library_snapshots` first, falling through to `library_index` only as a safety net; if `track_latest = 1`, go straight to the live `library_index`
4. Returns `{source, library_id, version, frontmatter, body, ...}` or `None`

Composition + branches:

```python
await store.list_world_refs(campaign_id)
await store.list_pcs(campaign_id)
await store.branch_chain(branch_id)                            # [self, parent, ..., main]
await store.resolve_character_state(character_ref=..., branch_id=...)   # CoW walk
```

Audit + search:

```python
await store.get_delta_log(campaign_id=..., since=..., turn_id=..., include_reversed=..., limit=...)
await store.vector_search(query_vector=..., campaign_id=..., source_kinds=..., include_library=..., top_k=...)
await store.keyword_search(query=..., campaign_id=..., branch_id=..., kinds=(...), top_k=..., include_retired=...)
```

`vector_search` (`search.py:88`) builds a cosine-distance SQL query against `embeddings` filtered by `(campaign_id = ?)` and optionally `OR scope = 'library'`, returning hits in increasing distance order with `score = 1 - distance`. `keyword_search` fan-outs to `keyword_search_facts` (FTS5 over `facts_fts`) and `keyword_search_library` (FTS5 over `library_index_fts`) per kind and merges by score.

## Write API — files (mediated)

Each file-write API writes the file, upserts the appropriate index row in the same transaction, and records a reversible delta:

```python
await store.write_library_file(
    library_id=..., frontmatter=..., body=..., source=...,
    campaign_id=..., turn_id=...,
) -> FileWriteResult(library_id, path, version, delta_id)

await store.delete_library_file(library_id=..., source=..., campaign_id=...) -> delta_id

await store.write_override(campaign_id=..., library_id=..., patch=..., source=..., turn_id=...) -> Path
await store.write_emergent(campaign_id=..., kind=..., entity_id=..., frontmatter=..., body=..., source=..., turn_id=...) -> Path
await store.write_sheet(campaign_id=..., kind=..., entity_id=..., mechanics_id=..., sheet=..., source=..., turn_id=...) -> Path
await store.write_image_metadata(campaign_id=..., image_id=..., metadata=..., source=..., turn_id=...) -> Path
```

For each:

1. Compute the on-disk path via `paths.py`
2. If the target exists, capture `before = {frontmatter, body}` (markdown) or the raw YAML mapping (yaml) so the delta can reverse to the prior content
3. Write the file (`grimoire.files` handles atomic write + frontmatter serialization)
4. Open a transaction; upsert the index row (`indexers.upsert_library_index` bumps `version` only when `content_hash` changes); insert the delta with `target_scope` set to `"library"` or `"campaign-file"`; commit
5. Return the path / `FileWriteResult`

Scene file writes and post appends are owned by the Scene Manager module (`scenes/`), not the State Store. The Scene Manager uses `StateStore.db` directly for the `scenes` / `posts` rows; `write_scene_file` / `append_post_to_scene` from the spec do not exist as methods on `StateStore`.

Promotion (`promote_to_library`) lives on the domain services (`library/service.py:441`, `characters/service.py:696`, `world/service.py:687`) — they call into `StateStore.get_emergent` + `write_library_file` to do the file move + index update. Spec 03's placement of `promote_to_library` on the State Store API itself was not followed; the logic is the same but it sits one layer up.

## Write API — SQLite via deltas

```python
await store.apply_delta(delta=..., source=..., turn_id=..., branch_id=..., campaign_id=...) -> delta_id
await store.reverse_delta(delta_id) -> None
await store.queue_for_review(delta=..., source=..., campaign_id=...) -> review_id
await store.approve_review_item(review_id) -> delta_id
await store.reject_review_item(review_id, notes="") -> None
await store.get_delta_log(...)
```

`apply_delta` (`store.py:971`) accepts either a `StateDelta` pydantic model or a plain dict. The store reads `target_scope`, `target_table`, `target_id`, `after`, and either uses the provided `before` or auto-captures the current row via `_capture_current_row` so reversal can restore the prior state. Behavior by scope:

- `campaign-sqlite` → look up PK columns in `delta_log._PRIMARY_KEYS`, capture current row, then `upsert_row` against the registered table (one of `character_state`, `location_state`, `faction_state`, `facts`, `commitments`, `relationships`, `knowledge_state`, `calendar`, `images`, `scenes`, `posts`). Raises `StateStoreError` if `target_table` is missing
- `library` / `campaign-file` → the actual file write should have already happened via `write_library_file` etc; `apply_delta` just records the log row
- Anything else → `StateStoreError("unknown target_scope")`

`reverse_delta` (`store.py:1030`) dispatches on `target_scope`:

- `campaign-sqlite` → `reverse_sqlite_delta` re-upserts `before` (or `DELETE`s the row if `before` is null, meaning the row didn't exist pre-delta)
- `library` / `campaign-file` → `_reverse_file_delta` rewrites the file from `before` (or deletes it + the index row when `before` is null) using `write_yaml` / `write_markdown` and refreshes the affected index row
- Marks `deltas.reversed_at` at the end

`queue_for_review` logs the delta without applying it (notes = `"queued for review"`) and inserts a `review_queue` row with `status='pending'`. `approve_review_item` looks up the queued delta and runs the upsert for `campaign-sqlite` targets, then sets `status='approved'`. `reject_review_item` marks both the queue row rejected and the underlying delta reversed so it never shows up in active-deltas queries.

Convenience helpers like `add_fact`, `add_commitment`, `upsert_character_state`, `advance_time` are **not** exposed on `StateStore`. Continuity (`continuity/service.py`) and Time Engine (`time_engine/`) own those domain surfaces and route writes through `apply_delta` themselves.

## Composition writes

```python
await store.upsert_campaign(
    campaign_id=..., name=..., description=..., mechanics_module=..., style_guide_id=...,
    image_preset_id=..., inline_style_guide=..., content_boundaries=..., greeting_id=...,
    tags=..., config=...,
) -> None
```

Creates or updates the `campaigns` row and ensures a `{campaign_id}:main` branch exists with a deterministic SHA-256-derived seed (`_seed_for` in `store.py:1423`). Python's salted `hash()` was rejected after `3c97701` because branch RNG must be reproducible across restarts.

```python
await store.upsert_world_ref(
    campaign_id=..., world_id=..., priority=..., include=...,
    track_latest=..., bound_at_version=None,
) -> None
```

Computes `bound_at_version` from `MAX(version)` of the world's `library_index` rows when not provided. If `track_latest=False`, writes snapshots for the world into `library_snapshots` (filtered by `include`); if `track_latest=True`, removes any prior snapshots. `include=None` means "include every kind"; `include=[]` means "include nothing" (distinguished after `ad29fee`).

```python
await store.upgrade_world_ref(campaign_id=..., world_id=...) -> UpgradeReport
```

Refreshes snapshots from the current `library_index` for the world and bumps `bound_at_version` to the new max. Returns `UpgradeReport(world_id, diff={library_id: {before, after}})` for the UI to render. A `track_latest=True` ref returns an empty diff.

PCs:

```python
await store.add_pc(campaign_id=..., character_ref=..., display_name=..., owner="local")
await store.remove_pc(campaign_id=..., character_ref=...)
await store.set_active_pc(campaign_id=..., character_ref=...)
await store.list_pcs(campaign_id)
```

First PC added becomes active (`store.py:824`); `set_active_pc` is the only path that flips the bit afterwards and does it atomically (zero everyone, then set one).

## Branching and CoW reads

```python
await store.fork_branch(
    campaign_id=..., parent_branch_id=..., new_label=..., at_turn_id=None
) -> new_branch_id
```

Inserts a new `branches` row pointing at the parent and returns `"{campaign_id}:{new_label}"`. Snapshot rows are shared (no per-branch duplication).

```python
await store.branch_chain(branch_id) -> [branch_id, parent, ..., main]
await store.resolve_character_state(character_ref=..., branch_id=...) -> dict | None
```

Branch reads walk the chain until they find a row; child branches inherit parent state until they write their own. Only `character_state` has this helper today — other transient tables are queried directly through `apply_delta` flows and have not yet needed a CoW resolver.

## Library versioning + snapshots

`upsert_library_index` (`indexers.py:48`) hashes `json(frontmatter) + "\n" + body` into `content_hash`. If the existing row's hash matches, only `path` / `file_mtime` / `indexed_at` are touched and the row keeps its version. If the hash differs, `version = prior + 1` (or `1` on insert).

Snapshots (`snapshots.py`):

- `write_snapshots_for_world` copies `library_index` rows for the world into `library_snapshots`, optionally filtered by `include` (kinds). `include=None` snapshots every kind; `include=[]` writes zero rows and returns immediately
- `remove_snapshots_for_world` deletes snapshot rows for one world
- `upgrade_snapshots` records the before-versions, rewrites snapshots, then returns `{library_id: {before, after}}` for ids whose snapshot version changed

Snapshot deduplication-by-hash is left for v2 per spec; v1 stores a full copy per (campaign, branch, library_id).

## File watcher

`FileWatcher` (`watcher/watcher.py`) owns a `watchdog.observers.Observer` watching `data/library/` and `data/campaigns/` recursively. Filesystem events from the watchdog worker thread are bridged onto the owner event loop via `asyncio.run_coroutine_threadsafe`. Each path is funneled through `process_path` → `_reindex` so tests can drive the watcher synchronously without touching the OS event source.

Lifecycle:

1. `start(initial_scan=True)` ensures `data/library/` and `data/campaigns/` exist, runs `scan_now()` to sync SQLite to disk, then starts the `Observer`
2. `scan_now()` walks both roots, classifies each file with `classify_path`, calls `_reindex(watched, emit=False)` per file (so the initial scan doesn't emit thousands of per-file events), then drops orphan rows from `library_index` / `campaign_content_index` for files that no longer exist. Emits a single `library_indexed` event with file counts and embedding queue depth
3. `stop()` stops the observer and joins its worker thread

Per-file flow (`_reindex`):

- Pop any pending `expected_writes` entry for the path (registered by `register_expected_write` so the watcher knows what hash the app intended to land)
- Parse the file via `_parse_file` (markdown for entities/style-guides/emergent, YAML for worlds/image-presets/overrides/sheets/image-metadata, raw text for scene bodies)
- Compute the same content hash `upsert_library_index` computes; compare to the in-memory `_known_hashes`
- If unchanged → spurious event, return
- If changed → upsert or delete the appropriate index row, and if the kind is in `_EMBEDDABLE_KINDS` (`{library_entity, library_style_guide, emergent, scene_body}`) and body is non-empty, push an `EmbeddingJob` onto `embedding_queue`
- If `expected` was set and didn't match the new hash → flag `conflict=True` on the emitted event (last-write-wins; the user's external edit took precedence)
- Emit a typed event (`library_file_changed`, `campaign_file_changed`, `scene_file_changed`, `sheet_file_changed`, `library_indexed`) with `change_type` (`created` / `modified` / `deleted`) and the new `content_hash`

Embeddings deleted from disk are also cleared via `StateStore.delete_embeddings` so vectors don't outlive their content. The `EmbeddingQueue` is intentionally in-memory and rebuildable from a SQLite-vs-files diff on restart — no persistence required.

## Configuration

The state-store-specific config block from the spec (`state_store:` with `library_root`, `enable_wal`, etc.) is **not** exposed as a YAML config. The shipped knobs live on `grimoire.config.Settings` (`config.py`) via env vars (`GRIMOIRE_*`):

- `GRIMOIRE_DATA_ROOT` — defaults to `~/.grimoire`
- `GRIMOIRE_DATABASE_PATH` — defaults to `<data_root>/campaigns.sqlite`
- `GRIMOIRE_DB_POOL_SIZE` — defaults to 5
- `GRIMOIRE_ENABLE_WAL` — defaults to true

`vector_extension` is hard-wired to `sqlite-vec` (loaded in `db.py:_open_connection`). Watcher behaviour (`watch: true`, `scan_on_startup: true`) is unconditional today; embedding provider selection lives in the embedding plugin (not configured here).

## Error handling

- `paths.InvalidRefError` — bad library id format, unknown namespace, unsafe path component (e.g. `../`, `/`)
- `NotFoundError` — missing library file on delete, missing review item, missing parent branch on fork, missing world ref on upgrade, missing delta on reverse
- `StateStoreError` — base; raised on unknown `target_scope`, missing `target_table` for sqlite delta, double-reverse, unregistered table for upsert, double-apply of already-reviewed items
- `ConflictError` — defined but not currently raised; the file watcher surfaces conflicts as event payload (`conflict=True`) rather than throwing

Migration failures are atomic — `apply_migrations` rolls back the partial statement and leaves `schema_version` at the prior version (`03bcd23` switched from `executescript` to per-statement `BEGIN/COMMIT` to make this work). Pool acquisition rolls back leaked transactions (`659b6cd`).

## Test wiring

`backend/tests/state_store/conftest.py` provides a `store` fixture: a temp-dir `Database` + `apply_migrations(db)` + `StateStore(db, data_root)`. The suite covers path parsing, library file writes + version bumping, delta apply/reverse, review queue approve/reject, snapshot write/upgrade/remove, branch fork CoW, vector + keyword search, and override/emergent/sheet/image-metadata writes. `backend/tests/test_storage.py` covers the pool, migration runner, and the rollback-on-leak behaviour.
