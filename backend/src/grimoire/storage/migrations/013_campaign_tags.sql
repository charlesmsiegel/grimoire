-- Persist the tags field the campaign-create wizard already collects.
-- Stored as a JSON array string so multi-tag filtering can use json_each later.

ALTER TABLE campaigns ADD COLUMN tags TEXT;
