# Library — Design (Shipped)

> Captures the Library design as actually built. The matching "remaining" spec at `2026-05-16-library-remaining-design.md` covers everything from the original `specs/18-library.md` that did **not** land in this work.

**Commit:** `ab0628d` — "Implement Library indexer and Library protocol (task 10)" (followed by `ffdef5f`, `ad29fee`, `87e0643`, `6e512c4`)
**Module:** `backend/src/grimoire/library/`
**Related modules:** `backend/src/grimoire/watcher/`, `backend/src/grimoire/files/`, `backend/src/grimoire/state_store/`
**Tests:** `backend/tests/library/test_service.py`, `backend/tests/watcher/`

## Purpose

The Library holds the user-authored content campaigns play with: worlds (each containing characters, items, locations, lore, factions, greetings) plus top-level style guides and image presets. Files on disk under `data/library/` are the source of truth; SQLite's `library_index` is a cache the watcher keeps in sync.

The Library service wraps `StateStore` with a domain-shaped API: callers receive typed `LibraryEntity` / `WorldMeta` / `Greeting` / `ResolvedEntity` values and never touch raw rows. All writes go through file-mediating `StateStore` methods so the file and the index land atomically.

## Module surface

`LibraryService` (`library/service.py`) is constructed with a single dependency:

- `store: StateStore` — owns SQLite mutations, file mediation, snapshots, and the resolve-cascade base.

Everything else (file watching, embedding fan-out, world fork, calendar/weather) lives in collaborating modules:

- `watcher/` — `FileWatcher` + `classify_path` + `EmbeddingQueue`. The watcher both does the initial scan and the live event loop; the Library service does not poll the filesystem itself.
- `files/` — `read_markdown` / `write_markdown` / `parse_frontmatter` / `load_yaml` / `write_yaml` / `content_hash`. All file I/O is UTF-8.
- `world/` — `WorldService.fork_world` (directory copy + reindex), spatial/weather/calendar behaviors, and the world-side wrapper around `promote_to_library`.
- `api/library.py` — REST routes that wrap the service for the frontend.

## Public API

```python
class LibraryService:
    # Discovery / listing
    async def list_worlds() -> list[WorldMeta]
    async def get_world(world_id) -> WorldMeta
    async def list_in_world(world_id, kind) -> list[LibraryEntity]
    async def get_entity(world_id, kind, entity_id) -> LibraryEntity

    # Top-level assets
    async def list_style_guides() -> list[LibraryEntity]
    async def get_style_guide(id) -> LibraryEntity
    async def create_style_guide(id, *, name, description="", tags=None,
                                  pacing=None, voice=None, themes=None,
                                  avoid=None, source="user") -> LibraryEntity
    async def parse_style_guide(id) -> dict          # editable shape for the form
    async def update_style_guide(id, *, name=None, ...) -> LibraryEntity
    async def list_image_presets() -> list[LibraryEntity]
    async def get_image_preset(id) -> LibraryEntity

    # Greetings
    async def list_greetings(world_id) -> list[Greeting]
    async def get_greeting(world_id, id) -> Greeting

    # Character variants (in-world diff overlays)
    async def list_character_variants(world_id, character_id) -> list[CharacterVariant]
    async def get_character_variant(world_id, character_id, variant_id) -> CharacterVariant
    async def upsert_character_variant(world_id, character_id, variant_id, *, label=None, frontmatter=None, body="") -> CharacterVariant
    async def delete_character_variant(world_id, character_id, variant_id) -> None

    # Writes (file + index, mediated by StateStore)
    async def create_world(id, meta, *, source="user") -> WorldMeta
    async def create_entity(world_id, kind, entity_id, frontmatter, body,
                            *, source="user") -> LibraryEntity
    async def update_entity(world_id, kind, entity_id,
                            frontmatter_patch=None, body=None,
                            *, source="user") -> LibraryEntity
    async def delete_entity(world_id, kind, entity_id, *, source="user") -> None

    # Promotion
    async def promote_to_library(campaign_id, entity_kind, campaign_entity_id,
                                  target_world_id, *, source="user") -> str

    # Composition
    async def get_composition(campaign_id) -> Composition
    async def set_composition(campaign_id, composition) -> None
    async def upgrade_world_ref(campaign_id, world_id) -> UpgradeReport

    # Resolution (used by World, Characters, Context Builder)
    async def resolve(entity_id, campaign_id) -> ResolvedEntity

    # Dependents (who's using this library entity)
    async def dependents(world_id, kind, entity_id) -> list[CampaignRef]

    # Composition-aware listing
    async def list_for_composition(campaign_id, kind) -> list[LibraryEntity]
```

