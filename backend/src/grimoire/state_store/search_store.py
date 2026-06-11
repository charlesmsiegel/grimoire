"""SearchStore — embedding storage and vector/keyword search.

Extracted from :class:`~grimoire.state_store.store.StateStore` (#521). Thin
coordinator over :mod:`grimoire.state_store.search`, owning connection
acquisition and result merging.
"""

from __future__ import annotations

from collections.abc import Iterable

from grimoire.state_store.search import (
    SearchHit,
    delete_embeddings_for_ref,
    insert_embedding,
    keyword_search_facts,
    keyword_search_library,
)
from grimoire.state_store.search import vector_search as _vector_search
from grimoire.storage import Database
from grimoire.util import new_id, now_iso


class SearchStore:
    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def add_embedding(
        self,
        *,
        ref: str,
        scope: str,
        source_kind: str,
        text: str,
        vector: list[float],
        model: str,
        campaign_id: str | None = None,
    ) -> str:
        embedding_id = new_id("emb", length=16)
        async with self._db.acquire() as conn:
            await insert_embedding(
                conn,
                embedding_id=embedding_id,
                scope=scope,
                ref=ref,
                source_kind=source_kind,
                text=text,
                vector=vector,
                model=model,
                embedded_at=now_iso(),
                campaign_id=campaign_id,
            )
        return embedding_id

    async def delete_embeddings(self, ref: str) -> int:
        async with self._db.acquire() as conn:
            return await delete_embeddings_for_ref(conn, ref)

    async def vector_search(
        self,
        *,
        query_vector: list[float],
        campaign_id: str,
        source_kinds: list[str] | None = None,
        include_library: bool = True,
        top_k: int = 8,
    ) -> list[SearchHit]:
        async with self._db.acquire() as conn:
            return await _vector_search(
                conn,
                query_vector=query_vector,
                campaign_id=campaign_id,
                source_kinds=source_kinds,
                include_library=include_library,
                top_k=top_k,
            )

    async def keyword_search(
        self,
        *,
        query: str,
        campaign_id: str | None = None,
        kinds: Iterable[str] = ("fact",),
        top_k: int = 5,
        include_retired: bool = False,
    ) -> list[SearchHit]:
        kinds_set = set(kinds)
        hits: list[SearchHit] = []
        async with self._db.acquire() as conn:
            if "fact" in kinds_set:
                hits.extend(
                    await keyword_search_facts(
                        conn,
                        query=query,
                        campaign_id=campaign_id,
                        include_retired=include_retired,
                        top_k=top_k,
                    )
                )
            if kinds_set & {"character", "item", "location", "lore", "faction"}:
                hits.extend(
                    await keyword_search_library(
                        conn,
                        query=query,
                        kinds=list(
                            kinds_set
                            & {
                                "character",
                                "item",
                                "location",
                                "lore",
                                "faction",
                            }
                        ),
                        top_k=top_k,
                    )
                )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
