-- Clear the embedding cache after the vector wire format was pinned to
-- explicit little-endian f32 (previously the cache packed via array.array in
-- the host's native byte order). On a big-endian host, pre-existing rows would
-- otherwise be misread as little-endian garbage with no way to tell them apart.
-- The cache is derived and recomputed on demand, so dropping every row is safe.
DELETE FROM embedding_cache;
