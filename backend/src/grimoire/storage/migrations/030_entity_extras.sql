-- Narrative Extras mirror: frontmatter is the SSOT; this table exists for
-- query (substring search, pinned listings, observability). Reads do NOT
-- touch the mirror -- they cascade-resolve frontmatter dicts on the
-- ResolvedEntity. ExtrasService.set/delete materialize the mirror on every
-- write; a periodic reconcile job rebuilds from disk on drift.

CREATE TABLE entity_extras (
    campaign_id    TEXT NOT NULL,            -- '' for library scope
    entity_kind    TEXT NOT NULL,            -- character | location | item | faction
    entity_id      TEXT NOT NULL,
    scope          TEXT NOT NULL,            -- library | campaign-local | override
    key            TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    set_at         TEXT NOT NULL,
    set_by         TEXT NOT NULL,
    PRIMARY KEY (campaign_id, entity_kind, entity_id, scope, key),
    CHECK (
        key NOT LIKE '\_internal\_%' ESCAPE '\'
        AND key NOT LIKE 'mechanics\_%' ESCAPE '\'
        AND key NOT LIKE 'system\_%' ESCAPE '\'
    )
);

CREATE INDEX IF NOT EXISTS idx_entity_extras_entity
    ON entity_extras (campaign_id, entity_kind, entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_extras_key
    ON entity_extras (key);

-- FTS5 mirror for substring search across value_text. The triggers below
-- keep it synchronized with entity_extras. value_json stands in for
-- value_text -- list and dict values land flattened by the service writer.

-- Contentless FTS5: triggers fully populate it. Avoids the
-- ``content='entity_extras'`` external-content path which requires column
-- names on the content table to match the FTS columns -- we want the
-- denormalized ``value_text`` projection that ExtrasMirror computes.
CREATE VIRTUAL TABLE entity_extras_fts USING fts5(
    entity_kind UNINDEXED,
    entity_id   UNINDEXED,
    key         UNINDEXED,
    value_text
);

CREATE TRIGGER entity_extras_ai AFTER INSERT ON entity_extras BEGIN
    INSERT INTO entity_extras_fts(rowid, entity_kind, entity_id, key, value_text)
    VALUES (new.rowid, new.entity_kind, new.entity_id, new.key, new.value_json);
END;

CREATE TRIGGER entity_extras_ad AFTER DELETE ON entity_extras BEGIN
    DELETE FROM entity_extras_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER entity_extras_au AFTER UPDATE ON entity_extras BEGIN
    DELETE FROM entity_extras_fts WHERE rowid = old.rowid;
    INSERT INTO entity_extras_fts(rowid, entity_kind, entity_id, key, value_text)
    VALUES (new.rowid, new.entity_kind, new.entity_id, new.key, new.value_json);
END;
