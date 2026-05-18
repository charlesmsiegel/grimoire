-- Track per-PC last-played-at for the rich PC switcher (spec frontend §8).
-- A turn submitted for a PC stamps this column so the switcher can show
-- "Aleksandr (vampire) — scene 47, Camden club, last played 12m ago".

ALTER TABLE campaign_pcs ADD COLUMN last_played_at TEXT;
