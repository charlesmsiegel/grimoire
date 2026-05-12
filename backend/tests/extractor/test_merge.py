"""Merge / dedupe tests."""

from __future__ import annotations

from grimoire.extractor.merge import merge_candidates, merge_deltas
from grimoire.types.common import EntityKind, Scope
from grimoire.types.extraction import EntityCandidate
from grimoire.types.state import DeltaKind, StateDelta


def _delta(kind: DeltaKind, target_id: str, confidence: float, strategy: str) -> StateDelta:
    return StateDelta(
        kind=kind,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id=target_id,
        target_table="t",
        confidence=confidence,
        evidence=f"{strategy} evidence",
        extra={"strategy": strategy},
    )


def test_merge_deltas_keeps_higher_confidence_and_unions_strategies():
    a = _delta(DeltaKind.TIME_ADVANCE, "time:7200", 0.95, "rule_based")
    b = _delta(DeltaKind.TIME_ADVANCE, "time:7200", 0.6, "structured_llm")
    merged = merge_deltas([a], [b])
    assert len(merged) == 1
    assert merged[0].confidence == 0.95
    assert merged[0].extra["strategies"] == ["rule_based", "structured_llm"]


def test_merge_deltas_keeps_distinct_targets():
    a = _delta(DeltaKind.TIME_ADVANCE, "time:7200", 0.9, "rule_based")
    b = _delta(DeltaKind.TIME_ADVANCE, "time:3600", 0.9, "rule_based")
    merged = merge_deltas([a, b])
    assert {d.target_id for d in merged} == {"time:7200", "time:3600"}


def test_merge_candidates_dedupes_by_name():
    a = EntityCandidate(
        kind=EntityKind.CHARACTER,
        proposed_id="margaux",
        proposed_name="Margaux",
        confidence=0.6,
    )
    b = EntityCandidate(
        kind=EntityKind.CHARACTER,
        proposed_id="margaux",
        proposed_name="Margaux",
        confidence=0.9,
    )
    merged = merge_candidates([a], [b])
    assert len(merged) == 1
    assert merged[0].confidence == 0.9
