"""Confidence routing for extracted deltas (spec 04 §Confidence scoring).

Decides per-delta whether it auto-applies, queues for review, or drops.
Pure function — the State Store / review queue side effects live with
the Orchestrator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from grimoire.extractor.config import ExtractorConfig
from grimoire.types.state import StateDelta

logger = logging.getLogger(__name__)


class Decision(StrEnum):
    AUTO_APPLY = "auto_apply"
    REVIEW = "review"
    DROP = "drop"


@dataclass
class Routing:
    auto_apply: list[StateDelta] = field(default_factory=list)
    review: list[StateDelta] = field(default_factory=list)
    dropped: list[StateDelta] = field(default_factory=list)

    def decisions(self) -> list[tuple[StateDelta, Decision]]:
        result: list[tuple[StateDelta, Decision]] = []
        for d in self.auto_apply:
            result.append((d, Decision.AUTO_APPLY))
        for d in self.review:
            result.append((d, Decision.REVIEW))
        for d in self.dropped:
            result.append((d, Decision.DROP))
        return result


def decide(delta: StateDelta, *, config: ExtractorConfig) -> Decision:
    if delta.confidence >= config.auto_apply_threshold:
        return Decision.AUTO_APPLY
    if delta.confidence >= config.review_threshold:
        return Decision.REVIEW
    return Decision.DROP


def route_deltas(deltas: list[StateDelta], *, config: ExtractorConfig) -> Routing:
    """Bucket each delta by confidence threshold."""
    routing = Routing()
    for delta in deltas:
        bucket = decide(delta, config=config)
        if bucket is Decision.AUTO_APPLY:
            routing.auto_apply.append(delta)
        elif bucket is Decision.REVIEW:
            routing.review.append(delta)
        else:
            routing.dropped.append(delta)
            # Feeds the calibration loop (spec 04 §Open questions item 4):
            # silently-dropped deltas are otherwise invisible.
            logger.debug(
                "dropped delta kind=%s target_id=%s confidence=%.3f evidence=%r",
                str(delta.kind),
                delta.target_id,
                delta.confidence,
                delta.evidence,
                extra={
                    "kind": str(delta.kind),
                    "target_id": delta.target_id,
                    "confidence": delta.confidence,
                    "evidence": delta.evidence,
                },
            )
    return routing


__all__ = ["Decision", "Routing", "decide", "route_deltas"]
