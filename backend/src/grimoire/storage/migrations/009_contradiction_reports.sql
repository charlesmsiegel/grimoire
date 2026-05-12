-- Contradiction reports surfaced by Continuity for user resolution.

CREATE TABLE contradiction_reports (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  candidate_fact TEXT NOT NULL,
  conflicts TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  resolution TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX idx_contradictions_campaign ON contradiction_reports(campaign_id, branch_id);
CREATE INDEX idx_contradictions_resolved ON contradiction_reports(resolved);
