-- Delta log, review queue, and embeddings (vectors via sqlite-vec BLOB column).

CREATE TABLE deltas (
  id TEXT PRIMARY KEY,
  campaign_id TEXT,
  branch_id TEXT,
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
  notes TEXT
);

CREATE INDEX idx_deltas_campaign ON deltas(campaign_id, branch_id);
CREATE INDEX idx_deltas_turn ON deltas(turn_id);
CREATE INDEX idx_deltas_applied_at ON deltas(applied_at);
CREATE INDEX idx_deltas_target ON deltas(target_scope, target_id);

CREATE TABLE review_queue (
  id TEXT PRIMARY KEY,
  delta_id TEXT NOT NULL REFERENCES deltas(id) ON DELETE CASCADE,
  campaign_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewed_at TEXT,
  reviewer_notes TEXT
);

CREATE INDEX idx_review_queue_campaign ON review_queue(campaign_id, status);

CREATE TABLE embeddings (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  ref TEXT NOT NULL,
  source_kind TEXT,
  text TEXT,
  vector BLOB,
  embedded_at TEXT,
  model TEXT,
  campaign_id TEXT
);

CREATE INDEX idx_emb_scope ON embeddings(scope);
CREATE INDEX idx_emb_campaign ON embeddings(campaign_id);
CREATE INDEX idx_emb_ref ON embeddings(ref);
