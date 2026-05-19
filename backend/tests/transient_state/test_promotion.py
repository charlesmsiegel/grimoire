"""promote_to_fact: write through ContinuityService.add_fact + supersede."""

from __future__ import annotations

import pytest

from grimoire.continuity.service import ContinuityService
from grimoire.transient_state import TransientStateService
from grimoire.types.transient import EntityKind, Provenance


@pytest.fixture
def continuity() -> ContinuityService:
    return ContinuityService(campaign_id="c_test", branch_id="c_test:main")


async def test_promote_creates_fact_and_supersedes_transient(
    service: TransientStateService,
    seeded_campaign: str,
    continuity: ContinuityService,
):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "haunted",
        provenance=Provenance.EXTRACTOR_AUTO,
        source_post_id="p_1",
        confidence=0.95,
    )
    fact_id, transient_id = await service.promote_to_fact(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        evidence="She kept watching the door.",
        turn_id="t_1",
        continuity=continuity,
    )
    assert fact_id
    assert transient_id > 0
    # Continuity has the fact
    fact = await continuity._store.get_fact(fact_id)
    assert fact is not None
    assert "haunted" in fact.text
    # Transient row is expired
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is None


async def test_promote_without_continuity_still_clears_transient(
    service: TransientStateService, seeded_campaign: str
):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "haunted",
        provenance=Provenance.EXTRACTOR_AUTO,
        source_post_id="p_1",
        confidence=0.95,
    )
    fact_id, transient_id = await service.promote_to_fact(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        evidence="...",
        turn_id="t_1",
    )
    assert fact_id == ""
    assert transient_id > 0
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x", "mood")
    assert v is None


async def test_promote_missing_value_raises(service: TransientStateService, seeded_campaign: str):
    with pytest.raises(ValueError, match="no current transient value"):
        await service.promote_to_fact(
            seeded_campaign,
            EntityKind.CHARACTER,
            "ghost",
            "mood",
            evidence="...",
            turn_id="t_1",
        )
