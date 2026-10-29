"""V2 fiction-first domain core (no I/O, UI, or model provider dependencies)."""

from .model import (
    ActorView,
    Belief,
    CampaignActor,
    CharacterDefinition,
    ControlAssignment,
    Event,
    Observation,
    SourceRef,
)
from .resolve import MissingDefinition, actor_view

__all__ = [
    "ActorView", "Belief", "CampaignActor", "CharacterDefinition",
    "ControlAssignment", "Event", "Observation", "SourceRef",
    "MissingDefinition", "actor_view",
]
