"""Event-bus emissions from ContinuityService (§4)."""

from __future__ import annotations

import pytest

from grimoire.continuity import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    ContinuityService,
    Duration,
    InGameTime,
)
from grimoire.event_bus import Event, EventBus
from tests.continuity.conftest import make_fact

pytestmark = pytest.mark.asyncio


def _collect_events(bus: EventBus) -> tuple[list[Event], object]:
    captured: list[Event] = []

    async def handler(event: Event) -> None:
        captured.append(event)

    sub = bus.subscribe("*", handler)
    return captured, sub


async def test_add_fact_emits_fact_recorded() -> None:
    bus = EventBus()
    captured, _ = _collect_events(bus)
    service = ContinuityService(
        event_bus=bus, campaign_id="camp-1", branch_id="camp-1:main"
    )
    fid = await service.add_fact(make_fact(text="A new fact"), source="extractor")
    types = [e.type for e in captured]
    assert "fact_recorded" in types
    payload = next(e.payload for e in captured if e.type == "fact_recorded")
    assert payload["fact_id"] == fid
    assert payload["campaign_id"] == "camp-1"
    assert payload["source"] == "extractor"


async def test_resolve_commitment_emits_paid_or_broken() -> None:
    bus = EventBus()
    captured, _ = _collect_events(bus)
    service = ContinuityService(event_bus=bus, campaign_id="camp-1")
    cid = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="Return the heirloom",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="extractor",
    )
    await service.resolve_commitment(cid, CommitmentStatus.PAID, "p-5")
    paid = [e for e in captured if e.type == "commitment_paid_off"]
    assert len(paid) == 1
    assert paid[0].payload["commitment_id"] == cid
    assert paid[0].payload["in_post"] == "p-5"

    cid2 = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="Second promise",
            created_in_post="p-2",
            in_game_created_at=InGameTime(day_count=2),
        ),
        source="extractor",
    )
    await service.resolve_commitment(cid2, CommitmentStatus.BROKEN, "p-6")
    broken = [e for e in captured if e.type == "commitment_broken"]
    assert len(broken) == 1
    assert broken[0].payload["commitment_id"] == cid2


async def test_age_emits_overdue_and_stale() -> None:
    bus = EventBus()
    captured, _ = _collect_events(bus)
    service = ContinuityService(event_bus=bus, campaign_id="camp-1")
    # Commitment with a due date in the past -> OVERDUE
    cid_over = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="Pay debt",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
            due_by=InGameTime(day_count=10),
        ),
        source="extractor",
    )
    # Stale commitment: no due date, inactivity > threshold
    cid_stale = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.MYSTERY,
            text="Find the witness",
            created_in_post="p-2",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="extractor",
    )
    await service.age(InGameTime(day_count=200))
    overdue = [e for e in captured if e.type == "commitment_overdue"]
    stale = [e for e in captured if e.type == "commitment_stale"]
    assert any(e.payload["commitment_id"] == cid_over for e in overdue)
    assert any(e.payload["commitment_id"] == cid_stale for e in stale)


async def test_check_contradictions_emits_on_conflict() -> None:
    from grimoire.continuity.protocols import ContradictionJudge
    from grimoire.continuity.types import ContradictionCandidate, ContradictionVerdict

    bus = EventBus()
    captured, _ = _collect_events(bus)

    class ConflictJudge(ContradictionJudge):
        async def judge(self, candidate, existing, *, turn_id=None):
            return ContradictionCandidate(
                existing_fact=existing,
                similarity=0.9,
                verdict=ContradictionVerdict.CONFLICT,
                confidence=0.9,
                rationale="forced conflict",
            )

    service = ContinuityService(
        judge=ConflictJudge(), event_bus=bus, campaign_id="camp-1"
    )
    existing = make_fact(text="The orchard is in winifred's hand.")
    await service.add_fact(existing, source="extractor")
    report = await service.check_contradictions(
        make_fact(text="The orchard belongs to no one.")
    )
    assert report.conflicts
    detected = [e for e in captured if e.type == "contradiction_detected"]
    assert detected, captured
    assert detected[0].payload["report_id"] == report.id
    assert detected[0].payload["conflict_count"] == len(report.conflicts)


async def test_emit_swallows_handler_exceptions() -> None:
    """A buggy subscriber must not block the write path."""
    bus = EventBus()

    async def boom(event: Event) -> None:
        raise RuntimeError("nope")

    bus.subscribe("fact_recorded", boom)
    service = ContinuityService(event_bus=bus, campaign_id="camp-1")
    # Should not raise even though the handler does.
    fid = await service.add_fact(make_fact(text="ok"), source="extractor")
    assert fid


_ = Duration  # silence unused-import warning when run in isolation
