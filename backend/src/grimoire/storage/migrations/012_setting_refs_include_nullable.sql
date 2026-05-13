-- Make campaign_setting_refs.include nullable so NULL can distinguish "no
-- filter (include every kind)" from "include nothing" (empty JSON array).
-- The library service now treats NULL as "all" and `[]` as "none"; the prior
-- schema's NOT NULL constraint collapsed those into the same value, which
-- broke the wizard's uncheck-all semantics.

-- SQLite can't alter a column to drop NOT NULL directly, so rebuild the table.

CREATE TABLE campaign_setting_refs_new (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  setting_id TEXT NOT NULL,
  priority INTEGER NOT NULL,
  include TEXT,
  bound_at_version INTEGER NOT NULL,
  track_latest INTEGER NOT NULL DEFAULT 0,
  bound_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, setting_id)
);

INSERT INTO campaign_setting_refs_new
  (campaign_id, setting_id, priority, include, bound_at_version, track_latest, bound_at)
SELECT campaign_id, setting_id, priority, include, bound_at_version, track_latest, bound_at
FROM campaign_setting_refs;

DROP TABLE campaign_setting_refs;
ALTER TABLE campaign_setting_refs_new RENAME TO campaign_setting_refs;
