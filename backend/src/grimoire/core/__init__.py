"""V2 fiction-first domain core (no I/O, UI, or model provider dependencies)."""

from .identity import IdentityConflict, InstanceRef, LegacyId, SourceRef, parse_ref
from .model import (
    ActorView,
    Belief,
    CampaignActor,
    CharacterDefinition,
    ControlAssignment,
    Event,
    Observation,
)
from .resolve import MissingDefinition, actor_view

__all__ = [
    "ActorView", "Belief", "CampaignActor", "CharacterDefinition",
    "ControlAssignment", "Event", "Observation", "SourceRef",
    "IdentityConflict", "InstanceRef", "LegacyId", "parse_ref",
    "MissingDefinition", "actor_view",
]
