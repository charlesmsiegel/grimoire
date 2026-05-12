# 03 — State Store

## Purpose

The State Store is the authoritative persistence layer. It implements Grimoire's hybrid storage model:

- **Library content** lives as markdown + YAML files under `data/library/`. Files are SSOT.
- **Campaign narrative output** (scenes, posts, overrides, emergent content, mechanical sheets, image metadata) lives as markdown + YAML files under `data/campaigns/<id>/`. Files are SSOT.
- **Campaign structured state** (facts, commitments, embeddings, audit log, transient state, library indexes and snapshots) lives in SQLite at `data/campaigns.sqlite`.

Domain modules (Library, Setting, Characters, Scene Manager, Continuity) own the semantics of their slice but route all writes through the State Store, which enforces transactionality across the two backends, maintains the library index, and supports undo, fork, and retcon.

## Storage rule

> Files for things you'd want to read, edit, grep, share, or version. SQLite for vector search, full-text search, structured-relational queries, and high-volume transient state. Files are the source of truth; SQLite is a derived cache plus a store for things that don't render as readable files.

## Responsibilities

- Maintain the library index by scanning `data/library/` at startup and via a file watcher
- Maintain the campaign content index (scenes, overrides, emergent content) similarly
- Provide read APIs that cascade across scopes (campaign-local files/SQLite → library refs → fail)
- Mediate writes:
  - File writes (library, campaign narrative): write the file, update the index synchronously
  - SQLite writes (structured campaign state): insert/update rows, log a delta
- Maintain library snapshots for version-pinned campaigns
- Maintain a delta log for every campaign change with source and reversible inverse
- Support undo, fork, and retcon
- Manage embeddings (vectors over content)
- Handle SQLite migrations
- Provide a review queue for low-confidence deltas

## Non-responsibilities

- Does not interpret state semantically (domain modules do)
- Does not assemble prompts (Context Builder does)
- Does not parse model output (Extractor does)
- Does not enforce gameplay rules (Mechanics does)
- Does not run plugins or mechanics modules (the Plugins and Mechanics modules do)
- Does not author library content (the user does, via the UI or text editor)

## On-disk layout

```
data/
├── library/                              # SSOT for content (files)
│   ├── settings/<id>/
│   │   ├── setting.yaml
│   │   ├── characters/*.md
│   │   ├── items/*.md
│   │   ├── locations/*.md
│   │   ├── lore/*.md
│   │   ├── factions/*.md
│   │   └── greetings/*.md
│   ├── style-guides/*.md
│   └── image-presets/*.yaml
│
├── campaigns/                            # SSOT for narrative output (files)
│   └── <campaign-id>/
│       ├── campaign.yaml                 # composition refs, PCs, mechanics choice
│       ├── scenes/
│       │   ├── 0001-elysium-opening.md
│       │   └── 0001-elysium-opening.yaml
│       ├── overrides/
│       │   └── settings/<setting>/<kind>/<id>.yaml
│       ├── emergent/
│       │   ├── characters/*.md
│       │   ├── items/*.md
│       │   ├── locations/*.md
│       │   ├── lore/*.md
│       │   └── factions/*.md
│       ├── sheets/                       # mechanical sheets per kind per system
│       │   ├── characters/*.<mechanics-id>.yaml
│       │   ├── items/*.<mechanics-id>.yaml
│       │   └── locations/*.<mechanics-id>.yaml
│       └── images/
│           ├── *.png
│           └── *.yaml                    # metadata sidecars
│
├── campaigns.sqlite                       # structured state, indexes, snapshots, embeddings
│
├── mechanics/                            # user-installed mechanics modules (drop-in directories)
│   └── <module-id>/
│       ├── manifest.yaml
│       ├── mechanics.py
│       ├── sheets/*.json
│       └── ...
│
└── plugins/                              # user-installed plugins (drop-in directories)
    └── <plugin-id>/
        ├── manifest.yaml
        └── <implementation files>
```

Backups are `zip data/`. Search is `rg "term" data/library/ data/campaigns/`.

## SQLite schema

All tables live in `campaigns.sqlite`. Library data is not duplicated except as the read-only library index and version-pinning snapshots.

### Library index (cache, rebuilt from files)

