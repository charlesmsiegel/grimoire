"""Fiction-first adjudication (Sprint 029): judge a mechanics-free action without narrator fiat.

Pure values and rules. The store-backed service that records frames, rulings and decisions is
``grimoire.adjudicationstore``. See docs/ADJUDICATION.md.
"""

from .codec import (
    DECISION_KEYS, FRAME_KEYS, RULING_KEYS, attempt_from_payload, attempt_to_payload, decision_from_payload,
    decision_to_payload, frame_from_payload, frame_to_payload, ruling_from_payload, ruling_to_payload,
)
from .model import (
    ABILITY_FACTOR, ABILITY_UNCERTAIN, BASES, BEARINGS, MAX_FACTORS, MAX_STAKES, PROSE_MAX, RESULTS, AbilityFinding,
    ActionAssessment, ActionFrame, AdjudicationError, Decision, DerivedFactor, Factor, Outcome, Stake, assess_action,
    check_decision,
)

__all__ = [
    "ABILITY_FACTOR", "ABILITY_UNCERTAIN", "BASES", "BEARINGS", "DECISION_KEYS", "FRAME_KEYS", "MAX_FACTORS",
    "MAX_STAKES", "PROSE_MAX", "RESULTS", "RULING_KEYS", "AbilityFinding", "ActionAssessment", "ActionFrame",
    "AdjudicationError", "Decision", "DerivedFactor", "Factor", "Outcome", "Stake", "assess_action",
    "attempt_from_payload", "attempt_to_payload", "check_decision", "decision_from_payload", "decision_to_payload",
    "frame_from_payload", "frame_to_payload", "ruling_from_payload", "ruling_to_payload",
]
