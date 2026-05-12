-- Facts, commitments, relationships, knowledge state, calendar.

CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
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

CREATE INDEX idx_facts_campaign ON facts(campaign_id, branch_id);
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

CREATE TABLE commitments (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
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

CREATE INDEX idx_commitments_campaign ON commitments(campaign_id, branch_id);
CREATE INDEX idx_commitments_status ON commitments(status);

CREATE TABLE relationships (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  from_character_ref TEXT,
  to_character_ref TEXT,
  types TEXT,
  state TEXT,
  updated_at_turn TEXT
);

CREATE INDEX idx_relationships_campaign ON relationships(campaign_id, branch_id);
CREATE INDEX idx_relationships_pair ON relationships(from_character_ref, to_character_ref);

CREATE TABLE knowledge_state (
  fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
  character_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  knows INTEGER NOT NULL DEFAULT 0,
  learned_in_post TEXT,
  source TEXT,
  PRIMARY KEY (fact_id, character_ref, branch_id)
);

CREATE INDEX idx_knowledge_character ON knowledge_state(character_ref, branch_id);

CREATE TABLE calendar (
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  current_in_game_time TEXT,
  PRIMARY KEY (branch_id)
);
