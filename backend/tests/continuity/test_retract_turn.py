"""ContinuityService.retract_turn — used by cascade delete to undo a turn's
fact/commitment writes (which bypass the reversible delta log)."""

from __future__ import annotations

from grimoire.continuity import Commitment, CommitmentKind, ContinuityService, InGameTime

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
    assert result == {"retired_facts": [], "removed_commitments": []}
    active = await service.recent_facts(InGameTime(day_count=0), limit=50)
    assert {f.text for f in active} == {"from T2"}
