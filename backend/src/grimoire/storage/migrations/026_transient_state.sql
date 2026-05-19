-- Field-level transient state with supersession history.
-- See docs/superpowers/specs/2026-05-19-transient-state-design.md.

CREATE TABLE transient_character_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_character_state(id),
    in_game_at     TEXT
);

CREATE INDEX ix_tcs_current
    ON transient_character_state(campaign_id, branch_id, entity_id, field)
    WHERE superseded_by IS NULL;

CREATE INDEX ix_tcs_supersedes
    ON transient_character_state(superseded_by)
    WHERE superseded_by IS NOT NULL;


CREATE TABLE transient_location_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_location_state(id),
    in_game_at     TEXT
);

CREATE INDEX ix_tls_current
    ON transient_location_state(campaign_id, branch_id, entity_id, field)
    WHERE superseded_by IS NULL;

CREATE INDEX ix_tls_supersedes
    ON transient_location_state(superseded_by)
    WHERE superseded_by IS NOT NULL;


CREATE TABLE transient_faction_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_faction_state(id),
    in_game_at     TEXT
);

CREATE INDEX ix_tfs_current
    ON transient_faction_state(campaign_id, branch_id, entity_id, field)
    WHERE superseded_by IS NULL;

CREATE INDEX ix_tfs_supersedes
    ON transient_faction_state(superseded_by)
    WHERE superseded_by IS NOT NULL;


CREATE TABLE transient_scene_state (
    id             INTEGER PRIMARY KEY,
    campaign_id    TEXT    NOT NULL,
    branch_id      TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    field          TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    provenance     TEXT    NOT NULL,
    source_post_id TEXT,
    confidence     REAL    NOT NULL DEFAULT 1.0,
    created_at     TEXT    NOT NULL,
    expires_at     TEXT,
    superseded_by  INTEGER REFERENCES transient_scene_state(id),
    in_game_at     TEXT
);

CREATE INDEX ix_tss_current
    ON transient_scene_state(campaign_id, branch_id, entity_id, field)
    WHERE superseded_by IS NULL;

CREATE INDEX ix_tss_supersedes
    ON transient_scene_state(superseded_by)
    WHERE superseded_by IS NOT NULL;
