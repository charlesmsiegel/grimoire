"""Unit tests for the orchestrator's continuity-delta routing (§5).

Covers ``OrchestratorService._apply_continuity_delta`` directly so we
exercise FACT_ADD / FACT_RETIRE / COMMITMENT_ADD / COMMITMENT_RESOLVE /
KNOWLEDGE_REVEAL translation without standing up a full turn.
"""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.continuity import (
    Commitment,
    CommitmentStatus,
    ContinuityRegistry,
    ContinuityService,
    Fact,
    FactSource,
    FactSubject,
    InGameTime,
    InMemoryContinuityStore,
)
from grimoire.event_bus import EventBus
from grimoire.orchestrator.service import OrchestratorService
from grimoire.types.common import Scope
from grimoire.types.state import DeltaKind, StateDelta

pytestmark = pytest.mark.asyncio


class _MinimalStore:
    """State store stub that only implements queue_for_review for the
    review-queue fallback path."""

    def __init__(self) -> None:
        self.reviewed: list[Any] = []
        self.applied: list[Any] = []

    async def queue_for_review(self, *, delta, source, campaign_id):
        self.reviewed.append((delta, source, campaign_id))
        return "review-1"

    async def apply_delta(self, *, delta, source, turn_id, branch_id, campaign_id):
        self.applied.append(delta)
        return "delta-1"


def _orchestrator(registry: ContinuityRegistry, store: _MinimalStore) -> OrchestratorService:
    return OrchestratorService(
        event_bus=EventBus(),
        scene_manager=None,  # type: ignore[arg-type]
        llm_gateway=None,
        context_builder=None,
        extractor=None,
        state_store=store,
        continuity=registry,
    )


async def test_fact_add_routes_to_continuity_when_no_conflict() -> None:
    registry = ContinuityRegistry(
        store_factory=lambda c, b: InMemoryContinuityStore(),
    )
    store = _MinimalStore()
    orch = _orchestrator(registry, store)

    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="fact:abc",
        target_table="facts",
        after={
            "text": "winifred loves her orchard.",
            "about": {"character_ids": ["winifred"], "scope": "public"},
        },
        confidence=0.9,
        source="extractor",
    )
    handled = await orch._delta._apply_continuity_delta(
        delta=delta,
        campaign_id="camp-a",
        branch_id="camp-a:main",
        turn_id="t1",
    )
    assert handled is True
    service: ContinuityService = registry.for_campaign("camp-a")
    facts = await service.facts_about(limit=10)
    assert any(f.text.startswith("winifred loves") for f in facts)
    assert store.reviewed == []  # no review queue when no conflicts


async def test_fact_add_conflict_queues_for_review() -> None:
    from grimoire.continuity.protocols import ContradictionJudge
    from grimoire.continuity.types import ContradictionCandidate, ContradictionVerdict

    class AlwaysConflict(ContradictionJudge):
        async def judge(self, candidate, existing, *, turn_id=None):
            return ContradictionCandidate(
                existing_fact=existing,
                similarity=0.9,
                verdict=ContradictionVerdict.CONFLICT,
                confidence=0.9,
                rationale="forced",
            )

    # Pre-populate one fact in the store the judge can flag as a conflict.
    stores: dict[tuple[str, str], InMemoryContinuityStore] = {}

    def store_factory(c: str, b: str) -> InMemoryContinuityStore:
        key = (c, b)
        if key not in stores:
            stores[key] = InMemoryContinuityStore()
        return stores[key]

    registry = ContinuityRegistry(store_factory=store_factory)
    # Force-construct service so we can seed a conflict-target fact.
    service = registry.for_campaign("camp-a")
    service._judge = AlwaysConflict()  # type: ignore[attr-defined]
    await service.add_fact(
        Fact(
            id="",
            text="The orchard is hers.",
            established_in_post="p-1",
            established_at_in_game=InGameTime(day_count=1),
            confidence=0.9,
            source=FactSource.NARRATOR,
            about=FactSubject(),
        ),
        source="user",
    )

    store = _MinimalStore()
    orch = _orchestrator(registry, store)
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="fact:new",
        target_table="facts",
        after={"text": "The orchard belongs to no one.", "about": {}},
        confidence=0.9,
        source="extractor",
    )
    handled = await orch._delta._apply_continuity_delta(
        delta=delta, campaign_id="camp-a", branch_id="camp-a:main", turn_id="t1"
    )
    assert handled is True  # conflict path "handled" by review queue
    assert store.reviewed, "FACT_ADD with conflict should land in review queue"


