"""SqliteContinuityStore — bind Continuity reads/writes to State Store tables."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.continuity import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    ContinuityService,
    ContradictionCandidate,
    ContradictionReport,
    ContradictionVerdict,
    InGameTime,
    KnowledgeEntry,
    RetirementReason,
    SqliteContinuityStore,
)
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from tests.continuity.conftest import make_fact


@pytest.fixture
async def sqlite_store(tmp_path: Path):
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    # Insert posts referenced by the test facts so the FK constraint passes.
    for pid in ("post-1", "post-2", "post-3", "post-9", "p2"):
        await db.execute(
            """
            INSERT INTO posts (
              id, scene_id, campaign_id, order_in_scene, author_kind
            ) VALUES (?, NULL, ?, 0, 'narrator')
            """,
            (pid, "c1"),
        )
    store = SqliteContinuityStore(db, campaign_id="c1")
    try:
        yield store, db
    finally:
        await db.close()


async def test_put_and_get_fact_round_trips(sqlite_store) -> None:
    store, _db = sqlite_store
    fact = make_fact(
        fact_id="fact_a",
        text="winifred promised julian an orchard visit.",
        post="post-1",
        day=7,
        characters=["winifred", "julian"],
        keywords=["winifred", "julian", "orchard"],
        tags=["public"],
    )
    await store.put_fact(fact)
    got = await store.get_fact("fact_a")
    assert got is not None
    assert got.id == "fact_a"
    assert got.text == fact.text
    assert got.established_at_in_game == InGameTime(day_count=7)
    assert sorted(got.about.character_ids) == ["julian", "winifred"]
    assert "public" in got.tags
    assert got.retired is False


async def test_list_facts_excludes_retired_by_default(sqlite_store) -> None:
    store, _db = sqlite_store
    await store.put_fact(make_fact(fact_id="a", text="alpha"))
    retired = make_fact(fact_id="b", text="beta")
    retired.retired = True
    retired.retired_in_post = "p2"
    retired.retired_reason = RetirementReason.SUPERSEDED
    await store.put_fact(retired)

    active = await store.list_facts()
    assert {f.id for f in active} == {"a"}

    all_rows = await store.list_facts(include_retired=True)
    assert {f.id for f in all_rows} == {"a", "b"}
    [restored] = [f for f in all_rows if f.id == "b"]
    assert restored.retired_reason is RetirementReason.SUPERSEDED


async def test_campaign_isolation(sqlite_store) -> None:
    store, db = sqlite_store
    # Seed FK targets for the second campaign
    for pid in ("p-other-1",):
        await db.execute(
            """
            INSERT INTO posts (
              id, scene_id, campaign_id, order_in_scene, author_kind
            ) VALUES (?, NULL, ?, 0, 'narrator')
            """,
            (pid, "c2"),
        )
    other = SqliteContinuityStore(db, campaign_id="c2")
    await store.put_fact(make_fact(fact_id="main_only", text="lives on c1"))
    await other.put_fact(make_fact(fact_id="other_only", text="lives on c2"))

    c1_ids = {f.id for f in await store.list_facts()}
    c2_ids = {f.id for f in await other.list_facts()}
    assert c1_ids == {"main_only"}
    assert c2_ids == {"other_only"}


async def test_commitment_round_trip_with_status_filter(sqlite_store) -> None:
    store, _db = sqlite_store
    open_c = Commitment(
        id="com_a",
        kind=CommitmentKind.PROMISE,
        text="Take julian to the orchard",
        created_in_post="post-1",
        in_game_created_at=InGameTime(day_count=10),
        from_id="winifred",
        to_id="julian",
        weight=4,
        due_by=InGameTime(day_count=20),
    )
    await store.put_commitment(open_c)
    paid_c = Commitment(
        id="com_b",
        kind=CommitmentKind.OBLIGATION,
        text="Settle the bill",
        created_in_post="post-2",
        in_game_created_at=InGameTime(day_count=5),
        status=CommitmentStatus.PAID,
        resolved_in_post="post-9",
    )
    await store.put_commitment(paid_c)

    open_rows = await store.list_commitments(statuses=[CommitmentStatus.OPEN])
    assert {c.id for c in open_rows} == {"com_a"}
    paid_rows = await store.list_commitments(statuses=[CommitmentStatus.PAID])
    assert {c.id for c in paid_rows} == {"com_b"}
    all_rows = await store.list_commitments()
    assert {c.id for c in all_rows} == {"com_a", "com_b"}

    fetched = await store.get_commitment("com_a")
    assert fetched is not None
    assert fetched.weight == 4
    assert fetched.due_by == InGameTime(day_count=20)


async def test_knowledge_state_round_trip(sqlite_store) -> None:
    store, _db = sqlite_store
    await store.put_fact(make_fact(fact_id="f1", text="A secret"))
    await store.put_knowledge(
        KnowledgeEntry(
            fact_id="f1",
            character_id="julian",
            knows=True,
            learned_in_post="post-3",
            source="witnessed",
        )
    )
    entry = await store.get_knowledge("julian", "f1")
    assert entry is not None and entry.knows is True
    assert entry.source == "witnessed"

    by_char = await store.knowledge_for_character("julian")
    assert [e.fact_id for e in by_char] == ["f1"]


async def test_contradiction_report_round_trip(sqlite_store) -> None:
    store, _db = sqlite_store
    candidate = make_fact(fact_id="cand", text="winifred visited Sion as a child.")
    existing = make_fact(fact_id="existing", text="winifred has never left Greenwich.")
    report = ContradictionReport(
        id="rep_1",
        candidate_fact=candidate,
        conflicts=[
            ContradictionCandidate(
                existing_fact=existing,
                similarity=0.8,
                verdict=ContradictionVerdict.CONFLICT,
                confidence=0.9,
                rationale="locations disagree",
            )
        ],
    )
    await store.put_contradiction_report(report)
    got = await store.get_contradiction_report("rep_1")
    assert got is not None
    assert got.candidate_fact.text == candidate.text
    assert len(got.conflicts) == 1
    assert got.conflicts[0].existing_fact.id == "existing"
    assert got.conflicts[0].verdict is ContradictionVerdict.CONFLICT


async def test_service_works_against_sqlite_store(sqlite_store) -> None:
    """End-to-end: ContinuityService backed by the SQLite store."""
    store, _db = sqlite_store
    service = ContinuityService(store=store)

    fact = make_fact(text="julian carries the lantern.", characters=["julian"])
    fid = await service.add_fact(fact, source="extractor")

    retrieved = await service.get_fact(fid)
    assert retrieved.text == "julian carries the lantern."

    facts = await service.facts_about(character_ids=["julian"])
    assert any(f.id == fid for f in facts)
