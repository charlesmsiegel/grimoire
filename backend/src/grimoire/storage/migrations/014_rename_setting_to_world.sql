-- Rename the game-domain "setting" concept to "world" at the database layer.
-- Historical migrations 001/002/012 still create the tables under their
-- original names; this migration brings any already-applied database in line
-- with the post-rename code.

ALTER TABLE campaign_setting_refs RENAME TO campaign_world_refs;
ALTER TABLE campaign_world_refs RENAME COLUMN setting_id TO world_id;

ALTER TABLE library_index RENAME COLUMN setting_id TO world_id;
DROP INDEX IF EXISTS idx_libidx_setting;
CREATE INDEX idx_libidx_world ON library_index(world_id);
