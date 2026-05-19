-- Per-post character expression state. Latest-row-wins; granular and
-- replayable. Provenance distinguishes player-set (user:pc) from extractor
-- auto-apply (extractor:auto) and review-approved (extractor:reviewed).

CREATE TABLE expression_state (
    id           INTEGER PRIMARY KEY,
    campaign_id  TEXT NOT NULL,
    scene_id     TEXT NOT NULL,
    character_id TEXT NOT NULL,
    turn_id      TEXT NOT NULL,
    post_id      TEXT NOT NULL,
    emotion      TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    set_at       TEXT NOT NULL
);

CREATE INDEX ix_expr_current
    ON expression_state(campaign_id, character_id, turn_id DESC);
