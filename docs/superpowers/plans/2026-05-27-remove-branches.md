# Remove Branch Functionality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove per-campaign branching (copy-on-write timelines) from the entire codebase, since campaign forking already provides this functionality without the pervasive complexity of `branch_id` composite keys.

**Architecture:** A single database migration recreates all 21 affected tables without `branch_id` columns, copying only `*:main` branch rows. The service layer then drops all `branch_id` parameters, CoW resolution, branch-chain walks, and branch fork logic. Campaign forking (full independent campaign copy) is preserved and already has lineage tracking via migration 028.

**Tech Stack:** Python 3.12+, FastAPI, SQLite, Pydantic, TypeScript, React

---

## File Map

### Migration
- Create: `backend/src/grimoire/storage/migrations/036_remove_branches.sql`

### Types (remove BranchId)
- Modify: `backend/src/grimoire/types/common.py` — remove `BranchId` type alias
- Modify: `backend/src/grimoire/types/scene.py` — remove `branch_id` from `Scene`, `SceneInit`
- Modify: `backend/src/grimoire/types/protocols.py` — remove `branch_id` from all protocol method signatures

### State Store
- Modify: `backend/src/grimoire/state_store/store.py` — remove `fork_branch()`, `branch_chain()`, `resolve_character_state()` CoW, `branch_id` from all queries and writes; remove main-branch creation from `upsert_campaign()`
- Modify: `backend/src/grimoire/state_store/fork.py` — remove branch rewriting from `bulk_copy()`, remove `branches` from copy tables
- Modify: `backend/src/grimoire/state_store/delta_log.py` — remove `branch_id` from `DeltaRecord`

### Services
- Modify: `backend/src/grimoire/continuity/registry.py` — per-campaign factory instead of per-(campaign, branch)
- Modify: `backend/src/grimoire/scenes/manager.py` — remove `_known_branches()`, `fork_scenes_for_branch()`, branch directory walking, branch prefix in scene IDs
- Modify: `backend/src/grimoire/scenes/indexer.py` — remove branch directory walking
- Modify: `backend/src/grimoire/scenes/storage.py` — remove branch directory logic from `scenes_dir()`
- Modify: `backend/src/grimoire/transient_state/service.py` — remove `_default_branch()` and `branch_id` parameter
- Modify: `backend/src/grimoire/context/builder.py` — remove `DEFAULT_BRANCH_SUFFIX` and `branch_id` usage

### Orchestrator
- Modify: `backend/src/grimoire/orchestrator/fork.py` — remove `fork()` (branch fork), keep `fork_campaign()`
- Modify: `backend/src/grimoire/orchestrator/service.py` — remove `branch_id` from turn flow
- Modify: `backend/src/grimoire/orchestrator/delta_applier.py` — remove `branch_id` parameter
- Modify: `backend/src/grimoire/orchestrator/retcon_replay.py` — remove branch scoping
- Modify: `backend/src/grimoire/orchestrator/alternates.py` — remove branch scoping

### API
- Modify: `backend/src/grimoire/api/campaigns/fork.py` — remove `POST /branches` endpoint, remove `BranchForkPayload`
- Modify: `backend/src/grimoire/api/campaigns/schemas.py` — remove branch-related schemas
- Modify: `backend/src/grimoire/api/campaigns/scenes.py` — remove `branch_id` from scene creation

### Frontend
- Modify: `frontend/src/api/campaign/api.ts` — remove `forkBranch()` function
- Modify: `frontend/src/api/campaign/types.ts` — remove `branch_id` from `ApiScene`

### Tests
- Delete: `backend/tests/state_store/test_branches.py`
- Modify: `backend/tests/orchestrator/test_fork.py` — remove branch fork tests
- Modify: All other test files that pass `branch_id` to fixtures/constructors

---

## Task 1: Database Migration

**Files:**
- Create: `backend/src/grimoire/storage/migrations/036_remove_branches.sql`

This single migration recreates all 21 tables that have `branch_id` columns, copies data from old tables (only main-branch rows), drops old tables, renames new ones, and drops the `branches` table. SQLite doesn't support `ALTER TABLE DROP COLUMN` for columns in primary keys or indexes, so table recreation is the safe approach.

- [ ] **Step 1: Write the migration file**

