"""Confidence-band routing for expression changes.

Mirrors the extractor's auto-apply / review / discard thresholds but
isolates them so the expressions pipeline can tune independently. The
defaults match the design spec (0.7 / 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass

ROUTE_AUTO_APPLY = "auto_apply"
ROUTE_REVIEW = "review"
ROUTE_DISCARD = "discard"


@dataclass(frozen=True)
class RoutingThresholds:
    auto_apply: float = 0.7
    review: float = 0.5


def classify_route(confidence: float, thresholds: RoutingThresholds | None = None) -> str:
    """Return the route name for a given confidence."""
    t = thresholds or RoutingThresholds()
    if confidence >= t.auto_apply:
        return ROUTE_AUTO_APPLY
    if confidence >= t.review:
        return ROUTE_REVIEW
    return ROUTE_DISCARD


__all__ = [
    "ROUTE_AUTO_APPLY",
    "ROUTE_DISCARD",
    "ROUTE_REVIEW",
    "RoutingThresholds",
    "classify_route",
]
