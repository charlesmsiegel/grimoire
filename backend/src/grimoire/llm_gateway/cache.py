"""Embedding cache backed by the `embedding_cache` table.

Vectors are stored as little-endian float32 blobs (the same encoding
`sqlite-vec` expects). Keys are `(sha256(text), model_id)` so a model
swap forces a recompute.
"""

from __future__ import annotations

import array
import asyncio
import hashlib

from grimoire.storage.db import Database
from grimoire.util import now_iso

_FLOAT_TYPECODE = "f"  # 32-bit float, native byte order


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vector_to_blob(vector: list[float]) -> bytes:
    return array.array(_FLOAT_TYPECODE, vector).tobytes()


def _blob_to_vector(blob: bytes) -> list[float]:
    arr = array.array(_FLOAT_TYPECODE)
    arr.frombytes(blob)
    return arr.tolist()


class EmbeddingCache:
    """LRU-by-`cached_at` cache. All operations are async-safe."""

    def __init__(self, db: Database, *, max_entries: int = 100_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._db = db
        self._max_entries = max_entries
        self._write_lock = asyncio.Lock()

    @staticmethod
    def hash_text(text: str) -> str:
        return _text_hash(text)

    async def get(self, text: str, model_id: str) -> list[float] | None:
        row = await self._db.fetchone(
            "SELECT vector FROM embedding_cache WHERE text_hash = ? AND model_id = ?",
            (_text_hash(text), model_id),
        )
        if row is None:
            return None
        await self._touch(_text_hash(text), model_id)
        return _blob_to_vector(bytes(row["vector"]))

    async def get_many(self, texts: list[str], model_id: str) -> dict[str, list[float]]:
        """Bulk lookup keyed by *text* (not hash)."""
        if not texts:
            return {}
        unique = list(dict.fromkeys(texts))
        hashes = [_text_hash(t) for t in unique]
        placeholders = ",".join("?" for _ in hashes)
        rows = await self._db.fetchall(
            f"SELECT text_hash, vector FROM embedding_cache "
            f"WHERE model_id = ? AND text_hash IN ({placeholders})",
            (model_id, *hashes),
        )
        by_hash = {row["text_hash"]: _blob_to_vector(bytes(row["vector"])) for row in rows}
        result: dict[str, list[float]] = {}
        for text, h in zip(unique, hashes, strict=True):
            if h in by_hash:
                result[text] = by_hash[h]
        if result:
            await self._touch_many(list({_text_hash(t) for t in result}), model_id)
        return result

    async def set_many(self, items: list[tuple[str, list[float]]], model_id: str) -> None:
        if not items:
            return
        now = now_iso()
        async with self._write_lock, self._db.acquire() as conn:
            for text, vector in items:
                await conn.execute(
                    "INSERT OR REPLACE INTO embedding_cache "
                    "(text_hash, model_id, vector, cached_at) VALUES (?, ?, ?, ?)",
                    (_text_hash(text), model_id, _vector_to_blob(vector), now),
                )
            await self._evict_if_needed(conn)

    async def clear(self) -> None:
        async with self._write_lock:
            await self._db.execute("DELETE FROM embedding_cache")

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) AS c FROM embedding_cache")
        return int(row["c"]) if row else 0

    async def _touch(self, text_hash: str, model_id: str) -> None:
        await self._db.execute(
            "UPDATE embedding_cache SET cached_at = ? WHERE text_hash = ? AND model_id = ?",
            (now_iso(), text_hash, model_id),
        )

    async def _touch_many(self, hashes: list[str], model_id: str) -> None:
        if not hashes:
            return
        placeholders = ",".join("?" for _ in hashes)
        await self._db.execute(
            f"UPDATE embedding_cache SET cached_at = ? "
            f"WHERE model_id = ? AND text_hash IN ({placeholders})",
            (now_iso(), model_id, *hashes),
        )

    async def _evict_if_needed(self, conn) -> None:
        async with conn.execute("SELECT COUNT(*) AS c FROM embedding_cache") as cur:
            row = await cur.fetchone()
        count = int(row["c"]) if row else 0
        if count <= self._max_entries:
            return
        excess = count - self._max_entries
        await conn.execute(
            "DELETE FROM embedding_cache WHERE rowid IN ("
            "SELECT rowid FROM embedding_cache ORDER BY cached_at ASC, rowid ASC LIMIT ?)",
            (excess,),
        )