```sql
-- 036_remove_branches.sql
-- Remove branch_id from all tables; drop branches table.
-- Copies only main-branch rows (branch_id LIKE '%:main').

-- ── scenes ──────────────────────────────────────────────
CREATE TABLE scenes_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  slug TEXT NOT NULL,
  file_path TEXT NOT NULL,
  location_ref TEXT,
  in_game_start TEXT,
  in_game_end TEXT,
  pov_character_ref TEXT,
  present_character_refs TEXT,
  present_pc_refs TEXT,
  summary TEXT,
  running_summary TEXT,
  key_beats TEXT,
  tags TEXT,
  emotional_arc TEXT,
  post_count INTEGER NOT NULL DEFAULT 0,
  threads_introduced TEXT,
  threads_paid_off TEXT,
  title TEXT,
  greeting_id TEXT,
  closed INTEGER NOT NULL DEFAULT 0,
  closed_at_turn TEXT
);
INSERT INTO scenes_new SELECT
  id, campaign_id, ordinal, slug, file_path, location_ref,
  in_game_start, in_game_end, pov_character_ref, present_character_refs,
  present_pc_refs, summary, running_summary, key_beats, tags,
  emotional_arc, post_count, threads_introduced, threads_paid_off,
  title, greeting_id, closed, closed_at_turn
FROM scenes WHERE branch_id LIKE '%:main';
DROP TABLE scenes;
ALTER TABLE scenes_new RENAME TO scenes;
CREATE INDEX idx_scenes_campaign ON scenes(campaign_id, ordinal);

-- ── posts ───────────────────────────────────────────────
CREATE TABLE posts_new (
  id TEXT PRIMARY KEY,
  scene_id TEXT REFERENCES scenes(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL,
  turn_id TEXT,
  order_in_scene INTEGER NOT NULL,
  author_kind TEXT,
  author_pc_ref TEXT,
  body_excerpt TEXT,
  body_hash TEXT,
  is_player INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  retconned_from TEXT REFERENCES posts_new(id),
  body TEXT,
  author_npc_ref TEXT
);
INSERT INTO posts_new SELECT
  id, scene_id, campaign_id, turn_id, order_in_scene,
  author_kind, author_pc_ref, body_excerpt, body_hash, is_player,
  created_at, retconned_from, body, author_npc_ref
FROM posts WHERE branch_id LIKE '%:main';
DROP TABLE posts;
ALTER TABLE posts_new RENAME TO posts;
CREATE INDEX idx_posts_scene_order ON posts(scene_id, order_in_scene);
CREATE INDEX idx_posts_campaign ON posts(campaign_id);
CREATE INDEX idx_posts_turn ON posts(turn_id);

-- ── character_state ─────────────────────────────────────
CREATE TABLE character_state_new (
  character_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  location_ref TEXT,
  emotional_state TEXT,
  physical_state TEXT,
  immediate_intent TEXT,
  knowledge_state TEXT,
  last_action TEXT,
  last_screen_time_turn TEXT,
  visible_to_pc INTEGER NOT NULL DEFAULT 0,
  drift_score REAL NOT NULL DEFAULT 0,
  tier_pin TEXT,
  current_scene_id TEXT,
  updated_at_turn TEXT,
  PRIMARY KEY (character_ref, campaign_id)
);
INSERT INTO character_state_new SELECT
  character_ref, campaign_id, location_ref, emotional_state,
  physical_state, immediate_intent, knowledge_state, last_action,
  last_screen_time_turn, visible_to_pc, drift_score, tier_pin,
  current_scene_id, updated_at_turn
FROM character_state WHERE branch_id LIKE '%:main';
DROP TABLE character_state;
ALTER TABLE character_state_new RENAME TO character_state;
CREATE INDEX idx_charstate_campaign ON character_state(campaign_id);

-- ── location_state ──────────────────────────────────────
CREATE TABLE location_state_new (
  location_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  weather TEXT,
  time_of_day TEXT,
  occupants TEXT,
  condition TEXT,
  transient_features TEXT,
  updated_at_turn TEXT,
  PRIMARY KEY (location_ref, campaign_id)
);
INSERT INTO location_state_new SELECT
  location_ref, campaign_id, weather, time_of_day, occupants,
  condition, transient_features, updated_at_turn
FROM location_state WHERE branch_id LIKE '%:main';
DROP TABLE location_state;
ALTER TABLE location_state_new RENAME TO location_state;
CREATE INDEX idx_locstate_campaign ON location_state(campaign_id);

-- ── faction_state ───────────────────────────────────────
CREATE TABLE faction_state_new (
  faction_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  state TEXT,
  updated_at_turn TEXT,
  PRIMARY KEY (faction_ref, campaign_id)
);
INSERT INTO faction_state_new SELECT
  faction_ref, campaign_id, state, updated_at_turn
FROM faction_state WHERE branch_id LIKE '%:main';
DROP TABLE faction_state;
ALTER TABLE faction_state_new RENAME TO faction_state;
CREATE INDEX idx_factionstate_campaign ON faction_state(campaign_id);

-- ── facts ───────────────────────────────────────────────
CREATE TABLE facts_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  text TEXT NOT NULL,
  established_in_post TEXT REFERENCES posts(id),
  in_game_when TEXT,
  about TEXT,
  source TEXT,
  speaker_ref TEXT,
  confidence REAL,
  keywords TEXT,
  retired INTEGER NOT NULL DEFAULT 0,
  retired_in_post TEXT REFERENCES posts(id),
  contradicts TEXT,
  tags TEXT
);
INSERT INTO facts_new SELECT
  id, campaign_id, text, established_in_post, in_game_when,
  about, source, speaker_ref, confidence, keywords, retired,
  retired_in_post, contradicts, tags
FROM facts WHERE branch_id LIKE '%:main';

-- Rebuild FTS triggers on new table
DROP TRIGGER IF EXISTS facts_ai;
DROP TRIGGER IF EXISTS facts_ad;
DROP TRIGGER IF EXISTS facts_au;
DROP TABLE IF EXISTS facts_fts;
DROP TABLE facts;
ALTER TABLE facts_new RENAME TO facts;
CREATE INDEX idx_facts_campaign ON facts(campaign_id);
CREATE INDEX idx_facts_retired ON facts(retired);

CREATE VIRTUAL TABLE facts_fts USING fts5(
  text, keywords, tags,
  content='facts', content_rowid='rowid'
);
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, text, keywords, tags)
  VALUES (new.rowid, new.text, new.keywords, new.tags);
END;
CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text, keywords, tags)
  VALUES('delete', old.rowid, old.text, old.keywords, old.tags);
END;
CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text, keywords, tags)
  VALUES('delete', old.rowid, old.text, old.keywords, old.tags);
  INSERT INTO facts_fts(rowid, text, keywords, tags)
  VALUES (new.rowid, new.text, new.keywords, new.tags);
END;
-- Backfill FTS
INSERT INTO facts_fts(rowid, text, keywords, tags)
  SELECT rowid, text, keywords, tags FROM facts;

-- ── commitments ─────────────────────────────────────────
CREATE TABLE commitments_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  kind TEXT,
  text TEXT,
  from_character_ref TEXT,
  to_character_ref TEXT,
  due_by TEXT,
  status TEXT,
  weight INTEGER,
  created_in_post TEXT,
  in_game_created_at TEXT,
  resolved_in_post TEXT,
  tags TEXT,
  related_fact_ids TEXT
);
INSERT INTO commitments_new SELECT
  id, campaign_id, kind, text, from_character_ref,
  to_character_ref, due_by, status, weight, created_in_post,
  in_game_created_at, resolved_in_post, tags, related_fact_ids
FROM commitments WHERE branch_id LIKE '%:main';
DROP TABLE commitments;
ALTER TABLE commitments_new RENAME TO commitments;
CREATE INDEX idx_commitments_campaign ON commitments(campaign_id);
CREATE INDEX idx_commitments_status ON commitments(status);

-- ── relationships ───────────────────────────────────────
CREATE TABLE relationships_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  text TEXT,
  from_character_ref TEXT,
  to_character_ref TEXT,
  types TEXT,
  state TEXT,
  updated_at_turn TEXT
);
INSERT INTO relationships_new SELECT
  id, campaign_id, text, from_character_ref, to_character_ref,
  types, state, updated_at_turn
FROM relationships WHERE branch_id LIKE '%:main';
DROP TABLE relationships;
ALTER TABLE relationships_new RENAME TO relationships;
CREATE INDEX idx_relationships_campaign ON relationships(campaign_id);
CREATE INDEX idx_relationships_pair ON relationships(from_character_ref, to_character_ref);

-- ── knowledge_state ─────────────────────────────────────
CREATE TABLE knowledge_state_new (
  fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
  character_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  knows INTEGER NOT NULL DEFAULT 0,
  learned_in_post TEXT,
  source TEXT,
  PRIMARY KEY (fact_id, character_ref)
);
INSERT INTO knowledge_state_new SELECT
  fact_id, character_ref, campaign_id, knows, learned_in_post, source
FROM knowledge_state WHERE branch_id LIKE '%:main';
DROP TABLE knowledge_state;
ALTER TABLE knowledge_state_new RENAME TO knowledge_state;
CREATE INDEX idx_knowledge_character ON knowledge_state(character_ref);

-- ── calendar ────────────────────────────────────────────
CREATE TABLE calendar_new (
  campaign_id TEXT NOT NULL PRIMARY KEY,
  current_in_game_time TEXT
);
INSERT INTO calendar_new SELECT campaign_id, current_in_game_time
FROM calendar WHERE branch_id LIKE '%:main';
DROP TABLE calendar;
ALTER TABLE calendar_new RENAME TO calendar;

-- ── images ──────────────────────────────────────────────
CREATE TABLE images_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  scene_id TEXT REFERENCES scenes(id),
  post_id TEXT REFERENCES posts(id),
  file_path TEXT NOT NULL,
  thumbnail_path TEXT,
  prompt TEXT,
  negative_prompt TEXT,
  params TEXT,
  backend TEXT,
  model TEXT,
  seed INTEGER,
  created_at TEXT,
  user_starred INTEGER NOT NULL DEFAULT 0,
  tags TEXT
);
INSERT INTO images_new SELECT
  id, campaign_id, scene_id, post_id, file_path, thumbnail_path,
  prompt, negative_prompt, params, backend, model, seed,
  created_at, user_starred, tags
FROM images WHERE branch_id LIKE '%:main';
DROP TABLE images;
ALTER TABLE images_new RENAME TO images;
CREATE INDEX idx_images_campaign ON images(campaign_id);
CREATE INDEX idx_images_scene ON images(scene_id);

-- ── deltas ──────────────────────────────────────────────
CREATE TABLE deltas_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT,
  turn_id TEXT,
  source TEXT,
  kind TEXT,
  target_scope TEXT,
  target_table TEXT,
  target_path TEXT,
  target_id TEXT,
  before TEXT,
  after TEXT,
  confidence REAL,
  applied_at TEXT,
  reversed_at TEXT,
  notes TEXT,
  delta_set_id TEXT
);
INSERT INTO deltas_new SELECT
  id, campaign_id, turn_id, source, kind, target_scope,
  target_table, target_path, target_id, before, after, confidence,
  applied_at, reversed_at, notes, delta_set_id
FROM deltas WHERE branch_id LIKE '%:main' OR branch_id IS NULL;
DROP TABLE deltas;
ALTER TABLE deltas_new RENAME TO deltas;
CREATE INDEX idx_deltas_campaign ON deltas(campaign_id);
CREATE INDEX idx_deltas_turn ON deltas(turn_id);
CREATE INDEX idx_deltas_applied_at ON deltas(applied_at);
CREATE INDEX idx_deltas_target ON deltas(target_scope, target_id);
CREATE INDEX idx_deltas_set ON deltas(campaign_id, delta_set_id);

-- ── turn_audits ─────────────────────────────────────────
CREATE TABLE turn_audits_new (
  turn_id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  scene_id TEXT,
  pc_ref TEXT,
  composition TEXT,
  context_summary TEXT,
  prompt_messages TEXT,
  prompt_budget TEXT,
  mechanics_results TEXT,
  llm_metadata TEXT,
  response_text TEXT,
  extraction_summary TEXT,
  applied_delta_ids TEXT,
  queued_review_ids TEXT,
  side_effects TEXT,
  errors TEXT,
  created_at TEXT NOT NULL
);
INSERT INTO turn_audits_new SELECT
  turn_id, campaign_id, scene_id, pc_ref, composition,
  context_summary, prompt_messages, prompt_budget, mechanics_results,
  llm_metadata, response_text, extraction_summary, applied_delta_ids,
  queued_review_ids, side_effects, errors, created_at
FROM turn_audits WHERE branch_id LIKE '%:main';
DROP TABLE turn_audits;
ALTER TABLE turn_audits_new RENAME TO turn_audits;
CREATE INDEX idx_turnaudit_campaign ON turn_audits(campaign_id, created_at);

-- ── contradiction_reports ───────────────────────────────
CREATE TABLE contradiction_reports_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  candidate_fact TEXT NOT NULL,
  conflicts TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  resolution TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
INSERT INTO contradiction_reports_new SELECT
  id, campaign_id, candidate_fact, conflicts, resolved,
  resolution, created_at, resolved_at
FROM contradiction_reports WHERE branch_id LIKE '%:main';
DROP TABLE contradiction_reports;
ALTER TABLE contradiction_reports_new RENAME TO contradiction_reports;
CREATE INDEX idx_contradictions_campaign ON contradiction_reports(campaign_id);
CREATE INDEX idx_contradictions_resolved ON contradiction_reports(resolved);

-- ── scheduled_events ────────────────────────────────────
CREATE TABLE scheduled_events_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  payload TEXT,
  triggered INTEGER NOT NULL DEFAULT 0,
  triggered_at TEXT,
  created_at TEXT NOT NULL
);
INSERT INTO scheduled_events_new SELECT
  id, campaign_id, at, kind, label, payload, triggered,
  triggered_at, created_at
FROM scheduled_events WHERE branch_id LIKE '%:main';
DROP TABLE scheduled_events;
ALTER TABLE scheduled_events_new RENAME TO scheduled_events;
CREATE INDEX idx_scheduled_events_campaign ON scheduled_events(campaign_id);
CREATE INDEX idx_scheduled_events_at ON scheduled_events(at);
CREATE INDEX idx_scheduled_events_pending ON scheduled_events(triggered, at);

-- ── current_alternate_delta_sets ────────────────────────
CREATE TABLE current_alternate_delta_sets_new (
    campaign_id     TEXT NOT NULL,
    post_id         TEXT NOT NULL,
    delta_set_id    TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (campaign_id, post_id)
);
INSERT INTO current_alternate_delta_sets_new SELECT
  campaign_id, post_id, delta_set_id, updated_at
FROM current_alternate_delta_sets WHERE branch_id LIKE '%:main';
DROP TABLE current_alternate_delta_sets;
ALTER TABLE current_alternate_delta_sets_new RENAME TO current_alternate_delta_sets;
CREATE INDEX idx_current_alt_sets_campaign
    ON current_alternate_delta_sets(campaign_id);
CREATE INDEX idx_current_alt_sets_set
    ON current_alternate_delta_sets(campaign_id, delta_set_id);

-- ── context_pins ────────────────────────────────────────
CREATE TABLE context_pins_new (
    id                  TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    kind                TEXT NOT NULL,
    target_kind         TEXT NOT NULL,
    target_source_id    TEXT,
    target_entity_kind  TEXT,
    target_entity_id    TEXT,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at_turn_id  TEXT,
    ttl_turns           INTEGER,
    cleared_at          TEXT,
    cleared_by          TEXT
);
INSERT INTO context_pins_new SELECT
  id, campaign_id, kind, target_kind, target_source_id,
  target_entity_kind, target_entity_id, created_at, created_by,
  created_at_turn_id, ttl_turns, cleared_at, cleared_by
FROM context_pins WHERE branch_id LIKE '%:main';
DROP TABLE context_pins;
ALTER TABLE context_pins_new RENAME TO context_pins;
CREATE INDEX ix_ctx_pins_active
    ON context_pins(campaign_id)
    WHERE cleared_at IS NULL;

-- ── transient_character_state ───────────────────────────
CREATE TABLE transient_character_state_new (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_character_state_new(id),
    in_game_at     TEXT
);
INSERT INTO transient_character_state_new SELECT
  id, campaign_id, entity_id, field, value, provenance,
  source_post_id, confidence, created_at, expires_at,
  superseded_by, in_game_at
FROM transient_character_state WHERE branch_id LIKE '%:main';
DROP TABLE transient_character_state;
ALTER TABLE transient_character_state_new RENAME TO transient_character_state;
CREATE INDEX ix_tcs_current
    ON transient_character_state(campaign_id, entity_id, field)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tcs_supersedes
    ON transient_character_state(superseded_by)
    WHERE superseded_by IS NOT NULL;

-- ── transient_location_state ────────────────────────────
CREATE TABLE transient_location_state_new (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_location_state_new(id),
    in_game_at     TEXT
);
INSERT INTO transient_location_state_new SELECT
  id, campaign_id, entity_id, field, value, provenance,
  source_post_id, confidence, created_at, expires_at,
  superseded_by, in_game_at
FROM transient_location_state WHERE branch_id LIKE '%:main';
DROP TABLE transient_location_state;
ALTER TABLE transient_location_state_new RENAME TO transient_location_state;
CREATE INDEX ix_tls_current
    ON transient_location_state(campaign_id, entity_id, field)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tls_supersedes
    ON transient_location_state(superseded_by)
    WHERE superseded_by IS NOT NULL;

-- ── transient_faction_state ─────────────────────────────
CREATE TABLE transient_faction_state_new (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_faction_state_new(id),
    in_game_at     TEXT
);
INSERT INTO transient_faction_state_new SELECT
  id, campaign_id, entity_id, field, value, provenance,
  source_post_id, confidence, created_at, expires_at,
  superseded_by, in_game_at
FROM transient_faction_state WHERE branch_id LIKE '%:main';
DROP TABLE transient_faction_state;
ALTER TABLE transient_faction_state_new RENAME TO transient_faction_state;
CREATE INDEX ix_tfs_current
    ON transient_faction_state(campaign_id, entity_id, field)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tfs_supersedes
    ON transient_faction_state(superseded_by)
    WHERE superseded_by IS NOT NULL;

-- ── transient_scene_state ───────────────────────────────
CREATE TABLE transient_scene_state_new (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_scene_state_new(id),
    in_game_at     TEXT
);
INSERT INTO transient_scene_state_new SELECT
  id, campaign_id, entity_id, field, value, provenance,
  source_post_id, confidence, created_at, expires_at,
  superseded_by, in_game_at
FROM transient_scene_state WHERE branch_id LIKE '%:main';
DROP TABLE transient_scene_state;
ALTER TABLE transient_scene_state_new RENAME TO transient_scene_state;
CREATE INDEX ix_tss_current
    ON transient_scene_state(campaign_id, entity_id, field)
    WHERE superseded_by IS NULL;
CREATE INDEX ix_tss_supersedes
    ON transient_scene_state(superseded_by)
    WHERE superseded_by IS NOT NULL;

-- ── Drop branches table ────────────────────────────────
DROP TABLE IF EXISTS branches;
```

