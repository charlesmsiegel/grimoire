-- Add role_tags column to campaign_pcs for PC-to-greeting matching.
ALTER TABLE campaign_pcs ADD COLUMN role_tags TEXT NOT NULL DEFAULT '[]';
