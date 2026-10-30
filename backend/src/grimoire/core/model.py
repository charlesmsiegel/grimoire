"""V2 domain vocabulary, deliberately independent of disk formats and LLMs.

These immutable values are a *first vertical-slice contract*, not a final
persistent schema. They are kept small to expose mistaken identity and
knowledge assumptions before a storage implementation exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .identity import InstanceRef, SourceRef, validate_segment


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class CharacterDefinition:
    """One revision of reusable authoring/portrayal data, not campaign history.

    Derivation is an explicit ``DefinitionLink``, not a field here.
    """

    ref: SourceRef
    name: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.ref.kind != "character":
            raise ValueError("a character definition requires kind='character'")
        if not self.ref.pinned:
            raise ValueError("a definition is always a specific revision; pin its ref")
        _nonempty(self.name, "name")


@dataclass(frozen=True, slots=True)
class CampaignActor:
    """One incarnation in one campaign, regardless of PC/NPC control."""

    campaign_id: str
    actor_id: str
    source: SourceRef | None = None
    local_name: str | None = None

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.actor_id, "actor_id")
        if self.source is not None and self.source.kind != "character":
            raise ValueError("actor source must be a character definition")
        if self.local_name is not None:
            _nonempty(self.local_name, "local_name")
        if self.source is None and self.local_name is None:
            raise ValueError("a campaign-original actor needs a local_name")

    @property
    def ref(self) -> InstanceRef:
        return InstanceRef(self.campaign_id, "character", self.actor_id)


@dataclass(frozen=True, slots=True)
class ControlAssignment:
    """Control/authority is a relationship, not a character subtype."""

    campaign_id: str
    actor_id: str
    controller_id: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.actor_id, "actor_id")
        _nonempty(self.controller_id, "controller_id")


@dataclass(frozen=True, slots=True)
class Event:
    """Director-visible accepted event; not automatically actor knowledge."""

    campaign_id: str
    event_id: str
    description: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")


@dataclass(frozen=True, slots=True)
class Observation:
    """What one actor perceived, not the entire objective event."""

    campaign_id: str
    actor_id: str
    event_id: str
    channel: str
    perceived: str

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.actor_id, "actor_id")


@dataclass(frozen=True, slots=True)
class Belief:
    """An actor's possibly false interpretation; never promoted to truth."""

    campaign_id: str
    actor_id: str
    proposition: str
    status: Literal["believed", "suspected", "rejected"]
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.actor_id, "actor_id")


@dataclass(frozen=True, slots=True)
class ActorView:
    """Disposable perspective for one inference, not a canonical record."""

    campaign_id: str
    actor_id: str
    name: str
    description: str
    observations: tuple[Observation, ...]
    beliefs: tuple[Belief, ...]
    resolved_source: SourceRef | None = None  # the exact revision this view used
