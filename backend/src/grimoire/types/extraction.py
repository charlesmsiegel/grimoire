"""Extractor types: results, candidates, flags."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .common import EntityKind, Json
from .state import StateDelta
from .transient import TransientUpdateProposal


class FlagLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CONTRADICTION = "contradiction"
    MISSING_CONTEXT = "missing_context"
    MISSING_MECHANIC = "missing_mechanic"


class ExtractionFlag(BaseModel):
    level: FlagLevel
    code: str  # short machine identifier
    message: str
    evidence: str = ""
    related: list[str] = Field(default_factory=list)
    payload: Json = Field(default_factory=dict)


class EntityCandidate(BaseModel):
    """A newly named entity proposed by the Extractor.

    All candidates default to campaign-local scope; the user opts in to promote.
    """

    kind: EntityKind
    proposed_id: str
    proposed_name: str
    role_hint: str = ""
    evidence: str = ""
    confidence: float = 0.0
    suggested_card: Json = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    deltas: list[StateDelta] = Field(default_factory=list)
    candidates: list[EntityCandidate] = Field(default_factory=list)
    flags: list[ExtractionFlag] = Field(default_factory=list)
    transient_updates: list[TransientUpdateProposal] = Field(default_factory=list)
    confidence_overall: float = 0.0
    extraction_strategies_run: list[str] = Field(default_factory=list)
    duration_ms: int = 0
