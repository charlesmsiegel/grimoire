"""Narrow integration protocols for the Extractor.

The Extractor reaches outside its own module for four things: an LLM
provider (for the structured-LLM strategy), the Mechanics façade (to
validate narrated mechanical events), a contradiction checker (typically
backed by the Continuity module's fact ledger), and an entity resolver
(to determine whether a referenced entity lives in the library or in
campaign-local state). Each is abstracted behind a tiny protocol so the
Extractor can be built and tested without the full module wired up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from grimoire.types.common import CampaignId, EntityKind, Json, Scope, ValidationResult
from grimoire.types.mechanics import NarratedEvent
from grimoire.types.scene import SceneContext


@runtime_checkable
class MechanicsValidator(Protocol):
    """The bit of `Mechanics` the Extractor uses to validate prose events."""

    async def validate_narrated_event(
        self,
        campaign_id: CampaignId,
        event: NarratedEvent,
        scene: SceneContext,
    ) -> ValidationResult: ...


class ConflictRecord(BaseModel):
    """Structured description of one contradicting existing fact.

    Carries enough information for the review-UI to render the existing
    fact alongside the proposed new fact (spec extractor-remaining §2).
    `source_turn` is optional because not every backing store records
    the originating turn for every fact.
    """

    fact_id: str
    text: str
    source_turn: str | None = None


@runtime_checkable
class ContradictionChecker(Protocol):
    """Check whether a proposed fact contradicts existing campaign facts.

    Implementations typically wrap the Continuity module. The return is
    a list of `ConflictRecord`s, one per contradicting existing fact; an
    empty list means no contradictions were found. The Extractor surfaces
    conflicts as `FlagLevel.CONTRADICTION` flags, force-routes the new
    delta into the review queue, and threads the structured records into
    both the flag payload and `delta.extra["contradictions"]` so review
    consumers can render the existing fact records.
    """

    async def check(
        self,
        campaign_id: CampaignId,
        fact_text: str,
        about: dict[str, list[str]],
    ) -> list[ConflictRecord]: ...


@dataclass(frozen=True)
class ResolvedEntity:
    """Resolution result returned by `EntityResolver.resolve`.

    `scope` indicates where the entity's current state actually lives
    (library vs. campaign cascade). `card` is the live card dict the
    Extractor compares prose-derived deltas against to detect library
    drift (spec extractor-remaining §1).
    """

    scope: Scope
    card: Json


@runtime_checkable
class EntityResolver(Protocol):
    """Resolve an entity ref to its scope + current card.

    Implementations typically wrap the campaign asset cascade
    (campaign-local → library). Return `None` when the ref does not
    resolve to any known entity — the Extractor treats that the same as
    a non-library scope (no drift can be flagged against an absent card).
    """

    async def resolve(
        self,
        campaign_id: CampaignId,
        entity_ref: str,
        kind: EntityKind,
    ) -> ResolvedEntity | None: ...
