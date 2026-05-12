-- Scene and post indexes (full prose lives in scene markdown files).

CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  slug TEXT NOT NULL,
  file_path TEXT NOT NULL,
  location_ref TEXT,
  in_game_start TEXT,
  in_game_end TEXT,
  pov_character_ref TEXT,
  present_character_refs TEXT,
  present_pc_refs TEXT,
  summary TEXT,
  running_summary TEXT,
  key_beats TEXT,
  tags TEXT,
  emotional_arc TEXT,
  post_count INTEGER NOT NULL DEFAULT 0,
  threads_introduced TEXT,
  threads_paid_off TEXT,
  title TEXT,
  greeting_id TEXT,
  closed INTEGER NOT NULL DEFAULT 0,
  closed_at_turn TEXT
);

CREATE INDEX idx_scenes_campaign ON scenes(campaign_id, branch_id, ordinal);

CREATE TABLE posts (
  id TEXT PRIMARY KEY,
  scene_id TEXT REFERENCES scenes(id) ON DELETE CASCADE,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  turn_id TEXT,
  order_in_scene INTEGER NOT NULL,
  author_kind TEXT,
  author_pc_ref TEXT,
  body_excerpt TEXT,
  body_hash TEXT,
  is_player INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  retconned_from TEXT REFERENCES posts(id)
);

CREATE INDEX idx_posts_scene_order ON posts(scene_id, order_in_scene);
CREATE INDEX idx_posts_campaign ON posts(campaign_id, branch_id);
CREATE INDEX idx_posts_turn ON posts(turn_id);