- [ ] **Step 2: Verify the migration file is syntactically correct**

Run: `cd backend && uv run python -c "from pathlib import Path; sql = Path('src/grimoire/storage/migrations/036_remove_branches.sql').read_text(); print(f'Migration loaded: {len(sql)} chars, {sql.count(chr(59))} statements')"`

- [ ] **Step 3: Commit**

```
git add backend/src/grimoire/storage/migrations/036_remove_branches.sql
git commit -m "feat: add migration 034 to remove branch_id from all tables"
```

---

## Task 2: Remove BranchId from Types and Models

**Files:**
- Modify: `backend/src/grimoire/types/common.py:85` — remove `BranchId = str`
- Modify: `backend/src/grimoire/types/scene.py:12,60,101` — remove `BranchId` import and `branch_id` fields

- [ ] **Step 1: Remove BranchId type alias from common.py**

In `backend/src/grimoire/types/common.py`, delete the line:
```python
BranchId = str
```

- [ ] **Step 2: Remove branch_id from Scene and SceneInit**

In `backend/src/grimoire/types/scene.py`:
1. Remove `BranchId` from the imports
2. Remove `branch_id: BranchId` from the `Scene` class (line 60)
3. Remove `branch_id: BranchId` from the `SceneInit` class (line 101)

- [ ] **Step 3: Find and fix all imports of BranchId**

