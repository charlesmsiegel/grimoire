"""Extractor types: results, candidates, flags."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .common import EntityKind, Json
from .state import StateDelta


class FlagLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CONTRADICTION = "contradiction"
    MISSING_CONTEXT = "missing_context"
    MISSING_MECHANIC = "missing_mechanic"


@dataclass
class ExtractionFlag:
    level: FlagLevel
    code: str  # short machine identifier
    message: str
    evidence: str = ""
    related: list[str] = field(default_factory=list)
    payload: Json = field(default_factory=dict)


@dataclass
class EntityCandidate:
    """A newly named entity proposed by the Extractor.

    All candidates default to campaign-local scope; the user opts in to promote.
    """

    kind: EntityKind
    proposed_id: str
    proposed_name: str
    role_hint: str = ""
    evidence: str = ""
    confidence: float = 0.0
    suggested_card: Json = field(default_factory=dict)


@dataclass
class ExtractionResult:
    deltas: list[StateDelta] = field(default_factory=list)
    candidates: list[EntityCandidate] = field(default_factory=list)
    flags: list[ExtractionFlag] = field(default_factory=list)
    confidence_overall: float = 0.0
    extraction_strategies_run: list[str] = field(default_factory=list)
    duration_ms: int = 0