`update_world` and `delete_world` are not exposed on the service — they live on `WorldService` (`world/service.py`) because they need world-scoped cleanup. `WorldService.fork_world` copies a world directory and reindexes via the typed write API. The API router (`api/library.py`) wires both services together behind a single REST surface.

## File layout (as enforced by `state_store/paths.py`)

```
data/library/
├── worlds/<world>/world.yaml
├── worlds/<world>/{characters,items,locations,lore,factions,greetings}/<id>.md
├── style-guides/<id>.md
└── image-presets/<id>.yaml
```

`KIND_TO_DIR` (singular → plural) and the inverse `DIR_TO_KIND` map at `state_store/paths.py:38` are the single source of truth for kind/directory mapping. The classifier (`watcher/classifier.py`) and the resolver share them. Every interpolated id (campaign, world, asset, mechanics, image) runs through `_validate_path_component` (`state_store/paths.py:57`) which rejects anything that isn't `[A-Za-z0-9][A-Za-z0-9._-]*` so untrusted input can't escape the data root.

## SQLite schema (migration `001_indexes.sql`)

```sql
CREATE TABLE library_index (
  id TEXT PRIMARY KEY,            -- 'worlds/<w>/<dir>/<asset>', 'style-guides/<id>', etc.
  world_id TEXT,                  -- renamed from setting_id in migration 014
  kind TEXT NOT NULL,             -- singular: 'character', 'world', 'style_guide', ...
  asset_id TEXT NOT NULL,
  name TEXT,
  path TEXT NOT NULL,             -- relative to data_root
  frontmatter TEXT NOT NULL,      -- JSON
  body TEXT,
  body_compressed TEXT,           -- column exists; never written today
  tags TEXT,                      -- JSON
  keywords TEXT,                  -- JSON
  file_mtime TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TEXT NOT NULL,
  version INTEGER NOT NULL
);

CREATE INDEX idx_libidx_world ON library_index(world_id);   -- post-rename
CREATE INDEX idx_libidx_kind ON library_index(kind);
CREATE INDEX idx_libidx_asset_id ON library_index(asset_id);

CREATE VIRTUAL TABLE library_index_fts USING fts5(
  name, body, tags, keywords,
  content='library_index', content_rowid='rowid'
);
-- Triggers keep FTS in sync on insert/update/delete.

CREATE TABLE library_snapshots (
  campaign_id TEXT, branch_id TEXT, library_id TEXT,
  version INTEGER NOT NULL,
  frontmatter TEXT NOT NULL, body TEXT,
  snapshot_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, branch_id, library_id)
);
```

A composite `library_id` is the canonical row key. `make_library_id(world_id, kind, asset_id)` and `parse_library_id(library_id)` (both in `state_store/`) are used everywhere ids are constructed or destructured.

## Write path

`LibraryService` writes always go through `StateStore.write_library_file` (`state_store/store.py:160`):

1. Read prior file content (markdown frontmatter or YAML) into a `before_payload` for the delta log
2. Write the file: YAML for worlds and image presets, markdown + YAML frontmatter for everything else
3. Inside a single SQLite transaction:
   - `upsert_library_index` bumps `version` by 1 if `content_hash` changed; otherwise just refreshes `path` / `file_mtime` / `indexed_at`
   - `insert_delta` records a `library_file_write` row in the delta log with the before/after payloads so the write is reversible

`delete_library_file` is symmetrical: captures the deleted content into `before`, unlinks the file, deletes the index row inside one transaction, records a `library_file_delete` delta.

