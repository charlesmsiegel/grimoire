"""HybridFactSearchIndex — keyword (FTS5) + vector (sqlite-vec) over facts."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from grimoire.continuity import (
    HybridFactSearchIndex,
    SqliteContinuityStore,
)
from grimoire.state_store.search import serialize_vector
from grimoire.storage import Database, apply_migrations
from tests.continuity.conftest import make_fact


class _StaticEmbedder:
    """Deterministic embedder for tests.

    Maps each known token to a fixed vector; unknown queries fall back to a
    zero-ish vector that scores poorly against everything.
    """

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def embed(self, task: str, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            lower = text.lower()
            chosen = None
            for token, vec in self._vectors.items():
                if token in lower:
                    chosen = vec
                    break
            out.append(chosen if chosen is not None else [0.01, 0.01, 0.01])
        return out


@pytest.fixture
async def db_with_facts(tmp_path: Path):
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    # FK targets
    for pid in ("post-1", "post-2", "post-3"):
        await db.execute(
            """
            INSERT INTO posts (
              id, scene_id, campaign_id, branch_id, order_in_scene, author_kind
            ) VALUES (?, NULL, ?, ?, 0, 'narrator')
            """,
            (pid, "c1", "c1:main"),
        )
    store = SqliteContinuityStore(db, campaign_id="c1", branch_id="c1:main")

    facts = [
        make_fact(
            fact_id="f_orchard",
            text="winifred promised julian she would take him to the orchard.",
            post="post-1",
            keywords=["winifred", "julian", "orchard"],
        ),
        make_fact(
            fact_id="f_storm",
            text="A violent storm rolled in over the harbor.",
            post="post-2",
            keywords=["storm", "harbor"],
        ),
        make_fact(
            fact_id="f_meeting",
            text="winifred and julian met by the riverbank.",
            post="post-3",
            keywords=["winifred", "julian", "riverbank"],
        ),
    ]
    for f in facts:
        await store.put_fact(f)

    # Insert matching embedding vectors for "winifred" → [1,0,0] facts.
    embeddings = {
        "f_orchard": [1.0, 0.0, 0.0],
        "f_storm": [0.0, 1.0, 0.0],
        "f_meeting": [0.9, 0.1, 0.0],
    }
    for fact_id, vec in embeddings.items():
        await db.execute(
            """
            INSERT INTO embeddings (
              id, scope, ref, source_kind, text, vector, embedded_at, model, campaign_id
            )
            VALUES (?, 'campaign', ?, 'fact', ?, ?, '2025-01-01', 'test', 'c1')
            """,
            (
                f"emb_{fact_id}",
                fact_id,
                "text",
                serialize_vector(vec),
            ),
        )

    try:
        yield store, db
    finally:
        await db.close()


async def test_keyword_only_returns_matching_facts(db_with_facts) -> None:
    store, db = db_with_facts
    index = HybridFactSearchIndex(store, db, campaign_id="c1", branch_id="c1:main")
    results = await index.search("orchard", top_k=3)
    assert results
    ids = [f.id for f, _ in results]
    assert "f_orchard" in ids


async def test_hybrid_search_combines_vector_and_keyword(db_with_facts) -> None:
    store, db = db_with_facts
    embedder = _StaticEmbedder({"winifred": [1.0, 0.0, 0.0]})
    index = HybridFactSearchIndex(
        store, db, campaign_id="c1", branch_id="c1:main", embedder=embedder
    )
    results = await index.search("winifred", top_k=2)
    ids = [f.id for f, _ in results]
    # Vector pass should rank winifred-related facts at the top.
    assert set(ids) <= {"f_orchard", "f_meeting"}
    assert ids[0] in {"f_orchard", "f_meeting"}


async def test_vector_degrades_gracefully_when_embedder_unavailable(db_with_facts) -> None:
    store, db = db_with_facts
    index = HybridFactSearchIndex(store, db, campaign_id="c1", branch_id="c1:main")
    results = await index.search("nonsense_token_xyz", top_k=3)
    assert results == []


async def test_search_skips_retired_by_default(db_with_facts) -> None:
    store, db = db_with_facts
    fact = await store.get_fact("f_orchard")
    fact.retired = True
    await store.put_fact(fact)
    index = HybridFactSearchIndex(store, db, campaign_id="c1", branch_id="c1:main")
    results = await index.search("orchard", top_k=3)
    assert all(f.id != "f_orchard" for f, _ in results)
    results_incl = await index.search("orchard", top_k=3, include_retired=True)
    assert any(f.id == "f_orchard" for f, _ in results_incl)


async def test_vector_search_returns_within_top_k(db_with_facts) -> None:
    store, db = db_with_facts
    embedder = _StaticEmbedder({"winifred": [1.0, 0.0, 0.0]})
    index = HybridFactSearchIndex(
        store, db, campaign_id="c1", branch_id="c1:main", embedder=embedder
    )
    results = await index.search("winifred", top_k=2)
    assert len(results) <= 2
    for _, score in results:
        assert score > 0.0 and not math.isnan(score)
