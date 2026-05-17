"""Confidence-routing tests."""

from __future__ import annotations

import logging

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.routing import Decision, decide, route_deltas
from grimoire.types.common import Scope
from grimoire.types.state import DeltaKind, StateDelta


def _delta(confidence: float, *, target_id: str | None = None, evidence: str = "") -> StateDelta:
    return StateDelta(
        kind=DeltaKind.OTHER,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=target_id or f"t-{confidence}",
        confidence=confidence,
        evidence=evidence,
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


def test_route_deltas_emits_debug_log_for_dropped(caplog):
    """Spec extractor-remaining §4: dropped deltas must leave a calibration trail."""
    config = ExtractorConfig()
    dropped = _delta(0.3, target_id="char:falin", evidence="she whispered something")
    with caplog.at_level(logging.DEBUG, logger="grimoire.extractor.routing"):
        route_deltas([_delta(0.9), dropped], config=config)

    drop_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(drop_records) == 1
    record = drop_records[0]
    # Required fields per spec: kind, target_id, confidence, evidence.
    assert "char:falin" in record.getMessage()
    assert "0.3" in record.getMessage()
    assert record.kind == str(DeltaKind.OTHER)
    assert record.target_id == "char:falin"
    assert record.confidence == 0.3
    assert record.evidence == "she whispered something"


def test_route_deltas_emits_one_log_per_dropped_delta(caplog):
    config = ExtractorConfig()
    with caplog.at_level(logging.DEBUG, logger="grimoire.extractor.routing"):
        route_deltas([_delta(0.1), _delta(0.2), _delta(0.95)], config=config)

    drop_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(drop_records) == 2


def test_route_deltas_no_debug_log_when_nothing_dropped(caplog):
    config = ExtractorConfig()
    with caplog.at_level(logging.DEBUG, logger="grimoire.extractor.routing"):
        route_deltas([_delta(0.95), _delta(0.7)], config=config)

    drop_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert drop_records == []
