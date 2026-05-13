"""Tests for the audit store."""

from __future__ import annotations

from datetime import UTC, datetime

from grimoire.observability.audit import AuditStore
from grimoire.types.common import Scope
from grimoire.types.observability import (
    CompositionSnapshot,
    ContextSummary,
    TurnAudit,
)
from grimoire.types.state import AppliedDelta, ContextTier, DeltaKind, StateDelta


async def test_record_and_get_turn_audit(db) -> None:
    store = AuditStore(db)
    audit = TurnAudit(
        turn_id="t_001",
        campaign_id="c_one",
        branch_id="c_one:main",
        started_at=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        completed_at=datetime(2024, 1, 1, 12, 0, 5, tzinfo=UTC),
        duration_ms=5000,
        player_input="I open the door.",
        scene_id="s_001",
        composition_snapshot=CompositionSnapshot(mechanics_module="vampires"),
        context_summary=ContextSummary(total_tokens=1234, per_tier={ContextTier.SPOTLIGHT: 800}),
        context_messages_hash="abc123",
        llm_provider="anthropic",
        llm_model="claude-3-haiku",
        llm_prompt_tokens=900,
        llm_completion_tokens=200,
        llm_cost_usd=0.0042,
        llm_latency_ms=1200,
        llm_finish_reason="end_turn",
        llm_retries=0,
        response_text="The door creaks open.",
    )
    await store.record(audit)

    fetched = await store.get("t_001")
    assert fetched is not None
    assert fetched.turn_id == "t_001"
    assert fetched.campaign_id == "c_one"
    assert fetched.llm_provider == "anthropic"
    assert fetched.llm_model == "claude-3-haiku"
    assert fetched.response_text == "The door creaks open."
    assert fetched.context_messages_hash == "abc123"
    assert fetched.composition_snapshot is not None
    assert fetched.composition_snapshot.mechanics_module == "vampires"
    assert fetched.context_summary is not None
    assert fetched.context_summary.total_tokens == 1234
    assert fetched.duration_ms == 5000


async def test_record_is_upsert(db) -> None:
    store = AuditStore(db)
    audit = TurnAudit(
        turn_id="t_upsert",
        campaign_id="c",
        branch_id="b",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        response_text="first",
    )
    await store.record(audit)
    audit2 = audit.model_copy(update={"response_text": "second"})
    await store.record(audit2)
    fetched = await store.get("t_upsert")
    assert fetched is not None
    assert fetched.response_text == "second"


async def test_get_missing_returns_none(db) -> None:
    store = AuditStore(db)
    assert await store.get("does-not-exist") is None


async def test_record_serializes_applied_deltas(db) -> None:
    store = AuditStore(db)
    applied = AppliedDelta(
        id="ad_1",
        delta=StateDelta(
            kind=DeltaKind.FACT_ADD,
            target_scope=Scope.CAMPAIGN_SQLITE,
            target_id="f_1",
            source="extractor",
        ),
        campaign_id="c",
        branch_id="b",
        turn_id="t_applied",
        applied_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    audit = TurnAudit(
        turn_id="t_applied",
        campaign_id="c",
        branch_id="b",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        applied_deltas=[applied],
    )
    # Regression: previously raised AttributeError because the writer
    # dereferenced d.delta.id (no such attribute) instead of d.id.
    await store.record(audit)
    fetched = await store.get("t_applied")
    assert fetched is not None


async def test_list_orders_by_recency_and_filters_by_campaign(db) -> None:
    store = AuditStore(db)
    for idx in range(3):
        await store.record(
            TurnAudit(
                turn_id=f"t_{idx}",
                campaign_id="c_one",
                branch_id="c_one:main",
                started_at=datetime(2024, 1, idx + 1, tzinfo=UTC),
            )
        )
    await store.record(
        TurnAudit(
            turn_id="t_other",
            campaign_id="c_two",
            branch_id="c_two:main",
            started_at=datetime(2024, 1, 9, tzinfo=UTC),
        )
    )

    listed = await store.list("c_one", limit=10)
    assert [a.turn_id for a in listed] == ["t_2", "t_1", "t_0"]
    assert all(a.campaign_id == "c_one" for a in listed)

    limited = await store.list("c_one", limit=2)
    assert [a.turn_id for a in limited] == ["t_2", "t_1"]
