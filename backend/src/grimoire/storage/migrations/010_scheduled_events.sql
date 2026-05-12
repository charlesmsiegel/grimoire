-- Scheduled events owned by the Time Engine.
--
-- Each row represents a future (or past, once triggered) event keyed to an
-- in-game moment. The Time Engine consults this table during advancement and
-- marks events triggered when their ``at`` falls in the elapsed window.

CREATE TABLE scheduled_events (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  payload TEXT,
  triggered INTEGER NOT NULL DEFAULT 0,
  triggered_at TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_scheduled_events_campaign ON scheduled_events(campaign_id, branch_id);
CREATE INDEX idx_scheduled_events_at ON scheduled_events(at);
CREATE INDEX idx_scheduled_events_pending ON scheduled_events(triggered, at);
