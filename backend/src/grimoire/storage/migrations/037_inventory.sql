-- Inventory subsystem (#444). Derived from per-holder overlay `inventory:`
-- sections, which are the source of truth. Rebuilt from files by the watcher.
-- No branch_id: branches were removed in migration 036.

CREATE TABLE inventory_holdings (
  id           TEXT PRIMARY KEY,   -- campaign_id:holder_kind:holder_id:item_ref
  campaign_id  TEXT NOT NULL,
  holder_kind  TEXT NOT NULL,      -- 'character' | 'location'
  holder_id    TEXT NOT NULL,
  item_ref     TEXT NOT NULL,
  item_name    TEXT NOT NULL,
  quantity     INTEGER NOT NULL,
  fungible     INTEGER NOT NULL DEFAULT 0,
  equipped     INTEGER NOT NULL DEFAULT 0,
  provenance   TEXT,
  notes        TEXT
);
CREATE INDEX idx_inv_holder ON inventory_holdings(campaign_id, holder_kind, holder_id);
CREATE INDEX idx_inv_item   ON inventory_holdings(campaign_id, item_ref);

CREATE TABLE inventory_flags (
  id           TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL,
  turn_id      TEXT,
  op_json      TEXT NOT NULL,      -- the originating InventoryOperation as JSON
  flag_reason  TEXT NOT NULL,      -- low_confidence | reconciled_* | unresolved_*
  resolved     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);
CREATE INDEX idx_inv_flags_campaign ON inventory_flags(campaign_id, resolved);
