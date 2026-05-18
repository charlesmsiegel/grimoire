-- Per-campaign ImageGen config (trigger policy, active backend id, fallback
-- backend id). Stored as JSON-encoded text. NULL = "use defaults". See
-- imagegen/config.py for the schema.

ALTER TABLE campaigns ADD COLUMN imagegen_config TEXT;