Style guides have a richer write path: `create_style_guide` / `update_style_guide` (`library/service.py:213`, `library/service.py:268`) render the bulleted Pacing / Voice / Themes / Avoid sections into markdown via `_render_style_guide_body`, and the parse helper `_parse_style_guide_body` round-trips them out for the edit form (preserving any unknown `## Heading` blocks verbatim so hand-edited prose isn't silently dropped).

## File watcher (`watcher/watcher.py`)

`FileWatcher` owns a `watchdog.observers.Observer` over `data/library/` and `data/campaigns/`. Events come in on watchdog's worker thread and are bridged back to the owner event loop via `asyncio.run_coroutine_threadsafe` (`_schedule_from_thread`). Every event is funneled through `process_path` so tests can drive the watcher synchronously without an OS event source.

For each path:

1. `classify_path(data_root, path)` returns a `WatchedFile` (or `None` for uninteresting paths) carrying scope, kind, ids, and the composite `library_id` / `content_index_id`. The classifier knows the full library + campaign layout (`watcher/classifier.py:85`).
2. `_reindex(watched, emit=...)`:
   - Pops any pre-registered "expected write" hash so the next event after an internal write doesn't false-flag as a conflict
   - Parses the file (`read_markdown` for entity / style-guide / emergent kinds, `load_yaml` for worlds / presets / overrides / sheets / image metadata / campaign config, raw text for scene bodies)
   - Computes a `content_hash` over the same JSON-sorted `frontmatter + body` shape `upsert_library_index` will hash. If it matches the in-memory `_known_hashes[path]`, the event is a spurious touch and is dropped without an index hit
   - Otherwise upserts into `library_index` or `campaign_content_index` inside a `BEGIN ... COMMIT` block via the appropriate `state_store/indexers.py` helper; or deletes the row + best-effort `store.delete_embeddings(ref)` on file deletion
   - If `emit=True`, fires a typed event on the bus (`library_file_changed`, `campaign_file_changed`, `scene_file_changed`, `sheet_file_changed`) carrying `change_type` (`created` / `modified` / `deleted`), `content_hash`, and any conflict flag.
3. Prose-bearing kinds (`library_entity`, `library_style_guide`, `emergent`, `scene_body`) are also enqueued onto `EmbeddingQueue` so a downstream worker can compute vectors out-of-band.

`scan_now` walks both roots, indexes everything it finds, drops orphan rows whose files have vanished, and emits a single `library_indexed` event at the end with `library_files` / `campaign_files` / `embedding_queue_depth` counts. Live filesystem events are suppressed during the scan to avoid a thundering herd.

### Write-conflict detection

`register_expected_write(path, expected_hash)` lets a writer tell the watcher "the next event for this path should land on this hash". Mismatch ⇒ the watcher still reindexes what's on disk (last-write-wins) but flags `conflict=True` in the emitted event so downstream UI can warn. `_reindex` pops the expectation up front so a parse error or spurious dedup doesn't leave a stale expectation that would mis-flag a later real edit.

## Resolution cascade

`LibraryService.resolve(entity_id, campaign_id)` (`library/service.py:590`) accepts:

- A composite `library_id` (`worlds/<world>/<kind>/<asset>`)
- A campaign-local shorthand (`emergent/<kind>/<asset>`)

For composite ids the walk is:

1. **Campaign-local emergent** (name-shadowed) — `store.get_emergent(campaign_id, kind, asset_id)`. Found ⇒ build a `ResolvedEntity` with `layer=EMERGENT`.
2. **`StateStore.resolve_entity`** — `state_store/store.py` walks: campaign override (if present) merged on top of base; for the base, if the campaign pins the world (`track_latest=False`) it reads `library_snapshots` first and falls back to `library_index` only as a safety net (`library-fallback`), otherwise it reads `library_index` directly (`library-live`).
3. Map the source string to a `ResolutionLayer` (`EMERGENT` / `OVERRIDE` / `LIBRARY_SNAPSHOT` / `LIBRARY_LIVE`) and return a `ResolvedEntity` with a single-element `source_chain`. `overrides_applied` lists `["override"]` when the override layer contributed.

For `emergent/<kind>/<id>` the cascade is shorter — emergent-only. Top-level kinds (`world`, `style_guide`, `image_preset`) are rejected; use the dedicated getters.

## Composition

`Composition` (`types/composition.py`) holds the per-campaign reference set:

```python
Composition(
    worlds=list[WorldRef],          # priority-ordered
    mechanics=str | None,
    style_guide_id=str | None,
    image_preset_id=str | None,
    inline_style_guide=str | None,  # extension over spec 18
    content_boundaries=str | None,  # extension over spec 18
)

WorldRef(
    world_id, priority, bound_at_version, track_latest,
    include: list[str] | None,      # None = "include every kind"; [] = "include none"
)
```

The `include is None` vs `include == []` distinction is intentional and documented at `library/service.py:62`: a wizard that uncheck-all-kinds yields `[]` and excludes the world entirely instead of (silently) including everything.

### Pinning + snapshots (`state_store/snapshots.py`)

`set_composition` → `upsert_world_ref` per ref. When `track_latest=False`, the store calls `write_snapshots_for_world` which copies every (or filtered-by-`include`) `library_index` row for that world into `library_snapshots` keyed by `(campaign_id, branch_id, library_id)`. When the user flips a ref to `track_latest=True`, `remove_snapshots_for_world` deletes the snapshots so reads fall through to the live index.

`upgrade_world_ref` rebuilds snapshots from current `library_index` content and returns an `UpgradeReport(from_version, to_version, changed_entities, diff)` where `diff` is `{library_id: {"before": v, "after": v}}` for rows whose version changed. The campaign's `bound_at_version` is updated to the world's current max.

Snapshot storage cost: pinned snapshots duplicate library content per campaign+branch. Content-addressed deduplication is deferred (see remaining doc).

## Promotion (campaign-local → library)

`LibraryService.promote_to_library(campaign_id, entity_kind, campaign_entity_id, target_world_id)` (`library/service.py:441`):

1. Reads the emergent file via `store.get_emergent`
2. Writes it to `data/library/worlds/<target>/<kind>/<id>.md` through `store.write_library_file` with `source="<source>:promotion"` and `campaign_id` recorded on the delta
3. Returns the on-disk path
4. The watcher picks up the new file and adds the `library_index` row (the write also did so inside the same transaction).

The emergent file is **left in place**; the spec's "replace campaign-local record with a reference / convert to override" step is **not** implemented yet (see remaining doc). Characters are routed through the Characters module (`world/service.py:687` rejects `character`).

## Character variants

Variants are in-world diff overlays at `characters/<id>/variants/<vid>.md` (#579): frontmatter holds only the fields that differ from the base plus the reserved `label`; a non-empty body replaces the base prose. They are read from disk (never indexed in `library_index`); the base character must exist, and the reserved `id` key is dropped so a variant can't change identity. A campaign selects one per character via `campaign.yaml` `variants:`; `StateStore.resolve_entity` applies base → variant diff → override. There is no cross-world linkage — the same id in two worlds is two unrelated entities.

## Configuration

There is no `library:` section in `config.py` yet — `data_root` (default `~/.grimoire`, overridable via `GRIMOIRE_DATA_ROOT`) is the only library-related setting and it implicitly resolves to `<data_root>/library`. The watcher always starts (no `library.watch` toggle), always does the initial scan (no `scan_on_startup` toggle), and uses incremental hashing by default. The other spec 18 knobs (`embed_on_index`, `embedding_provider`, `default: pinned`, filename patterns, `confirm_required`) are unimplemented — see the remaining doc.

## Events emitted

By the watcher:

- `library_file_changed` — payload: `scope`, `kind`, `path`, `change_type` (`created` / `modified` / `deleted`), `content_hash`, `conflict`, plus any of `library_id` / `world_id` / `entity_kind` / `asset_id`
- `library_indexed` — payload: `library_files`, `campaign_files`, `embedding_queue_depth` (one emit per `scan_now` completion)
- `campaign_file_changed`, `scene_file_changed`, `sheet_file_changed` — sibling events for non-library kinds

## Error handling

- Invalid path components (anything not matching `[A-Za-z0-9][A-Za-z0-9._-]*`) → `InvalidRefError` from `state_store/paths.py`. Surfaced as a 4xx by the API layer.
- Missing world / entity / style guide / preset / greeting → `LibraryNotFoundError`.
- `create_style_guide` collision → `LibraryConflictError`.
- `create_entity` with a non-world-scoped kind → `LibraryError`.
- `promote_to_library` for a top-level kind or missing emergent → `PromotionError`.
- Watcher parse failures (malformed YAML or frontmatter) → logged at WARNING; the path is skipped and the existing index row (if any) is left alone. No event emitted.
- Watcher delete with a still-pending expected-write hash → the expectation is consumed up front, so it can't leak.

## Test wiring

`backend/tests/library/test_service.py` constructs `LibraryService(store)` directly with a real `StateStore` over a temp `data_root` and exercises the full read / write / cascade / composition / promotion surface (35 tests). `backend/tests/watcher/` covers `classify_path` (16 cases for path → kind), `process_path` end-to-end indexing for every kind, embedding-queue enqueue rules, `scan_now` orphan cleanup, the live `watchdog.Observer` round-trip, and the expected-write conflict semantics.
