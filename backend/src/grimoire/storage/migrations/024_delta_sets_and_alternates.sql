-- Swipes/alternates: introduce delta_set_id as a first-class concept on the
-- deltas table, plus a materialized view of which delta set is currently the
-- primary alternate per (campaign, branch, post).
--
-- See docs/superpowers/specs/2026-05-19-swipes-alternates-design.md.
--
-- Pre-existing deltas have delta_set_id IS NULL ("ungrouped"); new helpers
-- treat NULL as an opaque ungrouped set and never join across it.

ALTER TABLE deltas ADD COLUMN delta_set_id TEXT;

CREATE INDEX IF NOT EXISTS idx_deltas_set
    ON deltas(campaign_id, branch_id, delta_set_id);

CREATE TABLE current_alternate_delta_sets (
    campaign_id     TEXT NOT NULL,
    branch_id       TEXT NOT NULL,
    post_id         TEXT NOT NULL,
    delta_set_id    TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id, post_id)
);

CREATE INDEX IF NOT EXISTS idx_current_alt_sets_branch
    ON current_alternate_delta_sets(campaign_id, branch_id);

CREATE INDEX IF NOT EXISTS idx_current_alt_sets_set
    ON current_alternate_delta_sets(campaign_id, branch_id, delta_set_id);
