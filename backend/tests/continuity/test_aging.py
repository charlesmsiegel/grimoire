"""Aging tests: Time Engine calls `age()` and commitments transition."""

from __future__ import annotations

from grimoire.continuity import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    ContinuityConfig,
    ContinuityService,
    Duration,
    InGameTime,
)


def _commit(
    *,
    day: int,
    due: int | None = None,
    text: str = "x",
    last_activity: int | None = None,
) -> Commitment:
    return Commitment(
        id="",
        kind=CommitmentKind.PROMISE,
        text=text,
        created_in_post="p",
        in_game_created_at=InGameTime(day_count=day),
        due_by=InGameTime(day_count=due) if due is not None else None,
        last_activity_at=InGameTime(day_count=last_activity) if last_activity is not None else None,
    )


async def test_age_transitions_open_to_overdue(service):
    cid = await service.add_commitment(_commit(day=0, due=10), source="x")
    report = await service.age(InGameTime(day_count=20))
    after = await service.get_commitment(cid)
    assert after.status == CommitmentStatus.OVERDUE
    assert any(c.id == cid for c in report.became_overdue)


async def test_age_does_not_transition_within_due(service):
    cid = await service.add_commitment(_commit(day=0, due=100), source="x")
    await service.age(InGameTime(day_count=50))
    after = await service.get_commitment(cid)
    assert after.status == CommitmentStatus.OPEN


async def test_age_transitions_to_stale_after_threshold():
    svc = ContinuityService(
        config=ContinuityConfig(commitment_stale_threshold=Duration(days=30)),
    )
    cid = await svc.add_commitment(_commit(day=0, due=None), source="x")
    report = await svc.age(InGameTime(day_count=100))
    after = await svc.get_commitment(cid)
    assert after.status == CommitmentStatus.STALE
    assert any(c.id == cid for c in report.became_stale)


async def test_age_respects_last_activity_for_stale():
    svc = ContinuityService(
        config=ContinuityConfig(commitment_stale_threshold=Duration(days=30)),
    )
    cid = await svc.add_commitment(_commit(day=0, due=None, last_activity=80), source="x")
    await svc.age(InGameTime(day_count=100))
    after = await svc.get_commitment(cid)
    # Only 20 days since last activity — under the 30-day threshold.
    assert after.status == CommitmentStatus.OPEN


async def test_age_skips_terminal(service):
    cid = await service.add_commitment(_commit(day=0, due=10), source="x")
    await service.resolve_commitment(cid, CommitmentStatus.PAID, in_post="p")
    report = await service.age(InGameTime(day_count=999))
    assert report.became_overdue == []
    after = await service.get_commitment(cid)
    assert after.status == CommitmentStatus.PAID


async def test_age_with_no_commitments_is_noop(service):
    report = await service.age(InGameTime(day_count=10))
    assert report.became_overdue == []
    assert report.became_stale == []
