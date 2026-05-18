-- Record every change to a campaign's `mechanics_module` value so the UI can
-- detect "inactive but preserved" sheets after a mid-campaign switch.

CREATE TABLE campaign_mechanics_history (
    campaign_id TEXT NOT NULL,
    mechanics_module TEXT,           -- nullable; the current value after the switch
    switched_at TEXT NOT NULL,       -- ISO 8601 timestamp
    switched_from TEXT,              -- the previous mechanics_module (may be NULL)
    source TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (campaign_id, switched_at)
);

CREATE INDEX IF NOT EXISTS idx_campaign_mechanics_history_campaign
    ON campaign_mechanics_history (campaign_id);
