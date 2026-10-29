"""V2 domain vocabulary, deliberately independent of disk formats and LLMs.

These immutable values are a *first vertical-slice contract*, not a final
persistent schema. They are kept small to expose mistaken identity and
knowledge assumptions before a storage implementation exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Source-qualified identity; names and matching slugs are not identity."""

    source: str
    kind: str
    entity_id: str
    revision: str | None = None

    def __post_init__(self) -> None:
        for label in ("source", "kind", "entity_id"):
            _nonempty(getattr(self, label), label)
        if self.revision is not None:
            _nonempty(self.revision, "revision")


@dataclass(frozen=True, slots=True)
class CharacterDefinition:
    """Reusable authoring/portrayal data, not the person's campaign history."""

    ref: SourceRef
    name: str
    description: str = ""
    derived_from: SourceRef | None = None

    def __post_init__(self) -> None:
        if self.ref.kind != "character":
            raise ValueError("a character definition requires kind='character'")
        _nonempty(self.name, "name")


@dataclass(frozen=True, slots=True)
class CampaignActor:
    """One incarnation in one campaign, regardless of PC/NPC control."""

    campaign_id: str
    actor_id: str
    source: SourceRef | None = None
    local_name: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.campaign_id, "campaign_id")
        _nonempty(self.actor_id, "actor_id")
        if self.source is not None and self.source.kind != "character":
            raise ValueError("actor source must be a character definition")
        if self.local_name is not None:
            _nonempty(self.local_name, "local_name")
        if self.source is None and self.local_name is None:
            raise ValueError("a campaign-original actor needs a local_name")


@dataclass(frozen=True, slots=True)
class ControlAssignment:
    """Control/authority is a relationship, not a character subtype."""

    campaign_id: str
    actor_id: str
    controller_id: str

    def __post_init__(self) -> None:
        for label in ("campaign_id", "actor_id", "controller_id"):
            _nonempty(getattr(self, label), label)


@dataclass(frozen=True, slots=True)
class Event:
    """Director-visible accepted event; not automatically actor knowledge."""

    campaign_id: str
    event_id: str
    description: str


@dataclass(frozen=True, slots=True)
class Observation:
    """What one actor perceived, not the entire objective event."""

    campaign_id: str
    actor_id: str
    event_id: str
    channel: str
    perceived: str


@dataclass(frozen=True, slots=True)
class Belief:
    """An actor's possibly false interpretation; never promoted to truth."""

    campaign_id: str
    actor_id: str
    proposition: str
    status: Literal["believed", "suspected", "rejected"]
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActorView:
    """Disposable perspective for one inference, not a canonical record."""

    campaign_id: str
    actor_id: str
    name: str
    description: str
    observations: tuple[Observation, ...]
    beliefs: tuple[Belief, ...]
