"""Extractor module (spec 04).

Parses model and player text into typed `StateDelta` proposals, entity
candidates and flags. Runs three strategies in parallel — rule-based,
structured-LLM, and heuristic — then merges, deduplicates and routes by
confidence into auto-apply, review or drop.
"""

from __future__ import annotations

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.protocols import ContradictionChecker, MechanicsValidator
from grimoire.extractor.routing import Decision, Routing, route_deltas
from grimoire.extractor.service import ExtractorService

__all__ = [
    "ContradictionChecker",
    "Decision",
    "ExtractorConfig",
    "ExtractorService",
    "MechanicsValidator",
    "Routing",
    "route_deltas",
]