```sql
CREATE TABLE library_index (
  id TEXT PRIMARY KEY,                  -- composite path: "settings/wod-london/characters/alistair-hyde-smythe"
  setting_id TEXT,                      -- "wod-london" (null for top-level style-guides / image-presets)
  kind TEXT NOT NULL,                   -- 'character', 'item', 'location', 'lore', 'faction', 'greeting',
                                        --   'setting', 'style_guide', 'image_preset'
  asset_id TEXT NOT NULL,               -- "alistair-hyde-smythe"; basis for cross-setting variant lookup
  name TEXT,
  path TEXT NOT NULL,                   -- absolute file path
  frontmatter JSON NOT NULL,
  body TEXT,
  body_compressed TEXT,                 -- optional auto-summary for background-tier inclusion
  tags JSON,
  keywords JSON,
  file_mtime TIMESTAMP NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TIMESTAMP NOT NULL,
  version INTEGER NOT NULL              -- per-entity version, increments on content_hash change
);

CREATE INDEX idx_libidx_setting ON library_index(setting_id);
CREATE INDEX idx_libidx_kind ON library_index(kind);
CREATE INDEX idx_libidx_asset_id ON library_index(asset_id);   -- for cross-setting variant lookup

CREATE VIRTUAL TABLE library_index_fts USING fts5(
  name, body, tags, keywords,
  content='library_index', content_rowid='rowid'
);
```

No `family_id` column. Variant linking is by shared `asset_id` across rows.

### Campaign content index (cache, rebuilt from files)

The same idea for `data/campaigns/<id>/` content:

```sql
CREATE TABLE campaign_content_index (
  id TEXT PRIMARY KEY,                  -- composite path
  campaign_id TEXT NOT NULL,
  kind TEXT NOT NULL,                   -- 'scene', 'override', 'emergent', 'sheet', 'image'
  entity_subkind TEXT,                  -- for emergent/override/sheet: 'character', 'item', 'location', ...
  asset_id TEXT,                        -- e.g., scene id, character id
  path TEXT NOT NULL,
  frontmatter JSON,
  body TEXT,
  file_mtime TIMESTAMP NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_ccidx_campaign ON campaign_content_index(campaign_id);
CREATE INDEX idx_ccidx_kind ON campaign_content_index(kind);
```

### Library snapshots (for version-pinned campaigns)

```sql
CREATE TABLE library_snapshots (
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  library_id TEXT NOT NULL,            -- corresponds to library_index.id
  version INTEGER NOT NULL,
  frontmatter JSON NOT NULL,
  body TEXT,
  snapshot_at TIMESTAMP NOT NULL,
  PRIMARY KEY (campaign_id, branch_id, library_id)
);
```

Snapshots are written when a campaign binds to a setting at a version. Pinned reads consult snapshots first; `track_latest` campaigns consult the live index.

### Campaign

```sql
CREATE TABLE campaigns (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  mechanics_module TEXT,                -- 'wod-mechanics', 'another-campaign-mechanics', or NULL for narrative
  style_guide_id TEXT,                  -- library asset id, or NULL
  image_preset_id TEXT,                 -- library asset id, or NULL
  inline_style_guide TEXT,              -- alternative to library style guide
  content_boundaries TEXT,
  greeting_id TEXT,                     -- starting greeting (library), or NULL
  created_at TIMESTAMP NOT NULL,
  last_played_at TIMESTAMP,
  config JSON
);

CREATE TABLE campaign_setting_refs (
  campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
  setting_id TEXT NOT NULL,
  priority INTEGER NOT NULL,            -- 1 = highest
  include JSON NOT NULL,                -- ['characters', 'items', 'locations', 'lore', 'factions', 'greetings']
  bound_at_version INTEGER NOT NULL,
  track_latest BOOLEAN DEFAULT FALSE,
  bound_at TIMESTAMP NOT NULL,
  PRIMARY KEY (campaign_id, setting_id)
);

CREATE TABLE campaign_pcs (
  campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
  character_ref TEXT NOT NULL,         -- library or campaign-local ref
  display_name TEXT NOT NULL,
  owner TEXT NOT NULL,                  -- 'local' in v1; account id in v2
  active BOOLEAN DEFAULT TRUE,
  added_at TIMESTAMP NOT NULL,
  PRIMARY KEY (campaign_id, character_ref)
);

CREATE TABLE branches (
  id TEXT PRIMARY KEY,
  campaign_id TEXT REFERENCES campaigns(id),
  parent_branch_id TEXT REFERENCES branches(id),
  forked_from_turn_id TEXT,             -- NULL for main
  label TEXT,
  rng_seed INTEGER NOT NULL,            -- per-branch seed for deterministic rolls
  created_at TIMESTAMP
);
```

