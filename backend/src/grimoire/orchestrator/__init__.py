"""Orchestrator (spec 01).

Drives the turn loop: receives player input, decides whether to call the
LLM now or wait for a multi-PC advance, composes a prompt via the
Context Builder, streams the LLM response through the Gateway, extracts
state deltas, and applies them via the State Store. Schedules background
work over the in-process event bus.
"""

from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.errors import (
    NoTurnsToUndoError,
    OrchestratorError,
    TurnAlreadyInProgressError,
    UnknownCampaignError,
    UnknownPCError,
)
from grimoire.orchestrator.service import OrchestratorService, WSPushFn

__all__ = [
    "NoTurnsToUndoError",
    "OrchestratorConfig",
    "OrchestratorError",
    "OrchestratorService",
    "TurnAlreadyInProgressError",
    "UnknownCampaignError",
    "UnknownPCError",
    "WSPushFn",
]