Run: `cd backend && grep -rn "BranchId" src/grimoire/ --include="*.py" -l`

For every file that imports `BranchId`: remove the import and remove the `branch_id` parameter from type annotations. Replace `branch_id: BranchId` parameters with nothing (remove them entirely) in function signatures.

- [ ] **Step 4: Verify syntax**

Run: `cd backend && uv run python -c "import grimoire.types.common; import grimoire.types.scene; print('OK')"`

- [ ] **Step 5: Commit**

```
git add -u
git commit -m "refactor: remove BranchId type alias and branch_id from Scene/SceneInit"
```

---

## Task 3: Simplify State Store

**Files:**
- Modify: `backend/src/grimoire/state_store/store.py`
- Modify: `backend/src/grimoire/state_store/fork.py`
- Modify: `backend/src/grimoire/state_store/delta_log.py`

- [ ] **Step 1: Remove branch from DeltaRecord**

In `backend/src/grimoire/state_store/delta_log.py`, remove the `branch_id: str | None` field from the `DeltaRecord` dataclass. Remove all SQL references to `branch_id` in the delta log queries (INSERT, SELECT, WHERE clauses).

- [ ] **Step 2: Remove branch methods from store.py**

In `backend/src/grimoire/state_store/store.py`:

1. **Delete `fork_branch()`** — the entire method that creates a branch row
2. **Delete `branch_chain()`** — the CoW ancestor-chain walker
3. **Delete `resolve_character_state()`** — the CoW read that walks the chain
4. **Delete `list_tier_pins()` CoW logic** — simplify to a single query without chain walking
5. **Remove main-branch creation from `upsert_campaign()`** — delete the `INSERT OR IGNORE INTO branches` block (around line 1101-1109)
6. **Remove `branch_id` parameter** from every method signature: `list_scenes()`, `resolve_entity()`, `apply_delta()`, `apply_delta_set()`, `rewind_delta_set()`, `re_activate_delta_set()`, `swap_delta_set()`, `set_current_alternate_delta_set()`, `clear_current_alternate_delta_set()`, `current_delta_set_for()`
7. **Remove `branch_id` from all SQL queries** — drop from INSERT column lists, SELECT lists, WHERE clauses, and parameter tuples. For delta operations using `current_alternate_delta_sets`, the PK is now `(campaign_id, post_id)` instead of `(campaign_id, branch_id, post_id)`.

