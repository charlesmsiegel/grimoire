-- Campaign-level fork (spec 2026-05-19-fork). Adds provenance columns to
-- ``campaigns`` so a fork knows where it diverged from, and a
-- ``pending_forks`` queue table so a fork requested mid-stream survives
-- restart and is processed once streaming completes.

ALTER TABLE campaigns ADD COLUMN forked_from_campaign_id TEXT;
ALTER TABLE campaigns ADD COLUMN forked_at_post_id TEXT;
ALTER TABLE campaigns ADD COLUMN forked_at_turn_id TEXT;
ALTER TABLE campaigns ADD COLUMN forked_image_handling TEXT;

CREATE INDEX ix_campaigns_forked_from
    ON campaigns(forked_from_campaign_id)
    WHERE forked_from_campaign_id IS NOT NULL;

CREATE TABLE pending_forks (
    id                    TEXT PRIMARY KEY,
    source_campaign_id    TEXT NOT NULL,
    new_campaign_id       TEXT NOT NULL,
    new_name              TEXT NOT NULL,
    fork_at_post_id       TEXT,
    description           TEXT,
    make_active           INTEGER NOT NULL DEFAULT 0,
    enqueued_at           TEXT NOT NULL,
    started_at            TEXT,
    completed_at          TEXT,
    error                 TEXT
);

CREATE INDEX idx_pending_forks_source ON pending_forks(source_campaign_id, completed_at);