### Transient entity state (campaign-only)

Per-character, per-location, per-faction runtime state. Too high-write to be files; lives in SQLite.

```sql
CREATE TABLE character_state (
  character_ref TEXT NOT NULL,          -- 'library:settings/<setting>/characters/<id>' or 'campaign:emergent/<id>'
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  location_ref TEXT,
  emotional_state TEXT,
  physical_state TEXT,
  immediate_intent TEXT,
  knowledge_state JSON,
  last_action TEXT,
  last_screen_time_turn TEXT,
  visible_to_pc BOOLEAN,
  drift_score REAL DEFAULT 0,
  tier_pin TEXT,
  current_scene_id TEXT,                -- per-PC scene tracking
  updated_at_turn TEXT,
  PRIMARY KEY (character_ref, branch_id)
);

CREATE TABLE location_state (
  location_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  weather JSON,
  time_of_day TEXT,
  occupants JSON,
  condition TEXT,
  transient_features JSON,
  updated_at_turn TEXT,
  PRIMARY KEY (location_ref, branch_id)
);

CREATE TABLE faction_state (
  faction_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  state JSON,
  updated_at_turn TEXT,
  PRIMARY KEY (faction_ref, branch_id)
);
```

### Play history derived data

Scenes and posts live as files. Indexes in SQLite for query:

```sql
CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,             -- 0001, 0002, ...
  slug TEXT NOT NULL,                   -- 'elysium-opening'
  file_path TEXT NOT NULL,              -- 'campaigns/<c>/scenes/0001-elysium-opening.md'
  location_ref TEXT,
  in_game_start TIMESTAMP,
  in_game_end TIMESTAMP,
  pov_character_ref TEXT,
  present_character_refs JSON,
  present_pc_refs JSON,                 -- subset of present that are PCs; determines advance trigger
  summary TEXT,
  running_summary TEXT,
  key_beats JSON,
  tags JSON,
  emotional_arc TEXT,
  post_count INTEGER DEFAULT 0,
  threads_introduced JSON,
  threads_paid_off JSON,
  title TEXT,
  greeting_id TEXT,
  closed BOOLEAN DEFAULT FALSE,
  closed_at_turn TEXT
);

CREATE TABLE posts (
  id TEXT PRIMARY KEY,
  scene_id TEXT REFERENCES scenes(id),
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  turn_id TEXT,
  order_in_scene INTEGER,
  author_kind TEXT,                     -- 'pc', 'narrator', 'npc', 'system'
  author_pc_ref TEXT,                   -- if author_kind='pc'
  body_excerpt TEXT,                    -- short prefix for queries; full body is in the scene file
  body_hash TEXT,                       -- for change detection
  is_player BOOLEAN,
  created_at TIMESTAMP,
  retconned_from TEXT REFERENCES posts(id)
);
```

The full prose of each post lives in the scene's markdown file. Posts table is for query and audit; full content is in the file.

### Continuity

```sql
CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  text TEXT NOT NULL,
  established_in_post TEXT REFERENCES posts(id),
  in_game_when TIMESTAMP,
  about JSON,
  source TEXT,
  speaker_ref TEXT,
  confidence REAL,
  keywords JSON,
  retired BOOLEAN DEFAULT FALSE,
  retired_in_post TEXT REFERENCES posts(id),
  contradicts JSON,
  tags JSON
);

CREATE TABLE commitments (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  kind TEXT,
  text TEXT,
  from_character_ref TEXT,
  to_character_ref TEXT,
  due_by TIMESTAMP,
  status TEXT,                          -- OPEN, PAID, BROKEN, STALE, OVERDUE
  weight INTEGER,
  created_in_post TEXT,
  in_game_created_at TIMESTAMP,
  resolved_in_post TEXT,
  tags JSON,
  related_fact_ids JSON
);

CREATE TABLE relationships (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  from_character_ref TEXT,
  to_character_ref TEXT,
  types JSON,
  state JSON,
  updated_at_turn TEXT
);

CREATE TABLE knowledge_state (
  fact_id TEXT REFERENCES facts(id),
  character_ref TEXT,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  knows BOOLEAN,
  learned_in_post TEXT,
  source TEXT,
  PRIMARY KEY (fact_id, character_ref, branch_id)
);

CREATE TABLE calendar (
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  current_in_game_time TIMESTAMP,
  PRIMARY KEY (branch_id)
);
```

