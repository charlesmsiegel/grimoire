-- LLM request log, embedding cache, and observability tables (spec 16).

CREATE TABLE llm_requests (
  id TEXT PRIMARY KEY,
  campaign_id TEXT,
  turn_id TEXT,
  task TEXT,
  provider TEXT,
  model TEXT,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  cost_usd REAL,
  latency_ms INTEGER,
  retries INTEGER NOT NULL DEFAULT 0,
  fallback_used INTEGER NOT NULL DEFAULT 0,
  request_hash TEXT,
  response_excerpt TEXT,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_llmreq_campaign ON llm_requests(campaign_id, created_at);
CREATE INDEX idx_llmreq_task ON llm_requests(task);

CREATE TABLE embedding_cache (
  text_hash TEXT NOT NULL,
  model_id TEXT NOT NULL,
  vector BLOB NOT NULL,
  cached_at TEXT NOT NULL,
  PRIMARY KEY (text_hash, model_id)
);

CREATE TABLE turn_audits (
  turn_id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
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

CREATE INDEX idx_turnaudit_campaign ON turn_audits(campaign_id, created_at);

CREATE TABLE cost_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id TEXT,
  turn_id TEXT,
  task TEXT,
  model TEXT,
  cost_usd REAL NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE INDEX idx_costs_campaign_date ON cost_records(campaign_id, recorded_at);

CREATE TABLE metric_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  labels TEXT,
  recorded_at TEXT NOT NULL
);

CREATE INDEX idx_metrics_module ON metric_samples(module, metric, recorded_at);

CREATE TABLE log_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module TEXT NOT NULL,
  operation TEXT,
  turn_id TEXT,
  level TEXT NOT NULL DEFAULT 'info',
  message TEXT,
  payload TEXT,
  recorded_at TEXT NOT NULL
);

CREATE INDEX idx_logevents_module ON log_events(module, recorded_at);
CREATE INDEX idx_logevents_turn ON log_events(turn_id);

CREATE TABLE error_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module TEXT NOT NULL,
  turn_id TEXT,
  kind TEXT,
  message TEXT,
  attribution TEXT,
  payload TEXT,
  recorded_at TEXT NOT NULL
);

CREATE INDEX idx_errors_module ON error_records(module, recorded_at);
