-- Add full body column to posts so paginated reads can serve from SQLite.
ALTER TABLE posts ADD COLUMN body TEXT NOT NULL DEFAULT '';