- [ ] **Step 3: Simplify fork.py bulk_copy**

In `backend/src/grimoire/state_store/fork.py`:

1. Remove `"branches"` from the list of tables to copy
2. Remove all `"branch"` rewrite kind entries from table column specs — no more `old_campaign:label → new_campaign:label` rewriting
3. Remove `branch_id` columns from all table column lists in the copy specs
4. Keep the `"campaign"` rewrite logic (rewriting `campaign_id` from source to fork)

- [ ] **Step 4: Remove branch_id from character/location/faction state queries**

In `backend/src/grimoire/state_store/store.py`, update all `upsert_character_state()`, `upsert_location_state()`, `upsert_faction_state()` and their corresponding query methods. These tables now use `(ref, campaign_id)` as PK instead of `(ref, branch_id)`.

- [ ] **Step 5: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/state_store/ --fix`

- [ ] **Step 6: Commit**

```
git add -u
git commit -m "refactor: remove branch_id from state store, delta log, and fork"
```

---

## Task 4: Simplify ContinuityRegistry

**Files:**
- Modify: `backend/src/grimoire/continuity/registry.py`

- [ ] **Step 1: Change from per-(campaign, branch) to per-campaign factory**

In `backend/src/grimoire/continuity/registry.py`:
1. Remove `branch_id` parameter from `for_campaign()` — the cache key becomes just `campaign_id`
2. Remove `_default_branch()` helper
3. Remove `branch_id` from `resolve_continuity()`
4. Pass only `campaign_id` to `SqliteContinuityStore` and `HybridFactSearchIndex` constructors — remove any `branch_id` parameter in those constructors

- [ ] **Step 2: Update continuity store and search index**

Find the `SqliteContinuityStore` and `HybridFactSearchIndex` classes (grep for them). Remove `branch_id` from their constructors and all SQL queries. WHERE clauses change from `WHERE campaign_id = ? AND branch_id = ?` to `WHERE campaign_id = ?`.

- [ ] **Step 3: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/continuity/ --fix`