### Images (metadata; files in `data/campaigns/<id>/images/`)

```sql
CREATE TABLE images (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  scene_id TEXT REFERENCES scenes(id),
  post_id TEXT REFERENCES posts(id),
  file_path TEXT NOT NULL,              -- relative path under campaigns/<c>/images/
  thumbnail_path TEXT,
  prompt TEXT,
  negative_prompt TEXT,
  params JSON,
  backend TEXT,
  model TEXT,
  seed INTEGER,
  created_at TIMESTAMP,
  user_starred BOOLEAN,
  tags JSON
);
```

### Audit, review, embeddings

```sql
CREATE TABLE deltas (
  id TEXT PRIMARY KEY,
  campaign_id TEXT,
  branch_id TEXT,
  turn_id TEXT,
  source TEXT,
  kind TEXT,
  target_scope TEXT,                    -- 'library', 'campaign-file', 'campaign-sqlite'
  target_table TEXT,                    -- for sqlite targets
  target_path TEXT,                     -- for file targets
  target_id TEXT,
  before JSON,
  after JSON,
  confidence REAL,
  applied_at TIMESTAMP,
  reversed_at TIMESTAMP,
  notes TEXT
);

CREATE TABLE review_queue (
  id TEXT PRIMARY KEY,
  delta_id TEXT REFERENCES deltas(id),
  campaign_id TEXT,
  status TEXT,
  reviewed_at TIMESTAMP,
  reviewer_notes TEXT
);

CREATE TABLE embeddings (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,                  -- 'library' or 'campaign'
  ref TEXT NOT NULL,                    -- library_id, or compound 'campaign:scene:<id>', 'campaign:post:<id>', etc.
  source_kind TEXT,                     -- 'post', 'scene_summary', 'character', 'lore', 'fact', ...
  text TEXT,
  vector BLOB,                          -- via sqlite-vec
  embedded_at TIMESTAMP,
  model TEXT,
  campaign_id TEXT                      -- non-null for campaign-scoped embeddings
);

CREATE INDEX idx_emb_scope ON embeddings(scope);
CREATE INDEX idx_emb_campaign ON embeddings(campaign_id);
```

Vector search filters by scope: queries within a campaign return campaign-scoped embeddings plus library-scoped embeddings filtered by the campaign's composition.

## Read APIs

```python
class StateStore(Protocol):
    # Library
    async def get_library_entity(self, library_id: str) -> Optional[LibraryEntity]: ...
    async def list_library_in_setting(self, setting_id: str, kind: str) -> list[LibraryEntity]: ...
    async def query_library(self, predicate: dict) -> list[LibraryEntity]: ...
    async def variants_of(self, asset_id: str, kind: str) -> list[LibraryEntity]: ...

    # Campaign content
    async def get_scene_file(self, scene_id: str) -> SceneFile: ...
    async def get_scene_metadata(self, scene_id: str) -> dict: ...
    async def list_scenes(self, campaign_id: str, branch_id: str) -> list[Scene]: ...

    async def get_emergent(self, campaign_id: str, kind: str, id: str) -> Optional[dict]: ...
    async def list_emergent(self, campaign_id: str, kind: str) -> list[dict]: ...

    async def get_override(self, campaign_id: str, library_id: str) -> Optional[dict]: ...

    async def get_sheet(self, campaign_id: str, kind: str, entity_id: str, mechanics_id: str) -> Optional[dict]: ...

    # Composition-aware resolution
    async def resolve_character(self, character_ref: str, campaign_id: str, branch_id: str) -> ResolvedCharacter: ...
    async def resolve_location(self, ...) -> ResolvedLocation: ...
    async def resolve_entity(self, kind: str, ref: str, campaign_id: str, branch_id: str) -> ResolvedEntity: ...

    async def list_for_campaign(
        self,
        campaign_id: str,
        kind: str,
        filter: dict = {},
    ) -> list[ResolvedEntity]: ...

    # Retrieval
    async def vector_search(
        self,
        query_text: str,
        campaign_id: str,
        kinds: list[str] = ['post', 'scene_summary'],
        include_library: bool = True,
        top_k: int = 8,
    ) -> list[SearchResult]: ...

    async def keyword_search(
        self,
        terms: list[str],
        campaign_id: str,
        kinds: list[str] = ['fact'],
        top_k: int = 5,
    ) -> list[SearchResult]: ...

    # Audit
    async def get_delta_log(
        self,
        campaign_id: Optional[str] = None,
        since: Optional[datetime] = None,
        turn_id: Optional[str] = None,
    ) -> list[AppliedDelta]: ...
```

