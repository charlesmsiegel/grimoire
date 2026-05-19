"""Extractor: narrative-extras proposal heuristic + service routing."""

from __future__ import annotations

import pytest

from grimoire.extractor.heuristics import find_extras_proposals, run_heuristics
from grimoire.extractor.service import ExtractorService
from grimoire.extractor.config import ExtractorConfig
from grimoire.types.common import EntityKind
from grimoire.types.extras import ExtraScope


def test_repeated_smoking_pattern_proposes_extras():
    text = (
        "winifred lit a Sobranie and watched the rain. "
        "Later, winifred smoked another Sobranie in silence. "
        "Always Sobranies with winifred."
    )
    proposals = find_extras_proposals(text, known_names={"winifred"}, min_repeats=2)
    smokes = [p for p in proposals if p.key == "smokes"]
    assert len(smokes) == 1
    assert smokes[0].entity_id == "winifred"
    assert "Sobranie" in str(smokes[0].value)
    assert smokes[0].confidence >= 0.7
    assert smokes[0].scope_hint == ExtraScope.CAMPAIGN_LOCAL


def test_below_min_repeats_yields_no_proposal():
    text = "winifred lit a Sobranie once and that was that."
    assert find_extras_proposals(text, known_names={"winifred"}, min_repeats=2) == []


def test_unknown_name_skipped():
    text = "Margaux smokes Gauloises. Margaux smoked Gauloises again."
    assert find_extras_proposals(text, known_names={"winifred"}) == []


def test_known_name_via_slug_tail():
    text = "winifred drinks Glenfarclas. winifred drank Glenfarclas again."
    proposals = find_extras_proposals(
        text,
        known_names={"library:worlds/wod/characters/winifred-allard"},
    )
    assert any(p.key == "favorite_drink" for p in proposals)


# --------------------------------------------------------------------- #
# Service routing
# --------------------------------------------------------------------- #


@pytest.fixture
def service():
    return ExtractorService(config=ExtractorConfig())


async def test_service_filters_below_threshold(service):
    # A heuristic-emitted proposal at 0.5 is below the 0.7 default → dropped.
    from grimoire.types.extraction import ExtrasProposal

    filtered = service._filter_extras_proposals(
        [
            ExtrasProposal(
                entity_kind=EntityKind.CHARACTER,
                entity_id="winifred",
                key="smokes",
                value="Sobranies",
                confidence=0.5,
                evidence="",
            )
        ]
    )
    assert filtered == []


async def test_service_caps_per_entity(service):
    from grimoire.types.extraction import ExtrasProposal

    proposals = [
        ExtrasProposal(
            entity_kind=EntityKind.CHARACTER,
            entity_id="winifred",
            key=f"k_{i}",
            value="x",
            confidence=0.8,
            evidence="",
        )
        for i in range(3)
    ]
    filtered = service._filter_extras_proposals(proposals)
    # Default cap is 1 per entity per turn.
    assert len(filtered) == 1


def test_run_heuristics_returns_proposals():
    scene = None
    snapshot = None
    text = "winifred drinks Glenfarclas. winifred drank Glenfarclas again."
    out = run_heuristics(
        text,
        scene=scene,
        snapshot=snapshot,
        pre_roll_resolved=False,
        max_candidates=5,
        campaign_id="c1",
    )
    # No scene/snapshot → known_names empty → no proposals (we only propose
    # for known characters).
    assert out.extras_proposals == []
