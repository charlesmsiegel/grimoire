-- Per-(provider, model, mode) failure-rate tracking for the Extractor's
-- mode-selection / auto-disable feedback loop. The selector (`select_mode`)
-- consults this table to decide whether a configured Together / Tool-use
-- mode is currently healthy or should fall back to Separate.

CREATE TABLE extractor_mode_health (
    provider_id    TEXT NOT NULL,
    model          TEXT NOT NULL,
    mode           TEXT NOT NULL,           -- 'together' | 'tool_use'
    window_start   TEXT NOT NULL,           -- ISO 8601 timestamp; rolling 24h window
    total_calls    INTEGER NOT NULL DEFAULT 0,
    failures       INTEGER NOT NULL DEFAULT 0,
    disabled_at    TEXT,                    -- nullable; set when threshold crossed
    re_enabled_at  TEXT,                    -- nullable; cleared on user re-enable
    PRIMARY KEY (provider_id, model, mode)
);
