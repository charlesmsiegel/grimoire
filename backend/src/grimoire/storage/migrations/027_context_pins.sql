-- User pin / exclude overrides for the Context Inspector.
--
-- A pin protects a source or entity from budget-driven eviction. An exclude
-- removes a source or entity from the candidate set before assembly.
-- Pins/excludes scope to a (campaign_id, branch_id). TTL is stored as an
-- integer turn count alongside ``created_at_turn_id``; expiry is resolved
-- by counting turns elapsed since creation using ``turn_audits.created_at``
-- ordering. ``cleared_at`` marks an override that the user cancelled or
-- that aged out.

CREATE TABLE context_pins (
    id                  TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    branch_id           TEXT NOT NULL,
    kind                TEXT NOT NULL,            -- pin | exclude
    target_kind         TEXT NOT NULL,            -- source | entity
    target_source_id    TEXT,
    target_entity_kind  TEXT,
    target_entity_id    TEXT,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at_turn_id  TEXT,
    ttl_turns           INTEGER,                  -- NULL = never expires
    cleared_at          TEXT,
    cleared_by          TEXT
);

CREATE INDEX IF NOT EXISTS ix_ctx_pins_active
    ON context_pins(campaign_id, branch_id)
    WHERE cleared_at IS NULL;
