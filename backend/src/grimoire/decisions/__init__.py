"""Recorded NPC decisions (sprint 045, first slice): one bounded choice per NPC per turn, with evidence.

039's deterministic policy is the default; an optional, capped ``decision`` call may pick among the
approaches it allows (``grimoire.generation.decision``, ``GenerationRuntime.decide``). Either way the
turn's choice is written down as a ``DecisionRecord`` and replayed, never re-decided, when narration
is retried. Pure and in memory. See docs/DECISIONS.md for the contract.
"""

from .codec import FORMAT, decision_from_payload, decision_to_payload
from .model import (
    METHODS, Candidate, DecisionError, DecisionRecord, Evidence, agenda_digest, record_decision, replay,
)

__all__ = [
    "FORMAT", "decision_from_payload", "decision_to_payload",
    "METHODS", "Candidate", "DecisionError", "DecisionRecord", "Evidence", "agenda_digest", "record_decision",
    "replay",
]
