"""Confidence-routing tests."""

from __future__ import annotations

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.routing import Decision, decide, route_deltas
from grimoire.types.common import Scope
from grimoire.types.state import DeltaKind, StateDelta


def _delta(confidence: float) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.OTHER,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=f"t-{confidence}",
        confidence=confidence,
    )


def test_decide_routes_by_threshold():
    config = ExtractorConfig()
    assert decide(_delta(0.95), config=config) == Decision.AUTO_APPLY
    assert decide(_delta(0.85), config=config) == Decision.AUTO_APPLY  # inclusive
    assert decide(_delta(0.7), config=config) == Decision.REVIEW
    assert decide(_delta(0.6), config=config) == Decision.REVIEW  # inclusive
    assert decide(_delta(0.5), config=config) == Decision.DROP


def test_route_deltas_buckets_all_three():
    config = ExtractorConfig()
    routing = route_deltas([_delta(0.9), _delta(0.7), _delta(0.3)], config=config)
    assert len(routing.auto_apply) == 1
    assert len(routing.review) == 1
    assert len(routing.dropped) == 1
    assert {d.confidence for d, _ in routing.decisions()} == {0.9, 0.7, 0.3}
