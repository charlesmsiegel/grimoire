-- Drift-check cadence counter (spec characters-remaining §3). The
-- Characters service bumps this on `mark_screen_time` and resets it after
-- a drift check runs, so `maybe_check_drift` can throttle expensive LLM
-- calls to once per N appearances without an in-process counter. Defaults
-- to 0 so existing rows stay valid after the ALTER.

ALTER TABLE character_state ADD COLUMN appearances_since_last_drift_check INTEGER NOT NULL DEFAULT 0;
