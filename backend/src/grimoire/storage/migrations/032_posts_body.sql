-- Add full body and NPC ref columns to posts so paginated reads can serve from SQLite.
ALTER TABLE posts ADD COLUMN body TEXT NOT NULL DEFAULT '';
ALTER TABLE posts ADD COLUMN author_npc_ref TEXT;
