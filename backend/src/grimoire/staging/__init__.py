"""Scene staging (Sprint 041, first slice): who and what is where in one scene, and what follows from it.

Pure values (``model``), physical changes and corrections (``changes``) and the questions a runner or
referee asks (``senses``): can this actor reach that thing, see that individual, hear that speaker on a
032 channel, and who witnesses an event. Nothing here touches files or calls a model, and nothing is
stored in this slice. The stage is scene-local director truth: never world canon, never actor knowledge.
"""

from .changes import (
    CORRECTIONS, OPS, PHYSICAL, Change, actor_refusal, apply, close_way, drop, enter, give, leave, lock_way,
    move, open_way, place, reach_refusal, remove, set_way, take, unlock_way,
)
from .model import (
    BASES, LABEL_LIMIT, MAX_ACTORS, MAX_AREAS, MAX_PLACEMENTS, MAX_WAYS, WAY_STATES, Absence, Area, Placement,
    Root, Stage, StagingRefused, Way,
)
from .senses import (
    EVENT_ID_MAX, WHEREABOUTS, Answer, Whereabouts, audience, check_cast, custody_divergence, deliver, hears, perception_id, perceptions, presence,
    reach, sees, where, witnesses,
)

__all__ = [
    "BASES", "CORRECTIONS", "EVENT_ID_MAX", "LABEL_LIMIT", "MAX_ACTORS", "MAX_AREAS", "MAX_PLACEMENTS",
    "MAX_WAYS", "OPS", "PHYSICAL", "WAY_STATES", "WHEREABOUTS", "Absence", "Answer", "Area", "Change",
    "Placement", "Root", "Stage", "StagingRefused", "Way", "Whereabouts", "actor_refusal", "apply",
    "audience", "check_cast", "close_way", "custody_divergence", "deliver", "drop", "enter", "give", "hears", "leave", "lock_way", "move", "open_way",
    "perception_id", "perceptions", "place", "presence", "reach", "reach_refusal", "remove", "sees",
    "set_way", "take", "unlock_way", "where", "witnesses",
]
