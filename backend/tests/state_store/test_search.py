"""Vector + keyword search."""

from __future__ import annotations

import math

from grimoire.state_store import StateStore


async def _seed(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Test")


async def test_vector_search_returns_nearest_neighbors(store: StateStore) -> None:
    await _seed(store)
    # Three orthogonal-ish 3D vectors so distances are unambiguous.
    pairs = [
        ("post-1", [1.0, 0.0, 0.0], "the cat sat on the mat"),
        ("post-2", [0.0, 1.0, 0.0], "the dog barked"),
        ("post-3", [0.0, 0.0, 1.0], "an unrelated sentence"),
    ]
    for ref, vec, text in pairs:
        await store.add_embedding(
            ref=ref,
            scope="campaign",
            source_kind="post",
            text=text,
            vector=vec,
            model="test",
            campaign_id="c1",
        )

    hits = await store.vector_search(
        query_vector=[0.9, 0.1, 0.0],
        campaign_id="c1",
        include_library=False,
        top_k=3,
    )
    assert hits[0].ref == "post-1"
    # Score is similarity (1 - cosine distance): closer ≈ higher.
    assert hits[0].score > hits[1].score


async def test_vector_search_includes_library_when_requested(store: StateStore) -> None:
    await _seed(store)
    await store.add_embedding(
        ref="lib:winifred",
        scope="library",
        source_kind="character",
        text="character profile",
        vector=[1.0, 0.0, 0.0],
        model="test",
        campaign_id=None,
    )
    hits_with = await store.vector_search(
        query_vector=[1.0, 0.0, 0.0], campaign_id="c1", include_library=True
    )
    refs_with = [h.ref for h in hits_with]
    assert "lib:winifred" in refs_with

    hits_without = await store.vector_search(
        query_vector=[1.0, 0.0, 0.0], campaign_id="c1", include_library=False
    )
    assert all(h.ref != "lib:winifred" for h in hits_without)


async def test_keyword_search_over_facts(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta(
        delta={
            "kind": "fact_add",
            "target_scope": "campaign-sqlite",
            "target_table": "facts",
            "after": {
                "id": "f1",
                "campaign_id": "c1",
                "text": "winifred visited the Elysium on Tuesday evening.",
                "keywords": '["winifred", "elysium", "tuesday"]',
                "retired": 0,
            },
        },
        source="seed",
    )
    await store.apply_delta(
        delta={
            "kind": "fact_add",
            "target_scope": "campaign-sqlite",
            "target_table": "facts",
            "after": {
                "id": "f2",
                "campaign_id": "c1",
                "text": "Alistair lost his pocket watch at the docks.",
                "retired": 0,
            },
        },
        source="seed",
    )

    hits = await store.keyword_search(query="Elysium", campaign_id="c1")
    assert hits
    assert hits[0].ref == "f1"
    # f2 doesn't mention Elysium.
    assert all(h.ref != "f2" for h in hits)


async def test_keyword_search_tolerates_fts_operator_chars(store: StateStore) -> None:
    """The retrieval query carries entity refs (``worlds/w/characters/c``) and
    prose punctuation, which FTS5 would otherwise parse as operators."""
    await _seed(store)
    await store.apply_delta(
        delta={
            "kind": "fact_add",
            "target_scope": "campaign-sqlite",
            "target_table": "facts",
            "after": {
                "id": "f1",
                "campaign_id": "c1",
                "text": "winifred visited the Elysium on Tuesday evening.",
                "retired": 0,
            },
        },
        source="seed",
    )

    # A query as assembled by ArchiveRetriever.build_retrieval_query: free text
    # plus a slash-laden character ref and a trailing sentence with a period.
    query = "Where is winifred? worlds/city/characters/winifred-vespertine met her."
    hits = await store.keyword_search(query=query, campaign_id="c1")
    assert hits
    assert hits[0].ref == "f1"


async def test_keyword_search_empty_after_sanitisation(store: StateStore) -> None:
    """A query of only operator chars sanitises to nothing — return [], not error."""
    await _seed(store)
    hits = await store.keyword_search(query="/ . : - *", campaign_id="c1")
    assert hits == []


async def test_delete_embeddings(store: StateStore) -> None:
    await _seed(store)
    await store.add_embedding(
        ref="post-1",
        scope="campaign",
        source_kind="post",
        text="text",
        vector=[1.0, 0.0],
        model="test",
        campaign_id="c1",
    )
    removed = await store.delete_embeddings("post-1")
    assert removed == 1
    hits = await store.vector_search(
        query_vector=[1.0, 0.0], campaign_id="c1", include_library=False
    )
    assert all(h.ref != "post-1" for h in hits)


def test_serialize_round_trip() -> None:
    from grimoire.state_store.search import deserialize_vector, serialize_vector

    vec = [0.1, -0.2, 0.33, 1.0]
    out = deserialize_vector(serialize_vector(vec))
    assert len(out) == 4
    for a, b in zip(vec, out, strict=True):
        assert math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5)
