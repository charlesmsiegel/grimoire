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


@runtime_checkable
class ContradictionChecker(Protocol):
    """Check whether a proposed fact contradicts existing campaign facts.

    Implementations typically wrap the Continuity module. The return is
    a list of human-readable conflict descriptions; an empty list means
    no contradictions were found. The Extractor surfaces conflicts as
    `FlagLevel.CONTRADICTION` flags and downgrades the fact's confidence
    so it routes to the review queue rather than auto-applying.
    """

    async def check(
        self,
        campaign_id: CampaignId,
        fact_text: str,
        about: dict[str, list[str]],
    ) -> list[str]: ...