- [ ] **Step 4: Commit**

```
git add -u
git commit -m "refactor: simplify ContinuityRegistry to per-campaign factory"
```

---

## Task 5: Simplify SceneManager, SceneIndexer, Scene Storage

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py`
- Modify: `backend/src/grimoire/scenes/indexer.py`
- Modify: `backend/src/grimoire/scenes/storage.py`

- [ ] **Step 1: Simplify scenes_dir() in storage.py**

In `backend/src/grimoire/scenes/storage.py`:
- Remove the branch directory logic. `scenes_dir()` should always return `data/campaigns/{campaign_id}/scenes/` regardless. Remove the `branch_id` parameter entirely.
- Remove the Windows-safe encoding of `:` → `__` for branch subdirectories

- [ ] **Step 2: Simplify SceneManager**

In `backend/src/grimoire/scenes/manager.py`:
1. **Delete `_known_branches()`** — no more branch directory walking
2. **Delete `fork_scenes_for_branch()`** — no more scene directory copying for branches
3. **Remove `branch_id` parameter** from `list_scenes()`, `active_scene_for_campaign()`, `get_scene()`
4. **Simplify `_scene_id()`** — remove the branch prefix logic. Scene IDs become just `{campaign_id}:{ordinal}-{slug}` (no `branch_id:` prefix)
5. **Update `_active_scene` cache key** from `(campaign_id, branch_id)` to just `campaign_id`
6. **Remove `branch_id="main"` defaults** from all method signatures

- [ ] **Step 3: Simplify SceneIndexer**

In `backend/src/grimoire/scenes/indexer.py`:
- Remove `branch_id` from all scene/post metadata tracking
- Remove branch directory walking in backfill
- Update all upsert operations to not include `branch_id`

- [ ] **Step 4: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/scenes/ --fix`

- [ ] **Step 5: Commit**

```
git add -u
git commit -m "refactor: remove branch logic from scene manager, indexer, and storage"
```

---

## Task 6: Simplify ForkCoordinator

**Files:**
- Modify: `backend/src/grimoire/orchestrator/fork.py`

- [ ] **Step 1: Remove branch fork method**

In `backend/src/grimoire/orchestrator/fork.py`:
1. **Delete `fork()`** — the entire method that creates within-campaign branches
2. Keep `fork_campaign()`, `_execute_fork()`, `_enqueue_fork()`, `list_pending_forks()`, `process_pending_forks()`
3. In `_execute_fork()`, remove the branch-related logic:
   - Don't create a "main" branch in the forked campaign (since branches table is gone)
   - Don't call `fork_scenes_for_branch()` (scenes are already copied by bulk_copy file operations)
