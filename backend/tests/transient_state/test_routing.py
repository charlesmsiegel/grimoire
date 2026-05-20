"""Routing rules for extractor TransientUpdateProposal candidates."""

from __future__ import annotations

from grimoire.transient_state import (
    TransientStateService,
    route_transient_updates,
)
from grimoire.transient_state.config import TransientStateConfig
from grimoire.types.transient import (
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)


async def test_high_confidence_proposal_auto_applied(
    service: TransientStateService, seeded_campaign: str
):
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.92,
        evidence="She tensed at the question.",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
    )
    assert summary.auto_applied == 1
    assert summary.enqueued_for_review == 0
    assert summary.discarded == 0
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is not None
    assert v.value == "guarded"
    assert v.provenance == Provenance.EXTRACTOR_AUTO


async def test_medium_confidence_enqueued_for_review(
    service: TransientStateService, seeded_campaign: str
):
    enqueued: list[tuple[str, str]] = []

    async def enqueue(proposal: TransientUpdateProposal, campaign_id: str) -> str:
        enqueued.append((proposal.entity_id, proposal.field))
        return "r_1"

    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="conflicted",
        confidence=0.65,
        evidence="A flicker of doubt.",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
        review_enqueuer=enqueue,
    )
    assert summary.auto_applied == 0
    assert summary.enqueued_for_review == 1
    assert enqueued == [("char_x", "mood")]
    # Nothing written to transient table
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is None


async def test_low_confidence_discarded(service: TransientStateService, seeded_campaign: str):
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="weird",
        confidence=0.4,
        evidence="vibes",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
    )
    assert summary.discarded == 1
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is None


async def test_routing_uses_config_overrides(service: TransientStateService, seeded_campaign: str):
    cfg = TransientStateConfig(auto_apply_threshold=0.5, review_threshold=0.3)
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="curious",
        confidence=0.55,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
        config=cfg,
    )
    assert summary.auto_applied == 1


async def test_routing_handles_mixed_batch(service: TransientStateService, seeded_campaign: str):
    proposals = [
        TransientUpdateProposal(
            entity_kind=EntityKind.CHARACTER,
            entity_id="a",
            field="mood",
            value="x",
            confidence=0.95,
            evidence="...",
        ),
        TransientUpdateProposal(
            entity_kind=EntityKind.CHARACTER,
            entity_id="b",
            field="mood",
            value="y",
            confidence=0.7,
            evidence="...",
        ),
        TransientUpdateProposal(
            entity_kind=EntityKind.CHARACTER,
            entity_id="c",
            field="mood",
            value="z",
            confidence=0.4,
            evidence="...",
        ),
    ]
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=proposals,
        transient_state=service,
        source_post_id="p_1",
    )
    assert summary.auto_applied == 1
    assert summary.enqueued_for_review == 1
    assert summary.discarded == 1


async def test_routing_summary_carries_write_record(
    service: TransientStateService, seeded_campaign: str
):
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.92,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
    )
    assert len(summary.writes) == 1
    w = summary.writes[0]
    assert w["entity_kind"] == "character"
    assert w["entity_id"] == "char_x"
    assert w["field"] == "mood"
    assert w["provenance"] == "extractor:auto"
    assert w["confidence"] == 0.92
    assert isinstance(w["new_value_id"], int)
    assert summary.conflicts == []


async def test_routing_summary_surfaces_conflict_when_user_outranks(
    service: TransientStateService, seeded_campaign: str
):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "calm",
        provenance=Provenance.USER_EDIT,
    )
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="angry",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_2",
    )
    assert summary.auto_applied == 1
    assert len(summary.conflicts) == 1
    c = summary.conflicts[0]
    assert c["field"] == "mood"
    assert c["entity_id"] == "char_x"
    current = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert current is not None
    assert current.value == "calm"
