-- Generated image metadata (image files live under data/campaigns/<id>/images/).

CREATE TABLE images (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  scene_id TEXT REFERENCES scenes(id),
  post_id TEXT REFERENCES posts(id),
  file_path TEXT NOT NULL,
  thumbnail_path TEXT,
  prompt TEXT,
  negative_prompt TEXT,
  params TEXT,
  backend TEXT,
  model TEXT,
  seed INTEGER,
  created_at TEXT,
  user_starred INTEGER NOT NULL DEFAULT 0,
  tags TEXT
);

CREATE INDEX idx_images_campaign ON images(campaign_id, branch_id);
CREATE INDEX idx_images_scene ON images(scene_id);
