"""Vector + keyword hybrid fact search.

Implements the `FactSearchIndex` protocol against the State Store's
`facts_fts` FTS5 table (keyword) and `embeddings` sqlite-vec column
(vector). The two result sets are merged with reciprocal-rank fusion so
neither path dominates when one is unavailable.

The vector half needs an embedding for the query; that comes from the
LLM Gateway via the configured `extractor` (or override) embedding task.
If embedding fails or no provider is configured, the search degrades to
keyword-only.
"""

from __future__ import annotations

import logging
import struct
from typing import Protocol

from grimoire.continuity.protocols import ContinuityStore, FactSearchIndex
from grimoire.continuity.types import Fact, FactId

logger = logging.getLogger(__name__)


class QueryEmbedder(Protocol):
    """Minimal seam over the LLM Gateway's embed call."""

    async def embed(self, task: str, texts: list[str]) -> list[list[float]]: ...


def _serialise_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


class HybridFactSearchIndex(FactSearchIndex):
    """Fact search backed by SQLite FTS5 + sqlite-vec.

    The index does not maintain its own copy of facts — it queries the
    SQLite tables directly. The `store` argument is used only to fetch
    the materialised `Fact` rows after candidate ids come back from the
    search.

    Parameters
    ----------
    store:
        A `ContinuityStore` (typically `SqliteContinuityStore`) used to
        materialise facts by id.
    db:
        The grimoire `Database` connection pool.
    campaign_id, branch_id:
        Scope the searches to one timeline.
    embedder:
        Optional. If provided, query embeddings are obtained from this
        seam and a vector pass is run; otherwise the search is keyword-only.
    embed_task:
        Task name to pass to the embedder. Defaults to ``"extractor"``
        which is the canonical embedding route in the LLM Gateway spec.
    rrf_k:
        Reciprocal-rank-fusion constant. Higher means flatter rank
        weighting; 60 is the literature default.
    """

    def __init__(
        self,
        store: ContinuityStore,
        db,
        *,
        campaign_id: str,
        branch_id: str,
        embedder: QueryEmbedder | None = None,
        embed_task: str = "extractor",
        rrf_k: int = 60,
    ) -> None:
        self._store = store
        self._db = db
        self._campaign_id = campaign_id
        self._branch_id = branch_id
        self._embedder = embedder
        self._embed_task = embed_task
        self._rrf_k = rrf_k

    async def search(
        self, query: str, top_k: int, *, include_retired: bool = False
    ) -> list[tuple[Fact, float]]:
        if not query.strip():
            return []
        kw_hits = await self._keyword_search(
            query, top_k=top_k * 2, include_retired=include_retired
        )
        vec_hits = await self._vector_search(
            query, top_k=top_k * 2, include_retired=include_retired
        )
        if not kw_hits and not vec_hits:
            return []

        fused: dict[FactId, float] = {}
        for rank, fact_id in enumerate(kw_hits):
            fused[fact_id] = fused.get(fact_id, 0.0) + 1.0 / (self._rrf_k + rank + 1)
        for rank, fact_id in enumerate(vec_hits):
            fused[fact_id] = fused.get(fact_id, 0.0) + 1.0 / (self._rrf_k + rank + 1)

        ordered = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:top_k]

        out: list[tuple[Fact, float]] = []
        for fact_id, score in ordered:
            fact = await self._store.get_fact(fact_id)
            if fact is None:
                continue
            if fact.retired and not include_retired:
                continue
            out.append((fact, score))
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _keyword_search(
        self, query: str, *, top_k: int, include_retired: bool
    ) -> list[FactId]:
        sanitised = _sanitise_fts_query(query)
        if not sanitised:
            return []
        where = [
            "facts_fts MATCH ?",
            "facts.campaign_id = ?",
            "facts.branch_id = ?",
        ]
        params: list[object] = [sanitised, self._campaign_id, self._branch_id]
        if not include_retired:
            where.append("facts.retired = 0")
        sql = (
            "SELECT facts.id AS id, bm25(facts_fts) AS rank "
            "FROM facts_fts JOIN facts ON facts.rowid = facts_fts.rowid "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY rank ASC LIMIT ?"
        )
        try:
            rows = await self._db.fetchall(sql, (*params, top_k))
        except Exception:
            logger.exception("keyword fact search failed")
            return []
        return [row["id"] for row in rows]

    async def _vector_search(
        self, query: str, *, top_k: int, include_retired: bool
    ) -> list[FactId]:
        if self._embedder is None:
            return []
        try:
            vectors = await self._embedder.embed(self._embed_task, [query])
        except Exception:
            logger.exception("embedder failed for query %r; falling back to keyword-only", query)
            return []
        if not vectors or not vectors[0]:
            return []
        qvec = _serialise_vector(list(vectors[0]))

        where = [
            "embeddings.source_kind = 'fact'",
            "(embeddings.campaign_id = ? OR embeddings.campaign_id IS NULL)",
            "embeddings.vector IS NOT NULL",
            "facts.branch_id = ?",
        ]
        params: list[object] = [self._campaign_id, self._branch_id]
        if not include_retired:
            where.append("facts.retired = 0")
        sql = (
            "SELECT embeddings.ref AS id, "
            "vec_distance_cosine(embeddings.vector, ?) AS distance "
            "FROM embeddings JOIN facts ON facts.id = embeddings.ref "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY distance ASC LIMIT ?"
        )
        try:
            rows = await self._db.fetchall(sql, (qvec, *params, top_k))
        except Exception:
            logger.exception("vector fact search failed")
            return []
        return [row["id"] for row in rows]


def _sanitise_fts_query(query: str) -> str:
    """Strip FTS5 operators from a free-text query.

    FTS5 treats characters like ``"``, ``*``, ``:`` and ``-`` as
    operators, so a contradiction-check candidate that mentions a colon
    or quote would either error or match unintended things. We replace
    them with spaces and split on whitespace; a trailing wildcard makes
    short stems match longer tokens.
    """
    out: list[str] = []
    for tok in query.split():
        cleaned = "".join(ch for ch in tok if ch.isalnum() or ch == "_")
        if cleaned:
            out.append(cleaned)
    return " OR ".join(out)


__all__ = ["HybridFactSearchIndex", "QueryEmbedder"]