## Write APIs

```python
class StateStore(Protocol):
    # Library writes (mediated; writes the file, updates the index)
    async def write_library_file(
        self,
        library_id: str,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> int:                            # returns new version
        ...
    async def delete_library_file(self, library_id: str, source: str) -> None: ...

    # Campaign narrative file writes (scenes, overrides, emergent, sheets, image metadata)
    async def write_scene_file(
        self,
        campaign_id: str,
        scene_id: str,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> None: ...
    async def append_post_to_scene(
        self,
        scene_id: str,
        post: Post,
        source: str,
    ) -> None: ...
    async def write_override(
        self,
        campaign_id: str,
        library_id: str,
        patch: dict,
        source: str,
    ) -> None: ...
    async def write_emergent(
        self,
        campaign_id: str,
        kind: str,
        entity_id: str,
        frontmatter: dict,
        body: str,
        source: str,
    ) -> None: ...
    async def write_sheet(
        self,
        campaign_id: str,
        kind: str,
        entity_id: str,
        mechanics_id: str,
        sheet: dict,
        source: str,
    ) -> None: ...
    async def write_image_metadata(
        self,
        image_id: str,
        metadata: dict,
        source: str,
    ) -> None: ...

    # Promotion (file move + index update + ref rewrite)
    async def promote_to_library(
        self,
        campaign_id: str,
        kind: str,
        campaign_entity_id: str,
        target_setting_id: str,
        source: str,
    ) -> str: ...

    # SQLite writes (facts, commitments, deltas, state)
    async def apply_delta(self, delta: StateDelta, source: str) -> AppliedDelta: ...
    async def reverse_delta(self, delta_id: str) -> None: ...
    async def queue_for_review(self, delta: StateDelta, source: str) -> ReviewItem: ...

    async def add_fact(self, fact: Fact, source: str) -> None: ...
    async def add_commitment(self, c: Commitment, source: str) -> None: ...
    async def upsert_character_state(self, state: CharacterState, source: str) -> None: ...
    async def upsert_location_state(self, state: LocationState, source: str) -> None: ...
    async def upsert_faction_state(self, state: FactionState, source: str) -> None: ...
    async def advance_time(self, to: datetime, branch_id: str, source: str) -> None: ...

    # Composition
    async def upsert_setting_ref(
        self,
        campaign_id: str,
        setting_id: str,
        priority: int,
        include: list[str],
        track_latest: bool,
    ) -> None: ...
    async def upgrade_setting_ref(
        self,
        campaign_id: str,
        setting_id: str,
    ) -> UpgradeReport: ...

    # PCs
    async def add_pc(
        self,
        campaign_id: str,
        character_ref: str,
        display_name: str,
        owner: str,
    ) -> None: ...
    async def remove_pc(self, campaign_id: str, character_ref: str) -> None: ...
```

## File watcher

Python `watchdog` monitors `data/library/` and `data/campaigns/`. On change:

- File created → parse, insert index row, queue embedding, emit event
- File modified → parse, update index, re-embed if body changed, emit event
- File deleted → remove index row, mark embeddings stale, emit event

Events: `library_file_changed`, `library_indexed`, `campaign_file_changed`, `scene_file_changed`, `sheet_file_changed`.

The watcher uses content_hash to detect actual changes (file mtime can change without content change).

## Indexing pipeline

On startup:

