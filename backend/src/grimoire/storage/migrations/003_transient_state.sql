-- Per-character, per-location, per-faction runtime state.

CREATE TABLE character_state (
  character_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  location_ref TEXT,
  emotional_state TEXT,
  physical_state TEXT,
  immediate_intent TEXT,
  knowledge_state TEXT,
  last_action TEXT,
  last_screen_time_turn TEXT,
  visible_to_pc INTEGER NOT NULL DEFAULT 0,
  drift_score REAL NOT NULL DEFAULT 0,
  tier_pin TEXT,
  current_scene_id TEXT,
  updated_at_turn TEXT,
  PRIMARY KEY (character_ref, branch_id)
);

CREATE INDEX idx_charstate_campaign ON character_state(campaign_id);

CREATE TABLE location_state (
  location_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  weather TEXT,
  time_of_day TEXT,
  occupants TEXT,
  condition TEXT,
  transient_features TEXT,
  updated_at_turn TEXT,
  PRIMARY KEY (location_ref, branch_id)
);

CREATE INDEX idx_locstate_campaign ON location_state(campaign_id);

CREATE TABLE faction_state (
  faction_ref TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  state TEXT,
  updated_at_turn TEXT,
  PRIMARY KEY (faction_ref, branch_id)
);

CREATE INDEX idx_factionstate_campaign ON faction_state(campaign_id);
