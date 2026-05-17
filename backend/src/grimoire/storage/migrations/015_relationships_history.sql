-- Append-only event log per relationship (spec 08 §Relationships, design
-- doc 2026-05-17 §12). Each row stores a JSON array of RelationshipEvent
-- records so callers can render a "relationship timeline" without a
-- sibling table or extra joins. Defaults to '[]' so existing rows stay
-- valid after the ALTER.

ALTER TABLE relationships ADD COLUMN history TEXT NOT NULL DEFAULT '[]';
