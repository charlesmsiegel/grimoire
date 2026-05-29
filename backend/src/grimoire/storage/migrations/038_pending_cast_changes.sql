CREATE TABLE pending_cast_changes (
    id            TEXT PRIMARY KEY,
    campaign_id   TEXT NOT NULL,
    scene_id      TEXT NOT NULL,
    character_ref TEXT NOT NULL,
    change        TEXT NOT NULL CHECK (change IN ('enter', 'leave')),
    is_pc         INTEGER NOT NULL DEFAULT 0,
    evidence      TEXT NOT NULL DEFAULT '',
    confidence    REAL NOT NULL DEFAULT 0.0,
    turn_id       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'confirmed', 'dismissed')),
    created_at    TEXT NOT NULL
);

CREATE INDEX idx_pending_cast_changes_scene_status
    ON pending_cast_changes (scene_id, status);
