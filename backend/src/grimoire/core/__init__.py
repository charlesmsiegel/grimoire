"""V2 fiction-first domain core (no I/O, UI, or model provider dependencies)."""

from .identity import IdentityConflict, InstanceRef, LegacyId, SourceRef, parse_ref
from .index import (
    BrokenReference,
    DefinitionIndex,
    DefinitionLink,
    LegacyIdMap,
    SourceDescriptor,
    UnmappedLegacyId,
)
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
    "BrokenReference", "DefinitionIndex", "DefinitionLink", "LegacyIdMap",
    "SourceDescriptor", "UnmappedLegacyId",
    "MissingDefinition", "actor_view",
]
