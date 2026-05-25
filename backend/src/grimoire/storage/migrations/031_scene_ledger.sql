CREATE TABLE scene_ledger (
    id               TEXT PRIMARY KEY,
    campaign_id      TEXT NOT NULL,
    summary          TEXT NOT NULL,
    greeting_id      TEXT,
    source           TEXT NOT NULL CHECK (source IN ('greeting', 'llm', 'user')),
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'used', 'dismissed')),
    created_at       TEXT NOT NULL,
    used_in_scene_id TEXT,
    proposed_location TEXT,
    proposed_cast    TEXT
);

CREATE INDEX idx_scene_ledger_campaign_status
    ON scene_ledger (campaign_id, status);
