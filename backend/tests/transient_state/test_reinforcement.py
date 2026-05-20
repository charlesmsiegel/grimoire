"""Reinforcement detection + Continuity promotion (spec §Promotion to facts)."""

from __future__ import annotations

from typing import Any

from grimoire.transient_state import (
    TransientStateService,
    route_transient_updates,
)
from grimoire.transient_state.config import (
    PromoteToFactConfig,
    TransientStateConfig,
)
from grimoire.types.transient import (
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)


class _FakeContinuity:
    def __init__(self) -> None:
        self.added_facts: list[Any] = []

    async def add_fact(self, fact: Any, *, source: str) -> str:
        self.added_facts.append((fact, source))
        return f"fact_{len(self.added_facts)}"


async def _seed_history(
    service: TransientStateService,
    campaign_id: str,
    value: str,
    posts: list[str],
) -> None:
    for post_id in posts:
        await service.set(
            campaign_id,
            EntityKind.CHARACTER,
            "char_x",
            "mood",
            value,
            provenance=Provenance.EXTRACTOR_AUTO,
            confidence=0.95,
            source_post_id=post_id,
        )


async def test_reinforcement_triggers_promote_to_fact(
    service: TransientStateService, seeded_campaign: str
):
    cfg = TransientStateConfig(
        promote_to_fact=PromoteToFactConfig(reinforcement_count=3),
    )
    continuity = _FakeContinuity()
    await _seed_history(service, seeded_campaign, "guarded", ["p1", "p2"])
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p3",
        config=cfg,
        continuity=continuity,
    )
    assert summary.auto_applied == 1
    assert summary.promoted_to_fact == 1
    assert len(continuity.added_facts) == 1
    fact, source = continuity.added_facts[0]
    assert "guarded" in fact.text
    assert source == "transient_state:reinforced"


async def test_no_promotion_when_values_diverge(
    service: TransientStateService, seeded_campaign: str
):
    cfg = TransientStateConfig(
        promote_to_fact=PromoteToFactConfig(reinforcement_count=3),
    )
    continuity = _FakeContinuity()
    await _seed_history(service, seeded_campaign, "guarded", ["p1"])
    await _seed_history(service, seeded_campaign, "curious", ["p2"])
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p3",
        config=cfg,
        continuity=continuity,
    )
    assert summary.promoted_to_fact == 0
    assert continuity.added_facts == []


async def test_promotion_skipped_when_no_continuity_wired(
    service: TransientStateService, seeded_campaign: str
):
    cfg = TransientStateConfig(
        promote_to_fact=PromoteToFactConfig(reinforcement_count=2),
    )
    await _seed_history(service, seeded_campaign, "guarded", ["p1"])
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p2",
        config=cfg,
        continuity=None,
    )
    assert summary.auto_applied == 1
    assert summary.promoted_to_fact == 0
