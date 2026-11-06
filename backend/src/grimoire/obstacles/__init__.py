"""Open obstacles (Sprint 040): problems with stakes and several credible answers, never a set solution.

Pure values and rules over 029 frames and outcomes. The store-backed service that records obstacles
and links attempts to them is ``grimoire.obstaclestore``. See docs/OBSTACLES.md.
"""

from .codec import (
    ATTEMPT_KEYS, OBSTACLE_KEYS, approach_from_payload, approach_to_payload, attempt_from_payload,
    attempt_to_payload, obstacle_from_payload, obstacle_to_payload,
)
from .model import (
    ANY, ESCALATION, FACT_KINDS, MAX_APPROACHES, MAX_COSTS, MAX_FACTS, MAX_STAKES, MIN_VIABLE, Approach, Attempt,
    AttemptState, Consequence, Cost, Obstacle, ObstacleError, ObstacleFact, ObstacleStake, ObstacleState,
    check_attempt, derive, escalation, required_factors, resolve_approach,
)

__all__ = [
    "ANY", "ATTEMPT_KEYS", "ESCALATION", "FACT_KINDS", "MAX_APPROACHES", "MAX_COSTS", "MAX_FACTS", "MAX_STAKES",
    "MIN_VIABLE", "OBSTACLE_KEYS", "Approach", "Attempt", "AttemptState", "Consequence", "Cost", "Obstacle",
    "ObstacleError", "ObstacleFact", "ObstacleStake", "ObstacleState", "approach_from_payload",
    "approach_to_payload", "attempt_from_payload", "attempt_to_payload", "check_attempt", "derive", "escalation",
    "obstacle_from_payload", "obstacle_to_payload", "required_factors", "resolve_approach",
]
