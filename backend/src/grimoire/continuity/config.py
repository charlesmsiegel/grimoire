"""Configuration for the Continuity module.

Mirrors the YAML schema in spec 11 §Configuration. Defaults match the spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grimoire.continuity.types import Duration


@dataclass
class ContradictionCheckConfig:
    enabled: bool = True
    top_k_similar: int = 5
    model_route: str = "drift_check"  # logical LLM Gateway task route


@dataclass
class KeywordRetrievalConfig:
    min_keyword_length: int = 4
    case_insensitive: bool = True


@dataclass
class ContinuityConfig:
    fact_confidence_floor: float = 0.5
    commitment_stale_threshold: Duration = field(default_factory=lambda: Duration.months(6))
    contradiction_check: ContradictionCheckConfig = field(default_factory=ContradictionCheckConfig)
    keyword_retrieval: KeywordRetrievalConfig = field(default_factory=KeywordRetrievalConfig)
    surface_overdue_in_context: bool = True
    surface_stale_in_context: bool = False


__all__ = [
    "ContinuityConfig",
    "ContradictionCheckConfig",
    "KeywordRetrievalConfig",
]