```
1. Read schema_version; run pending migrations.
2. Walk data/library/ and data/campaigns/ recursively.
3. For each file:
   a. Compute content_hash.
   b. Check appropriate index for an existing row.
   c. If hash matches, skip.
   d. Otherwise, parse frontmatter + body.
   e. UPSERT into the index with bumped version.
   f. Schedule embedding (queued; non-blocking).
4. Delete index rows for files that no longer exist.
5. Start the file watcher.
```

Initial scan is fast; embedding can take time and runs in the background with progress shown in UI.

## Library versioning

Each `library_index.version` increments when `content_hash` changes. A setting's version is `max(version)` across all its entities.

Each `campaign_setting_refs.bound_at_version` records the setting version at bind time. `track_latest` campaigns ignore this and read live; pinned campaigns consult `library_snapshots`.

## Snapshots for pinned campaigns

When a campaign binds a setting with `track_latest = false`:

```
For each entity in the setting (subject to the ref's include filter):
  Copy frontmatter + body from library_index into library_snapshots,
  keyed by (campaign_id, branch_id, library_id).
```

Reads against a pinned campaign go to snapshots first; fall through to `library_index` only as a safety net.

Upgrade: refresh snapshots from current `library_index`; bump `bound_at_version`. The Frontend shows a diff before applying.

Storage cost: pinned snapshots can duplicate a lot of content. Deduplicate-by-content-hash is a v2 optimization.

## Branching and forking

Branches are within a campaign. Forking creates a new branch with copy-on-write semantics:

- New branch row, pointing to parent
- Reads on the new branch fall back to parent for rows that haven't been changed on the new branch
- Writes on the new branch insert with the new `branch_id`

The query layer handles fallback. Branches do not duplicate library snapshots — both branches reference the same snapshot rows.

Scene files for the new branch are copied lazily: when a scene is edited on the new branch, it's copied into the new branch's scene directory (or kept in a shared location with branch annotation in frontmatter — implementation detail).

## Undo

Pops the last N turns and reverses their deltas in reverse order:

- Posts and scenes are reverted (file content rolled back via delta log)
- SQLite rows are reverted
- Embeddings for deleted content are removed
- Time Engine rolls the calendar back

Library file edits are deltas too (target_scope = `library`); undoing them is a separate confirmation because library state is shared across campaigns.

## Retcon

User edits a past post. The State Store:

1. Marks the original post as retconned, inserts the new version
2. Updates the scene file (rewrites the section)
3. Identifies all deltas sourced from that post
4. Reverses those deltas
5. Re-runs the Extractor on the edited text
6. Applies new deltas
7. Flags downstream turns for review

Library entities are not retconned this way; they're explicitly edited files.

## Review queue

Low-confidence extracted deltas go to review. Frontend surfaces a badge. User can approve, reject, or edit each. Approved deltas apply; rejected discarded; edited apply at confidence 1.0.

## Configuration

```yaml
state_store:
  library_root: ./data/library
  campaigns_root: ./data/campaigns
  database_path: ./data/campaigns.sqlite
  enable_wal: true
  vector_extension: sqlite-vec

  library:
    watch: true
    scan_on_startup: true
    embed_on_index: true
    embedding_batch_size: 50
    embedding_provider: sentence-transformers   # references the active embedding plugin

  snapshots:
    enabled: true
    deduplicate_by_hash: false                  # v2 optimization

  auto_backup:
    enabled: true
    interval_hours: 24
    retention_count: 14
    includes: [library, campaigns, sqlite]

  retention:
    embeddings_for_retired_facts: 90d
    delta_log: forever
```

## Open questions (deferred)

- **Single SQLite file vs. per-campaign databases.** v1 uses one shared file. Per-campaign would simplify sharing but complicate cross-campaign queries. Single file wins for now.
- **Snapshot deduplication.** Content-addressed snapshot store (keyed by hash) is a clear v2 optimization.
- **Concurrent edits to a library file.** Last-write-wins with warning in v1; collaborative merge in v2.
- **Scene file branching.** Lazy copy vs. annotation-in-frontmatter — implementation detail to be decided during build.
- **Large library indexing on startup.** Parsing is fast; embedding can be slow. Background embedding with UI progress.
- **Cross-library multi-root setups.** v1 has one library root; v2 may support multi-root via a `library_root` qualifier.
- **File watcher race conditions.** User edits a file while the app is also writing — last-write-wins detection via content_hash; emit a conflict warning.