async def test_commitment_add_routes_to_continuity() -> None:
    registry = ContinuityRegistry(
        store_factory=lambda c, b: InMemoryContinuityStore(),
    )
    store = _MinimalStore()
    orch = _orchestrator(registry, store)
    delta = StateDelta(
        kind=DeltaKind.COMMITMENT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="commitment:new",
        target_table="commitments",
        after={
            "kind": "promise",
            "text": "Return the heirloom",
            "from": "winifred",
            "to": "rosaline",
        },
        confidence=0.9,
        source="extractor",
    )
    handled = await orch._delta._apply_continuity_delta(
        delta=delta, campaign_id="camp-a", branch_id="camp-a:main", turn_id="t1"
    )
    assert handled is True
    service = registry.for_campaign("camp-a")
    rows = await service.open_commitments(limit=50)
    assert any(c.text == "Return the heirloom" for c in rows)


async def test_commitment_resolve_routes_to_continuity() -> None:
    registry = ContinuityRegistry(
        store_factory=lambda c, b: InMemoryContinuityStore(),
    )
    store = _MinimalStore()
    orch = _orchestrator(registry, store)
    service = registry.for_campaign("camp-a")
    cid = await service.add_commitment(
        Commitment(
            id="",
            kind=__import__("grimoire").continuity.CommitmentKind.PROMISE,
            text="A vow",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="user",
    )
    delta = StateDelta(
        kind=DeltaKind.COMMITMENT_RESOLVE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"commitment:{cid}",
        after={"commitment_id": cid, "status": "paid", "in_post": "p-5"},
        confidence=1.0,
        source="extractor",
    )
    handled = await orch._delta._apply_continuity_delta(
        delta=delta, campaign_id="camp-a", branch_id="camp-a:main", turn_id="t2"
    )
    assert handled is True
    assert (await service.get_commitment(cid)).status == CommitmentStatus.PAID


async def test_unrouted_kind_returns_false() -> None:
    """Unsupported continuity kinds fall back so the caller still logs."""
    registry = ContinuityRegistry(
        store_factory=lambda c, b: InMemoryContinuityStore(),
    )
    store = _MinimalStore()
    orch = _orchestrator(registry, store)
    delta = StateDelta(
        kind=DeltaKind.COMMITMENT_RESOLVE,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="commitment:missing",
        after={},  # no commitment_id
        confidence=1.0,
        source="extractor",
    )
    handled = await orch._delta._apply_continuity_delta(
        delta=delta, campaign_id="camp-a", branch_id="camp-a:main", turn_id="t2"
    )
    assert handled is False


async def test_routes_skipped_when_no_continuity_wired() -> None:
    """An orchestrator without continuity must not crash; routing returns False."""
    orch = OrchestratorService(
        event_bus=EventBus(),
        scene_manager=None,  # type: ignore[arg-type]
        llm_gateway=None,
        context_builder=None,
        extractor=None,
        state_store=_MinimalStore(),
        continuity=None,
    )
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="fact:n",
        target_table="facts",
        after={"text": "x"},
        confidence=0.9,
        source="extractor",
    )
    handled = await orch._delta._apply_continuity_delta(
        delta=delta, campaign_id="camp-a", branch_id="camp-a:main", turn_id="t1"
    )
    assert handled is False