4. Keep `get_lineage()` and `get_lineage_ancestors()` — these query `forked_from_campaign_id` which is a column on `campaigns` table (migration 028), not the `branches` table

- [ ] **Step 2: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/orchestrator/fork.py --fix`

- [ ] **Step 3: Commit**

```
git add -u
git commit -m "refactor: remove branch fork from ForkCoordinator, keep campaign fork"
```

---

## Task 7: Simplify DeltaApplier, RetconReplay, AlternatesManager

**Files:**
- Modify: `backend/src/grimoire/orchestrator/delta_applier.py`
- Modify: `backend/src/grimoire/orchestrator/retcon_replay.py`
- Modify: `backend/src/grimoire/orchestrator/alternates.py`

- [ ] **Step 1: Remove branch_id from DeltaApplier**

In `backend/src/grimoire/orchestrator/delta_applier.py`:
1. Remove `branch_id` from `StateSnapshot` construction — delete the `branch_id=scene.branch_id` kwarg
2. Remove `branch_id` parameter from `apply_routing()` signature
3. In `_apply_continuity_delta()`, remove `branch_id` parameter — call `resolve_continuity(self._continuity, campaign_id)` without `branch_id`
4. Remove `branch_id` from all `self._store.apply_delta()` calls

- [ ] **Step 2: Remove branch_id from RetconReplaySession**

In `backend/src/grimoire/orchestrator/retcon_replay.py`:
1. In `_collect_subsequent_post_ids()`, remove `branch_id = edited_scene.branch_id or "main"` — call `self._orch._scenes.list_scenes(campaign_id)` without branch_id
2. In `_discard_in_flight_alternate()`, remove `branch_id = scene.branch_id or "main"` — call `rewind_delta_set()` and `re_activate_delta_set()` without `branch_id`

- [ ] **Step 3: Remove branch_id from AlternatesManager**

In `backend/src/grimoire/orchestrator/alternates.py`:
1. Remove all `branch_id = scene.branch_id or "main"` lines
2. Remove `branch_id` from all `swap_delta_set()`, `apply_delta_set()`, `rewind_delta_set()`, `re_activate_delta_set()`, `set_current_alternate_delta_set()` calls

- [ ] **Step 4: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/orchestrator/ --fix`

- [ ] **Step 5: Commit**

```
git add -u
git commit -m "refactor: remove branch_id from delta applier, retcon replay, alternates"
```

---

## Task 8: Simplify Orchestrator Service and Context Builder

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Modify: `backend/src/grimoire/context/builder.py`
- Modify: `backend/src/grimoire/transient_state/service.py`

- [ ] **Step 1: Remove branch_id from orchestrator turn flow**

In `backend/src/grimoire/orchestrator/service.py`:
1. Remove `branch_id=initial_scene.branch_id` from `_emit_turn_event()` calls for TURN_STARTED and TURN_COMPLETE
2. Remove `branch_id_for_cache = getattr(scene_obj_for_cache, "branch_id", None)` and any usage
3. Remove `branch_id=scene_obj.branch_id` from `apply_routing()` and `route_transient_updates()` calls
4. Update any event payloads that include `branch_id`

- [ ] **Step 2: Remove branch_id from context builder**

In `backend/src/grimoire/context/builder.py`:
1. Delete `DEFAULT_BRANCH_SUFFIX = "main"` constant
2. Remove `branch_id` parameter from all methods
3. Remove `branch_id or "main"` default patterns

- [ ] **Step 3: Remove branch_id from transient state service**

In `backend/src/grimoire/transient_state/service.py`:
1. Delete `_default_branch()` helper
2. Remove `branch_id` parameter from all methods
3. Update SQL queries to not include `branch_id` in WHERE/INSERT/UPDATE

- [ ] **Step 4: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/orchestrator/ src/grimoire/context/ src/grimoire/transient_state/ --fix`

- [ ] **Step 5: Commit**

```
git add -u
git commit -m "refactor: remove branch_id from orchestrator, context builder, transient state"
```

---

## Task 9: Simplify Protocol Interfaces

**Files:**
- Modify: `backend/src/grimoire/types/protocols.py`

- [ ] **Step 1: Remove branch_id from all protocol method signatures**

In `backend/src/grimoire/types/protocols.py`:
1. Remove `branch_id: BranchId` from `SceneManager` protocol methods: `list_scenes()`, `active_scene_for_campaign()`, `fork_scenes_for_branch()`
2. Remove the `fork_scenes_for_branch()` method entirely from the protocol
3. Remove `branch_id` from `StateStore`/composition protocol methods: `list_scenes()`, `resolve_character()`, `resolve_location()`, `resolve_entity()`, `advance_time()`, `upsert_character_state()`
4. Remove `BranchId` import

- [ ] **Step 2: Find and fix all other protocol/interface references**

Run: `cd backend && grep -rn "branch_id" src/grimoire/types/ --include="*.py"`

Remove any remaining `branch_id` references in the types package.

- [ ] **Step 3: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/types/ --fix`

- [ ] **Step 4: Commit**

```
git add -u
git commit -m "refactor: remove branch_id from protocol interfaces"
```

---

