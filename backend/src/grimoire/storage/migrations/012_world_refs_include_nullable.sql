-- Make campaign_world_refs.include nullable so NULL can distinguish "no
-- filter (include every kind)" from "include nothing" (empty JSON array).
-- The library service now treats NULL as "all" and `[]` as "none"; the prior
-- schema's NOT NULL constraint collapsed those into the same value, which
-- broke the wizard's uncheck-all semantics.

-- SQLite can't alter a column to drop NOT NULL directly, so rebuild the table.

CREATE TABLE campaign_world_refs_new (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  world_id TEXT NOT NULL,
  priority INTEGER NOT NULL,
  include TEXT,
  bound_at_version INTEGER NOT NULL,
  track_latest INTEGER NOT NULL DEFAULT 0,
  bound_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, world_id)
);

INSERT INTO campaign_world_refs_new
  (campaign_id, world_id, priority, include, bound_at_version, track_latest, bound_at)
SELECT campaign_id, world_id, priority, include, bound_at_version, track_latest, bound_at
FROM campaign_world_refs;

DROP TABLE campaign_world_refs;
ALTER TABLE campaign_world_refs_new RENAME TO campaign_world_refs;
