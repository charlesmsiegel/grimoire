-- Campaign metadata, composition refs, PCs, branches.

CREATE TABLE campaigns (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  mechanics_module TEXT,
  style_guide_id TEXT,
  image_preset_id TEXT,
  inline_style_guide TEXT,
  content_boundaries TEXT,
  greeting_id TEXT,
  created_at TEXT NOT NULL,
  last_played_at TEXT,
  config TEXT
);

CREATE TABLE campaign_world_refs (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  world_id TEXT NOT NULL,
  priority INTEGER NOT NULL,
  include TEXT NOT NULL,
  bound_at_version INTEGER NOT NULL,
  track_latest INTEGER NOT NULL DEFAULT 0,
  bound_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, world_id)
);

CREATE TABLE campaign_pcs (
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  character_ref TEXT NOT NULL,
  display_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  added_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, character_ref)
);

CREATE TABLE branches (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  parent_branch_id TEXT REFERENCES branches(id),
  forked_from_turn_id TEXT,
  label TEXT,
  rng_seed INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_branches_campaign ON branches(campaign_id);
CREATE INDEX idx_branches_parent ON branches(parent_branch_id);
