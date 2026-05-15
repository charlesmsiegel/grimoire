-- Library + campaign content indexes, library snapshots for version-pinned campaigns.

CREATE TABLE library_index (
  id TEXT PRIMARY KEY,
  world_id TEXT,
  kind TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  name TEXT,
  path TEXT NOT NULL,
  frontmatter TEXT NOT NULL,
  body TEXT,
  body_compressed TEXT,
  tags TEXT,
  keywords TEXT,
  file_mtime TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TEXT NOT NULL,
  version INTEGER NOT NULL
);

CREATE INDEX idx_libidx_world ON library_index(world_id);
CREATE INDEX idx_libidx_kind ON library_index(kind);
CREATE INDEX idx_libidx_asset_id ON library_index(asset_id);

CREATE VIRTUAL TABLE library_index_fts USING fts5(
  name, body, tags, keywords,
  content='library_index', content_rowid='rowid'
);

-- Keep the FTS table in sync via triggers.
CREATE TRIGGER library_index_ai AFTER INSERT ON library_index BEGIN
  INSERT INTO library_index_fts(rowid, name, body, tags, keywords)
  VALUES (new.rowid, new.name, new.body, new.tags, new.keywords);
END;

CREATE TRIGGER library_index_ad AFTER DELETE ON library_index BEGIN
  INSERT INTO library_index_fts(library_index_fts, rowid, name, body, tags, keywords)
  VALUES('delete', old.rowid, old.name, old.body, old.tags, old.keywords);
END;

CREATE TRIGGER library_index_au AFTER UPDATE ON library_index BEGIN
  INSERT INTO library_index_fts(library_index_fts, rowid, name, body, tags, keywords)
  VALUES('delete', old.rowid, old.name, old.body, old.tags, old.keywords);
  INSERT INTO library_index_fts(rowid, name, body, tags, keywords)
  VALUES (new.rowid, new.name, new.body, new.tags, new.keywords);
END;

CREATE TABLE campaign_content_index (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  entity_subkind TEXT,
  asset_id TEXT,
  path TEXT NOT NULL,
  frontmatter TEXT,
  body TEXT,
  file_mtime TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  indexed_at TEXT NOT NULL
);

CREATE INDEX idx_ccidx_campaign ON campaign_content_index(campaign_id);
CREATE INDEX idx_ccidx_kind ON campaign_content_index(kind);

CREATE TABLE library_snapshots (
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  library_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  frontmatter TEXT NOT NULL,
  body TEXT,
  snapshot_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, branch_id, library_id)
);

CREATE INDEX idx_libsnap_lib ON library_snapshots(library_id);
