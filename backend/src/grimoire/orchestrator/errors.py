"""Exceptions raised by the Orchestrator."""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for orchestrator-specific failures."""


class UnknownCampaignError(OrchestratorError):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"campaign not found: {campaign_id!r}")
        self.campaign_id = campaign_id


class UnknownPCError(OrchestratorError):
    def __init__(self, campaign_id: str, pc_ref: str) -> None:
        super().__init__(f"pc {pc_ref!r} not registered for campaign {campaign_id!r}")
        self.campaign_id = campaign_id
        self.pc_ref = pc_ref


class TurnAlreadyInProgressError(OrchestratorError):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"turn already in progress for campaign {campaign_id!r}")
        self.campaign_id = campaign_id


class NoTurnsToUndoError(OrchestratorError):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"no turns to undo for campaign {campaign_id!r}")
        self.campaign_id = campaign_id


class TurnCancelledError(OrchestratorError):
    """Raised internally when an active turn is cooperatively cancelled."""


class TurnTimeoutError(OrchestratorError):
    """Raised when a turn exceeds ``turn_timeout_seconds``."""


__all__ = [
    "NoTurnsToUndoError",
    "OrchestratorError",
    "TurnAlreadyInProgressError",
    "TurnCancelledError",
    "TurnTimeoutError",
    "UnknownCampaignError",
    "UnknownPCError",
]
