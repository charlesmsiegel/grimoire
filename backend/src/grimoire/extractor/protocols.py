"""Narrow integration protocols for the Extractor.

The Extractor reaches outside its own module for three things: an LLM
provider (for the structured-LLM strategy), the Mechanics façade (to
validate narrated mechanical events), and a contradiction checker
(typically backed by the Continuity module's fact ledger). Each is
abstracted behind a tiny protocol so the Extractor can be built and
tested without the full module wired up.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from grimoire.types.common import CampaignId, ValidationResult
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
