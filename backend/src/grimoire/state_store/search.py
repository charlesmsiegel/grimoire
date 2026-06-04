"""Vector + keyword search over embeddings and FTS5 tables.

Vector search filters by campaign: embeddings keyed to ``campaign_id`` are
always included; ``scope='library'`` embeddings are included only when the
caller asks for them. Distance is cosine via sqlite-vec.

Keyword search runs against ``facts_fts`` (and could be extended to
``library_index_fts``); results are returned with a relevance score.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import aiosqlite


def serialize_vector(vector: list[float]) -> bytes:
    """Pack a float vector into the little-endian f32 BLOB sqlite-vec expects."""
    return struct.pack(f"<{len(vector)}f", *vector)


def deserialize_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


@dataclass(frozen=True)
class SearchHit:
    ref: str
    scope: str
    source_kind: str | None
    text: str | None
    score: float
    campaign_id: str | None = None


async def insert_embedding(
    conn: aiosqlite.Connection,
    *,
    embedding_id: str,
    scope: str,
    ref: str,
    source_kind: str | None,
    text: str | None,
    vector: list[float],
    model: str,
    embedded_at: str,
    campaign_id: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO embeddings (
          id, scope, ref, source_kind, text, vector, embedded_at, model, campaign_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          scope = excluded.scope,
          ref = excluded.ref,
          source_kind = excluded.source_kind,
          text = excluded.text,
          vector = excluded.vector,
          embedded_at = excluded.embedded_at,
          model = excluded.model,
          campaign_id = excluded.campaign_id
        """,
        (
            embedding_id,
            scope,
            ref,
            source_kind,
            text,
            serialize_vector(vector),
            embedded_at,
            model,
            campaign_id,
        ),
    )


async def delete_embeddings_for_ref(conn: aiosqlite.Connection, ref: str) -> int:
    cur = await conn.execute("DELETE FROM embeddings WHERE ref = ?", (ref,))
    return cur.rowcount or 0


async def vector_search(
    conn: aiosqlite.Connection,
    *,
    query_vector: list[float],
    campaign_id: str,
    source_kinds: list[str] | None = None,
    include_library: bool = True,
    top_k: int = 8,
) -> list[SearchHit]:
    """Top-K nearest neighbors by cosine distance via sqlite-vec.

    Returns matches in increasing distance order (closest first).
    """
    scope_clause = "(campaign_id = ?"
    params: list[object] = [campaign_id]
    if include_library:
        scope_clause += " OR scope = 'library'"
    scope_clause += ")"

    kind_clause = ""
    if source_kinds:
        placeholders = ",".join("?" * len(source_kinds))
        kind_clause = f" AND source_kind IN ({placeholders})"
        params.extend(source_kinds)

    sql = (
        f"SELECT id, scope, ref, source_kind, text, campaign_id, "
        f"vec_distance_cosine(vector, ?) AS distance "
        f"FROM embeddings "
        f"WHERE {scope_clause}{kind_clause} AND vector IS NOT NULL "
        f"ORDER BY distance ASC LIMIT ?"
    )
    qvec = serialize_vector(query_vector)
    cur = await conn.execute(sql, (qvec, *params, top_k))
    rows = await cur.fetchall()
    await cur.close()

    hits: list[SearchHit] = []
    for row in rows:
        distance = float(row["distance"])
        # Convert distance to a similarity-style score (higher = better).
        score = 1.0 - distance
        hits.append(
            SearchHit(
                ref=row["ref"],
                scope=row["scope"],
                source_kind=row["source_kind"],
                text=row["text"],
                score=score,
                campaign_id=row["campaign_id"],
            )
        )
    return hits


async def keyword_search_facts(
    conn: aiosqlite.Connection,
    *,
    query: str,
    campaign_id: str | None = None,
    include_retired: bool = False,
    top_k: int = 5,
) -> list[SearchHit]:
    """FTS5 search across ``facts``."""
    where: list[str] = ["facts_fts MATCH ?"]
    params: list[object] = [query]
    if campaign_id is not None:
        where.append("facts.campaign_id = ?")
        params.append(campaign_id)
    if not include_retired:
        where.append("facts.retired = 0")
    sql = (
        "SELECT facts.id AS id, facts.text AS text, facts.campaign_id AS campaign_id, "
        "bm25(facts_fts) AS rank "
        "FROM facts_fts JOIN facts ON facts.rowid = facts_fts.rowid "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY rank ASC LIMIT ?"
    )
    cur = await conn.execute(sql, (*params, top_k))
    rows = await cur.fetchall()
    await cur.close()
    return [
        SearchHit(
            ref=row["id"],
            scope="campaign-sqlite",
            source_kind="fact",
            text=row["text"],
            score=-float(row["rank"]),
            campaign_id=row["campaign_id"],
        )
        for row in rows
    ]


async def keyword_search_library(
    conn: aiosqlite.Connection,
    *,
    query: str,
    world_id: str | None = None,
    kinds: list[str] | None = None,
    top_k: int = 5,
) -> list[SearchHit]:
    """FTS5 search across ``library_index``."""
    where: list[str] = ["library_index_fts MATCH ?"]
    params: list[object] = [query]
    if world_id is not None:
        where.append("library_index.world_id = ?")
        params.append(world_id)
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        where.append(f"library_index.kind IN ({placeholders})")
        params.extend(kinds)
    sql = (
        "SELECT library_index.id AS id, library_index.kind AS kind, "
        "library_index.body AS body, library_index.name AS name, "
        "bm25(library_index_fts) AS rank "
        "FROM library_index_fts "
        "JOIN library_index ON library_index.rowid = library_index_fts.rowid "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY rank ASC LIMIT ?"
    )
    cur = await conn.execute(sql, (*params, top_k))
    rows = await cur.fetchall()
    await cur.close()
    return [
        SearchHit(
            ref=row["id"],
            scope="library",
            source_kind=row["kind"],
            text=(row["body"] or row["name"] or "")[:400],
            score=-float(row["rank"]),
        )
        for row in rows
    ]


__all__ = [
    "SearchHit",
    "delete_embeddings_for_ref",
    "deserialize_vector",
    "insert_embedding",
    "keyword_search_facts",
    "keyword_search_library",
    "serialize_vector",
    "vector_search",
]
