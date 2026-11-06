"""NPC goals and bounded autonomy (sprint 039, first slice): authored agendas and a deterministic policy.

An NPC's ``Agenda`` (goals, approaches, limits) and its own ``ActorKnowledge`` decide what it pursues
now, with no model call; 027's brief carries the ``Choice`` into the NPC's one generation call. Pure
and in memory. See docs/GOALS.md for the contract.
"""

from .model import (
    CREDITED, MAX_APPROACHES, MAX_CONDITIONS, MAX_GOALS, MAX_LIMITS, MAX_TAGS, PRIORITY_MAX, PRIORITY_MIN, STANCES,
    TEXT_CHARS, Agenda, Approach, Condition, Goal, GoalError, Holds, Limit, Present,
)
from .policy import OUTCOMES, Choice, Considered, Desire, choose, explain, held

__all__ = [
    "CREDITED", "MAX_APPROACHES", "MAX_CONDITIONS", "MAX_GOALS", "MAX_LIMITS", "MAX_TAGS", "PRIORITY_MAX",
    "PRIORITY_MIN", "STANCES", "TEXT_CHARS", "Agenda", "Approach", "Condition", "Goal", "GoalError", "Holds", "Limit",
    "Present",
    "OUTCOMES", "Choice", "Considered", "Desire", "choose", "explain", "held",
]
