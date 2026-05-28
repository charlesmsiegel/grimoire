-- Remove branch_id from all tables; drop branches table.
-- Copies only main-branch rows (branch_id LIKE '%:main').
-- SQLite requires table recreation when dropping PK/indexed columns.

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
-- Scenes and posts use a bare 'main' literal (SceneInit default), not the
-- `{campaign_id}:main` form used by other tables. Accept both so neither
-- index is silently dropped on migration.
INSERT INTO scenes_new SELECT
  id, campaign_id, ordinal, slug, file_path, location_ref,
  in_game_start, in_game_end, pov_character_ref, present_character_refs,
  present_pc_refs, summary, running_summary, key_beats, tags,
  emotional_arc, post_count, threads_introduced, threads_paid_off,
  title, greeting_id, closed, closed_at_turn
FROM scenes WHERE branch_id = 'main' OR branch_id LIKE '%:main';
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
  retconned_from TEXT,
  body TEXT NOT NULL DEFAULT '',
  author_npc_ref TEXT
);
INSERT INTO posts_new SELECT
  id, scene_id, campaign_id, turn_id, order_in_scene,
  author_kind, author_pc_ref, body_excerpt, body_hash, is_player,
  created_at, retconned_from, body, author_npc_ref
FROM posts WHERE branch_id = 'main' OR branch_id LIKE '%:main';
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
  appearances_since_last_drift_check INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (character_ref, campaign_id)
);
INSERT INTO character_state_new SELECT
  character_ref, campaign_id, location_ref, emotional_state,
  physical_state, immediate_intent, knowledge_state, last_action,
  last_screen_time_turn, visible_to_pc, drift_score, tier_pin,
  current_scene_id, updated_at_turn, appearances_since_last_drift_check
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
DROP TRIGGER IF EXISTS facts_ai;
DROP TRIGGER IF EXISTS facts_ad;
DROP TRIGGER IF EXISTS facts_au;
DROP TABLE IF EXISTS facts_fts;

CREATE TABLE facts_new (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  text TEXT NOT NULL,
  established_in_post TEXT,
  in_game_when TEXT,
  about TEXT,
  source TEXT,
  speaker_ref TEXT,
  confidence REAL,
  keywords TEXT,
  retired INTEGER NOT NULL DEFAULT 0,
  retired_in_post TEXT,
  contradicts TEXT,
  tags TEXT
);
INSERT INTO facts_new SELECT
  id, campaign_id, text, established_in_post, in_game_when,
  about, source, speaker_ref, confidence, keywords, retired,
  retired_in_post, contradicts, tags
FROM facts WHERE branch_id LIKE '%:main';
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
  from_character_ref TEXT,
  to_character_ref TEXT,
  types TEXT,
  state TEXT,
  updated_at_turn TEXT,
  history TEXT NOT NULL DEFAULT '[]'
);
INSERT INTO relationships_new SELECT
  id, campaign_id, from_character_ref, to_character_ref,
  types, state, updated_at_turn, history
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
  post_id TEXT,
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
  created_at TEXT NOT NULL,
  pre_notice_emitted_at TEXT
);
INSERT INTO scheduled_events_new SELECT
  id, campaign_id, at, kind, label, payload, triggered,
  triggered_at, created_at, pre_notice_emitted_at
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

-- ── library_snapshots ───────────────────────────────────
CREATE TABLE library_snapshots_new (
  campaign_id TEXT NOT NULL,
  library_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  frontmatter TEXT NOT NULL,
  body TEXT,
  snapshot_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, library_id)
);
INSERT INTO library_snapshots_new SELECT
  campaign_id, library_id, version, frontmatter, body, snapshot_at
FROM library_snapshots WHERE branch_id LIKE '%:main';
DROP TABLE library_snapshots;
ALTER TABLE library_snapshots_new RENAME TO library_snapshots;
CREATE INDEX idx_libsnap_lib ON library_snapshots(library_id);

-- ── Drop branches table ─────────────────────────────────
DROP TABLE IF EXISTS branches;
