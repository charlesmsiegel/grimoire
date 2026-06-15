"""Confidence-band routing for expression changes.

Mirrors the extractor's auto-apply / review / discard thresholds but
isolates them so the expressions pipeline can tune independently. The
defaults match the design spec (0.7 / 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.util import ConfidenceTier, classify_confidence

ROUTE_AUTO_APPLY = "auto_apply"
ROUTE_REVIEW = "review"
ROUTE_DISCARD = "discard"

_TIER_TO_ROUTE = {
    ConfidenceTier.AUTO_APPLY: ROUTE_AUTO_APPLY,
    ConfidenceTier.REVIEW: ROUTE_REVIEW,
    ConfidenceTier.DROP: ROUTE_DISCARD,
}


@dataclass(frozen=True)
class RoutingThresholds:
    auto_apply: float = 0.7
    review: float = 0.5


def classify_route(confidence: float, thresholds: RoutingThresholds | None = None) -> str:
    """Return the route name for a given confidence."""
    t = thresholds or RoutingThresholds()
    tier = classify_confidence(confidence, auto_apply=t.auto_apply, review=t.review)
    return _TIER_TO_ROUTE[tier]


__all__ = [
    "ROUTE_AUTO_APPLY",
    "ROUTE_DISCARD",
    "ROUTE_REVIEW",
    "RoutingThresholds",
    "classify_route",
]