## Task 10: Update API Routes

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/fork.py`
- Modify: `backend/src/grimoire/api/campaigns/schemas.py`
- Modify: `backend/src/grimoire/api/campaigns/scenes.py`
- Modify: any other API files referencing branch_id

- [ ] **Step 1: Remove branch fork endpoint**

In `backend/src/grimoire/api/campaigns/fork.py`:
1. Delete the `POST /{campaign_id}/branches` endpoint handler
2. Delete `BranchForkPayload` schema (or remove from schemas.py if defined there)
3. Keep campaign fork, lineage, and pending fork endpoints

- [ ] **Step 2: Remove branch_id from scene API**

In `backend/src/grimoire/api/campaigns/scenes.py`:
1. Remove `branch_id="main"` defaults from scene creation/listing
2. Remove `branch_id` from query parameters

- [ ] **Step 3: Clean up schemas**

In `backend/src/grimoire/api/campaigns/schemas.py`:
1. Remove any branch-related request/response schemas
2. Remove `branch_id` from scene response schemas if present

- [ ] **Step 4: Search for remaining branch_id in API layer**

Run: `cd backend && grep -rn "branch_id" src/grimoire/api/ --include="*.py"`

Fix any remaining references.

- [ ] **Step 5: Run linting**

Run: `cd backend && uv run ruff check src/grimoire/api/ --fix`

- [ ] **Step 6: Commit**

```
git add -u
git commit -m "refactor: remove branch endpoints and branch_id from API layer"
```

---

## Task 11: Update Frontend

**Files:**
- Modify: `frontend/src/api/campaign/api.ts`
- Modify: `frontend/src/api/campaign/types.ts`
- Modify: any other frontend files referencing branch_id

- [ ] **Step 1: Remove forkBranch from API client**

In `frontend/src/api/campaign/api.ts`:
- Delete the `forkBranch()` function

- [ ] **Step 2: Remove branch_id from types**

In `frontend/src/api/campaign/types.ts`:
- Remove `branch_id: string` from `ApiScene`
- Remove any branch-related type definitions

- [ ] **Step 3: Search for remaining branch references**

Run: `cd frontend && grep -rn "branch" src/ --include="*.ts" --include="*.tsx" -l`

Fix any remaining references (InspectorPanel, useLivePreview, etc.).

- [ ] **Step 4: Run type check and lint**

Run: `cd frontend && pnpm typecheck && pnpm lint`

- [ ] **Step 5: Commit**

```
git add -u
git commit -m "refactor: remove branch references from frontend"
```

---

## Task 12: Update Tests

**Files:**
- Delete: `backend/tests/state_store/test_branches.py`
- Modify: `backend/tests/orchestrator/test_fork.py`
- Modify: `backend/tests/orchestrator/test_fork_campaign.py`
- Modify: all other test files referencing branch_id

- [ ] **Step 1: Delete branch-specific test file**

Delete `backend/tests/state_store/test_branches.py`

- [ ] **Step 2: Update fork tests**

In `backend/tests/orchestrator/test_fork.py`:
- Remove any tests that test within-campaign branch forking
- Keep tests for campaign forking
- Remove `branch_id` from test fixtures and assertions

In `backend/tests/orchestrator/test_fork_campaign.py`:
- Keep all tests, remove `branch_id` from assertions and fixture setup

- [ ] **Step 3: Fix all other test files**

Run: `cd backend && grep -rn "branch_id" tests/ --include="*.py" -l`

For each file:
- Remove `branch_id` from fixture constructors (e.g., Scene creation)
- Remove `branch_id` from API call payloads
- Remove `branch_id` from assertion checks
- Update any mock setups that include branch_id

Common patterns to fix:
- `Scene(branch_id="test:main", ...)` → `Scene(...)`
- `store.list_scenes(campaign_id, branch_id)` → `store.list_scenes(campaign_id)`
- `branch_id="test:main"` in SQL fixture inserts → remove column and value

- [ ] **Step 4: Run full test suite**

Run: `cd backend && uv run pytest -x --timeout=30`

Fix any failures.

- [ ] **Step 5: Commit**

```
git add -u
git commit -m "test: update all tests to remove branch_id references"
```

---

## Task 13: Full Sweep and Verification

- [ ] **Step 1: Search for any remaining branch_id references in backend**

Run: `cd backend && grep -rn "branch_id" src/ --include="*.py" | grep -v "__pycache__"`

Fix any remaining references. Acceptable remaining references: comments explaining the migration, or the migration file itself.

- [ ] **Step 2: Search for remaining branch references in frontend**

Run: `cd frontend && grep -rn "branch" src/ --include="*.ts" --include="*.tsx" | grep -v "node_modules"`

- [ ] **Step 3: Run full backend test suite**

Run: `cd backend && uv run pytest --timeout=30`

All tests must pass.

- [ ] **Step 4: Run frontend checks**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm test`

- [ ] **Step 5: Run ruff format**

Run: `cd backend && uv run ruff format`

- [ ] **Step 6: Final commit if needed**

```
git add -u
git commit -m "refactor: final cleanup of branch_id references"
```

---

## Completion

After all tasks pass, the codebase should have:
- Zero references to `branch_id` in application code (backend + frontend)
- No `branches` table in the database
- Campaign forking preserved and working via `POST /campaigns/{id}/forks`
- Campaign lineage tracking preserved via `forked_from_campaign_id` columns
- All tests passing
