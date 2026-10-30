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
from .authority import (
    ADJUDICATED_DOMAINS,
    ALL_DOMAINS,
    CONDITIONAL_DOMAINS,
    DIRECTOR,
    OPEN_DOMAINS,
    PROTECTED_DOMAINS,
    AcceptedClaim,
    AuthorityError,
    Correction,
    CorrectionRefusal,
    Delegation,
    ImposedCondition,
    NarrationClaim,
    NarrationContext,
    NarrationPolicy,
    NarrationReview,
    PlayerContribution,
    RejectedClaim,
    Resolution,
    Revocation,
    apply_corrections,
    auto_complete,
    controller_of,
    delegate,
    narration_policy,
    review_narration,
    submit_contribution,
)
from .resolve import MissingDefinition, actor_view

__all__ = [
    "ActorView", "Belief", "CampaignActor", "CharacterDefinition",
    "ControlAssignment", "Event", "Observation", "SourceRef",
    "IdentityConflict", "InstanceRef", "LegacyId", "parse_ref",
    "BrokenReference", "DefinitionIndex", "DefinitionLink", "LegacyIdMap",
    "SourceDescriptor", "UnmappedLegacyId",
    "MissingDefinition", "actor_view",
    "ADJUDICATED_DOMAINS", "ALL_DOMAINS", "CONDITIONAL_DOMAINS", "DIRECTOR",
    "OPEN_DOMAINS", "PROTECTED_DOMAINS", "AcceptedClaim", "AuthorityError",
    "Correction", "CorrectionRefusal", "Delegation", "ImposedCondition",
    "NarrationClaim", "NarrationContext", "NarrationPolicy", "NarrationReview",
    "PlayerContribution", "RejectedClaim", "Resolution", "Revocation",
    "apply_corrections", "auto_complete", "controller_of", "delegate",
    "narration_policy", "review_narration", "submit_contribution",
]
