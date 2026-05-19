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


class LatestPostOnlyError(OrchestratorError):
    """Raised when an alternate operation targets a non-latest model post."""

    def __init__(self, post_id: str) -> None:
        super().__init__(
            f"alternate operations are only allowed on the latest model post: {post_id!r}"
        )
        self.post_id = post_id


class AlternateNotFoundError(OrchestratorError):
    """Raised when the requested alternate id is not on the given post."""

    def __init__(self, post_id: str, alternate_id: str) -> None:
        super().__init__(f"alternate {alternate_id!r} not found on post {post_id!r}")
        self.post_id = post_id
        self.alternate_id = alternate_id


class CannotDeletePrimaryError(OrchestratorError):
    """Raised when an alternate-delete targets the current primary."""

    def __init__(self, post_id: str, alternate_id: str) -> None:
        super().__init__(
            f"cannot delete primary alternate {alternate_id!r} on post {post_id!r}; "
            "switch primary first"
        )
        self.post_id = post_id
        self.alternate_id = alternate_id


class RetconInFlightError(OrchestratorError):
    """Raised when a second retcon replay is started while one is already open."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"a retcon replay batch is already in flight for campaign {campaign_id!r}")
        self.campaign_id = campaign_id


class RetconBatchNotFoundError(OrchestratorError):
    """Raised when a batch_id doesn't match any open or recent retcon session."""

    def __init__(self, batch_id: str) -> None:
        super().__init__(f"retcon replay batch {batch_id!r} not found")
        self.batch_id = batch_id


class RetconBatchClosedError(OrchestratorError):
    """Raised when an accept/try-again/cancel is issued on a completed batch."""

    def __init__(self, batch_id: str) -> None:
        super().__init__(f"retcon replay batch {batch_id!r} is already closed")
        self.batch_id = batch_id


class CampaignIdExists(OrchestratorError):
    """Raised when a fork target campaign id already exists."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"campaign already exists: {campaign_id!r}")
        self.campaign_id = campaign_id



__all__ = [
    "AlternateNotFoundError",
    "CampaignIdExists",
    "CannotDeletePrimaryError",
    "LatestPostOnlyError",
    "NoTurnsToUndoError",
    "OrchestratorError",
    "RetconBatchClosedError",
    "RetconBatchNotFoundError",
    "RetconInFlightError",
    "TurnAlreadyInProgressError",
    "TurnCancelledError",
    "TurnTimeoutError",
    "UnknownCampaignError",
    "UnknownPCError",
]
