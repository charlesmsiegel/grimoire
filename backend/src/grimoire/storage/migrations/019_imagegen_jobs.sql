-- Persist queued / running imagegen jobs across restarts (§8 of imagegen
-- remaining-design). Completed / failed / cancelled jobs are still
-- surfaced from the in-memory _jobs dict (they get purged on restart,
-- since their results are already in the images table).

CREATE TABLE imagegen_jobs (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  backend TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 5,
  request_json TEXT NOT NULL,
  scene_id TEXT,
  post_id TEXT,
  queued_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);

CREATE INDEX idx_imagegen_jobs_campaign ON imagegen_jobs(campaign_id);
CREATE INDEX idx_imagegen_jobs_status ON imagegen_jobs(status);
