-- Persist export history (§3 of export remaining-design / spec 13 §Responsibilities).
CREATE TABLE export_records (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  selection_json TEXT NOT NULL,
  options_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  world_versions_json TEXT,  -- per spec 13: "against what library versions"
  created_at TEXT NOT NULL
);
CREATE INDEX idx_export_records_campaign ON export_records(campaign_id, created_at);
