"""ContinuityService.retract_turn — used by cascade delete to undo a turn's
fact/commitment writes (which bypass the reversible delta log)."""

from __future__ import annotations

from grimoire.continuity import Commitment, CommitmentKind, ContinuityService, InGameTime
from grimoire.continuity.types import CommitmentStatus

from .conftest import make_fact


async def test_retract_turn_retires_facts_and_removes_commitments():
    service = ContinuityService()
    await service.add_fact(make_fact(text="from T1", post="T1"), source="extractor")
    await service.add_fact(make_fact(text="from T2", post="T2"), source="extractor")
    await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="promised in T1",
            created_in_post="T1",
            in_game_created_at=InGameTime(day_count=1),
            from_id="winifred",
            to_id="julian",
        ),
        source="extractor",
    )

    result = await service.retract_turn("T1")

    # The T1 fact is retired; the T2 fact survives.
    active = await service.recent_facts(InGameTime(day_count=0), limit=50)
    texts = {f.text for f in active}
    assert "from T1" not in texts
    assert "from T2" in texts
    assert len(result["retired_facts"]) == 1

    # The T1 commitment is gone.
    assert await service.all_commitments() == []
    assert len(result["removed_commitments"]) == 1


async def test_retract_turn_noop_when_nothing_matches():
    service = ContinuityService()
    await service.add_fact(make_fact(text="from T2", post="T2"), source="extractor")
    result = await service.retract_turn("T1")
    assert result == {
        "retired_facts": [],
        "removed_commitments": [],
        "unretired_facts": [],
        "reopened_commitments": [],
        "removed_knowledge": [],
    }
    active = await service.recent_facts(InGameTime(day_count=0), limit=50)
    assert {f.text for f in active} == {"from T2"}


async def test_retract_turn_unretires_fact_retired_in_the_turn():
    """A FACT_RETIRE issued in the deleted turn is reversed: a fact established
    earlier and retired in T2 is active again after retracting T2."""
    service = ContinuityService()
    fid = await service.add_fact(make_fact(text="long-standing", post="T1"), source="extractor")
    await service.retire_fact(fid, in_post="T2", reason="retconned")
    assert fid not in {f.id for f in await service.recent_facts(InGameTime(day_count=0))}

    result = await service.retract_turn("T2")

    assert result["unretired_facts"] == [fid]
    active = {f.id for f in await service.recent_facts(InGameTime(day_count=0))}
    assert fid in active


async def test_retract_turn_reopens_commitment_resolved_in_the_turn():
    """A COMMITMENT_RESOLVE issued in the deleted turn is reversed: a commitment
    created earlier and paid in T2 is OPEN again after retracting T2."""
    service = ContinuityService()
    await service.add_commitment(
        Commitment(
            id="c1",
            kind=CommitmentKind.PROMISE,
            text="created earlier",
            created_in_post="T1",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="extractor",
    )
    await service.resolve_commitment("c1", CommitmentStatus.PAID, in_post="T2")
    assert (await service.get_commitment("c1")).status == CommitmentStatus.PAID

    result = await service.retract_turn("T2")

    assert result["reopened_commitments"] == ["c1"]
    reopened = await service.get_commitment("c1")
    assert reopened.status == CommitmentStatus.OPEN
    assert reopened.resolved_in_post is None


async def test_retract_turn_removes_knowledge_revealed_in_the_turn():
    """A KNOWLEDGE_REVEAL issued in the deleted turn is reversed."""
    service = ContinuityService()
    fid = await service.add_fact(make_fact(text="a secret", post="T1"), source="extractor")
    await service.reveal(fid, ["julian"], in_post="T2", source="extractor")
    assert await service.knows("julian", fid)

    result = await service.retract_turn("T2")

    assert result["removed_knowledge"] == [f"julian:{fid}"]
    assert not await service.knows("julian", fid)
