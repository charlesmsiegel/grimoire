"""Characterization tests for the store-level fact queries (#594).

`facts_about` / `recent_facts` / `facts_known_by` moved from a
fetch-all-then-filter shape in the service into query-level filtering in the
store. These tests pin the contract — ordering, tie-breaks, subject overlap,
knowledge-scope visibility, limits — and assert the SQLite store and the
in-memory reference store agree on every case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.continuity import (
    InGameTime,
    InMemoryContinuityStore,
    KnowledgeEntry,
    SqliteContinuityStore,
)
from grimoire.continuity.protocols import ContinuityStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from tests.continuity.conftest import make_fact

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sqlite_store(tmp_path: Path):
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    for pid in ("post-1", "post-2", "post-3"):
        await db.execute(
            "INSERT INTO posts (id, scene_id, campaign_id, order_in_scene, author_kind) "
            "VALUES (?, NULL, ?, 0, 'narrator')",
            (pid, "c1"),
        )
    store = SqliteContinuityStore(db, campaign_id="c1")
    try:
        yield store
    finally:
        await db.close()


@pytest.fixture
def memory_store() -> InMemoryContinuityStore:
    return InMemoryContinuityStore()


async def _seed(store: ContinuityStore) -> None:
    # Mixed days and labels to exercise the (day_count, label) ordering and the
    # rowid tie-break for facts sharing a timestamp. Insertion order matters for
    # ties, so seed in a fixed sequence.
    await store.put_fact(make_fact(fact_id="f_old", text="oldest", day=1, characters=["alice"]))
    await store.put_fact(
        make_fact(fact_id="f_tie_a", text="tie a", day=5, characters=["alice", "bob"])
    )
    await store.put_fact(make_fact(fact_id="f_tie_b", text="tie b", day=5, characters=["bob"]))
    await store.put_fact(make_fact(fact_id="f_new", text="newest", day=9, locations=["tower"]))
    await store.put_fact(
        make_fact(
            fact_id="f_label",
            text="same day, later label",
            day=5,
            characters=["alice"],
        )
    )


async def test_facts_about_no_filter_returns_all_newest_first(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        await _seed(store)
        rows = await store.facts_about()
        # day 9, then the three day-5 facts in insertion order (tie-break), then day 1.
        assert [f.id for f in rows] == [
            "f_new",
            "f_tie_a",
            "f_tie_b",
            "f_label",
            "f_old",
        ]


async def test_facts_about_subject_overlap(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        await _seed(store)
        alice = await store.facts_about(character_ids=["alice"])
        assert [f.id for f in alice] == ["f_tie_a", "f_label", "f_old"]
        # OR across attrs: characters=bob OR locations=tower.
        mixed = await store.facts_about(character_ids=["bob"], location_ids=["tower"])
        assert {f.id for f in mixed} == {"f_new", "f_tie_a", "f_tie_b"}
        # No overlap -> empty.
        assert await store.facts_about(character_ids=["nobody"]) == []


async def test_facts_about_limit_applies_after_sort(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        await _seed(store)
        rows = await store.facts_about(limit=2)
        assert [f.id for f in rows] == ["f_new", "f_tie_a"]


async def test_facts_about_excludes_retired_by_default(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        retired = make_fact(fact_id="r1", text="gone", day=10, characters=["alice"])
        retired.retired = True
        await store.put_fact(retired)
        await store.put_fact(make_fact(fact_id="live", text="here", day=2, characters=["alice"]))
        assert [f.id for f in await store.facts_about(character_ids=["alice"])] == ["live"]
        with_retired = await store.facts_about(character_ids=["alice"], include_retired=True)
        assert [f.id for f in with_retired] == ["r1", "live"]


async def test_recent_facts_filters_and_orders(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        await _seed(store)
        rows = await store.recent_facts(InGameTime(day_count=5))
        # day >= 5: all day-5 facts plus day-9; day-1 excluded.
        assert [f.id for f in rows] == ["f_new", "f_tie_a", "f_tie_b", "f_label"]
        assert await store.recent_facts(InGameTime(day_count=100)) == []


async def test_recent_facts_excludes_retired(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        retired = make_fact(fact_id="r1", text="gone", day=20)
        retired.retired = True
        await store.put_fact(retired)
        await store.put_fact(make_fact(fact_id="live", text="here", day=20))
        assert [f.id for f in await store.recent_facts(InGameTime(day_count=1))] == ["live"]


async def test_facts_known_by_visibility(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        await store.put_fact(make_fact(fact_id="public", text="sky is blue", day=1, scope="public"))
        await store.put_fact(
            make_fact(
                fact_id="about_alice",
                text="secret about alice",
                day=2,
                characters=["alice"],
                scope="private",
            )
        )
        await store.put_fact(
            make_fact(
                fact_id="about_bob",
                text="secret about bob",
                day=3,
                characters=["bob"],
                scope="private",
            )
        )
        # Alice sees the public fact and the one about herself, not Bob's.
        seen = await store.facts_known_by("alice")
        assert {f.id for f in seen} == {"public", "about_alice"}

        # Reveal Bob's secret to Alice -> now visible.
        await store.put_knowledge(
            KnowledgeEntry(
                fact_id="about_bob",
                character_id="alice",
                knows=True,
                learned_in_post="post-1",
                source="witnessed",
            )
        )
        seen = await store.facts_known_by("alice")
        assert {f.id for f in seen} == {"public", "about_alice", "about_bob"}
        # Newest first.
        assert [f.id for f in seen] == ["about_bob", "about_alice", "public"]


async def test_facts_known_by_knows_false_is_not_visible(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        await store.put_fact(
            make_fact(
                fact_id="hidden",
                text="a private matter",
                day=1,
                characters=["bob"],
                scope="private",
            )
        )
        # An explicit knows=False entry must not make the fact visible to alice.
        await store.put_knowledge(
            KnowledgeEntry(
                fact_id="hidden",
                character_id="alice",
                knows=False,
                learned_in_post="post-1",
                source="rumor",
            )
        )
        assert await store.facts_known_by("alice") == []


async def test_facts_known_by_limit(
    sqlite_store: SqliteContinuityStore, memory_store: InMemoryContinuityStore
) -> None:
    for store in (sqlite_store, memory_store):
        for i in range(5):
            await store.put_fact(
                make_fact(fact_id=f"p{i}", text=f"public {i}", day=i, scope="public")
            )
        rows = await store.facts_known_by("alice", limit=2)
        assert [f.id for f in rows] == ["p4", "p3"]
