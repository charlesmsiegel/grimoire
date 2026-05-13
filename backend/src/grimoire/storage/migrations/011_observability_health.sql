-- Observability health-status table (spec 16).
--
-- One row per probe target carries the latest sample. Historic samples
-- are kept on metric_samples (module='health', metric='<target_kind>')
-- so subscribers can build trend lines without rescanning probes.

CREATE TABLE health_status (
  target_id TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  level TEXT NOT NULL,
  message TEXT,
  details TEXT,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (target_id)
);

CREATE INDEX idx_health_kind ON health_status(target_kind, checked_at);
