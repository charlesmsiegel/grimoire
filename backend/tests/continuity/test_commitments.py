"""Commitment ledger and resolution tests."""

from __future__ import annotations

import pytest

from grimoire.continuity import (
    Commitment,
    CommitmentKind,
    CommitmentNotFoundError,
    CommitmentStatus,
    InGameTime,
)


def _make(
    *,
    cid: str = "",
    kind: CommitmentKind = CommitmentKind.PROMISE,
    text: str = "winifred will take julian to the orchard.",
    post: str = "p1",
    day: int = 1,
    from_id: str | None = "winifred",
    to_id: str | None = "julian",
    due: int | None = None,
    weight: int = 3,
) -> Commitment:
    return Commitment(
        id=cid,
        kind=kind,
        text=text,
        created_in_post=post,
        in_game_created_at=InGameTime(day_count=day),
        from_id=from_id,
        to_id=to_id,
        due_by=InGameTime(day_count=due) if due is not None else None,
        weight=weight,
    )


async def test_add_commitment_assigns_id_and_default_activity(service):
    cid = await service.add_commitment(_make(), source="extractor")
    c = await service.get_commitment(cid)
    assert cid
    assert c.last_activity_at == c.in_game_created_at
    assert any(t.startswith("src:extractor") for t in c.tags)


async def test_resolve_commitment_to_paid(service):
    cid = await service.add_commitment(_make(), source="x")
    await service.resolve_commitment(cid, CommitmentStatus.PAID, in_post="p9")
    c = await service.get_commitment(cid)
    assert c.status == CommitmentStatus.PAID
    assert c.resolved_in_post == "p9"


async def test_resolve_unknown_raises(service):
    with pytest.raises(CommitmentNotFoundError):
        await service.resolve_commitment("missing", CommitmentStatus.PAID, in_post="p")


async def test_cannot_resolve_back_to_open(service):
    cid = await service.add_commitment(_make(), source="x")
    with pytest.raises(ValueError):
        await service.resolve_commitment(cid, CommitmentStatus.OPEN, in_post="p")


async def test_open_commitments_filters_by_involvement(service):
    a = await service.add_commitment(_make(from_id="winifred", to_id="julian"), source="x")
    b = await service.add_commitment(_make(from_id="mira", to_id="kell", text="other"), source="x")
    only_florence = await service.open_commitments(involving=["winifred"])
    ids = [c.id for c in only_florence]
    assert a in ids
    assert b not in ids


async def test_open_commitments_excludes_terminal(service):
    a = await service.add_commitment(_make(text="A"), source="x")
    b = await service.add_commitment(_make(text="B"), source="x")
    await service.resolve_commitment(a, CommitmentStatus.PAID, in_post="p")
    await service.resolve_commitment(b, CommitmentStatus.BROKEN, in_post="p")
    rows = await service.open_commitments()
    assert rows == []


async def test_open_commitments_includes_overdue(service):
    cid = await service.add_commitment(_make(text="due", due=10, weight=4), source="x")
    await service.age(InGameTime(day_count=20))
    rows = await service.open_commitments()
    assert any(c.id == cid for c in rows)


async def test_open_commitments_orders_by_weight_then_due(service):
    a = await service.add_commitment(_make(text="A", weight=1, day=1), source="x")
    b = await service.add_commitment(_make(text="B", weight=5, day=1), source="x")
    c = await service.add_commitment(_make(text="C", weight=3, day=1), source="x")
    rows = await service.open_commitments()
    assert [r.id for r in rows] == [b, c, a]


async def test_overdue_commitments_returns_passed_due(service):
    overdue_id = await service.add_commitment(_make(text="overdue", due=5), source="x")
    future_id = await service.add_commitment(_make(text="future", due=100), source="x")
    rows = await service.overdue_commitments(as_of=InGameTime(day_count=50))
    ids = [c.id for c in rows]
    assert overdue_id in ids
    assert future_id not in ids


async def test_overdue_ignores_terminal_and_stale(service):
    paid = await service.add_commitment(_make(text="paid", due=5), source="x")
    await service.resolve_commitment(paid, CommitmentStatus.PAID, in_post="p")
    stale = await service.add_commitment(_make(text="stale", due=5), source="x")
    await service.resolve_commitment(stale, CommitmentStatus.STALE, in_post="p")
    rows = await service.overdue_commitments(as_of=InGameTime(day_count=100))
    ids = {c.id for c in rows}
    assert paid not in ids
    assert stale not in ids


async def test_stale_commitments_returns_stale(service):
    a = await service.add_commitment(_make(text="lingering"), source="x")
    # No due_by; advance past the stale threshold (default 6mo = 180d).
    await service.age(InGameTime(day_count=400))
    rows = await service.stale_commitments(threshold=service._config.commitment_stale_threshold)
    ids = [c.id for c in rows]
    assert a in ids
