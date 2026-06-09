"""Exceptions raised by the Orchestrator."""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for orchestrator-specific failures."""

    http_status = 409


class UnknownCampaignError(OrchestratorError):
    http_status = 404

    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"campaign not found: {campaign_id!r}")
        self.campaign_id = campaign_id


class UnknownPCError(OrchestratorError):
    http_status = 404

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


class SceneClosedError(OrchestratorError):
    """Raised when a mutating op (e.g. cascade delete) targets a closed scene."""

    http_status = 409

    def __init__(self, scene_id: str) -> None:
        super().__init__(f"scene {scene_id!r} is closed")
        self.scene_id = scene_id


class TurnCancelledError(OrchestratorError):
    """Raised internally when an active turn is cooperatively cancelled."""


class TurnTimeoutError(OrchestratorError):
    """Raised when a turn exceeds ``turn_timeout_seconds``."""


class LatestPostOnlyError(OrchestratorError):
    """Raised when an alternate operation targets a non-latest model post."""

    http_status = 400

    def __init__(self, post_id: str) -> None:
        super().__init__(
            f"alternate operations are only allowed on the latest model post: {post_id!r}"
        )
        self.post_id = post_id


class AlternateNotFoundError(OrchestratorError):
    """Raised when the requested alternate id is not on the given post."""

    http_status = 404

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

    http_status = 404

    def __init__(self, batch_id: str) -> None:
        super().__init__(f"retcon replay batch {batch_id!r} not found")
        self.batch_id = batch_id


class RetconBatchClosedError(OrchestratorError):
    """Raised when an accept/try-again/cancel is issued on a completed batch."""

    def __init__(self, batch_id: str) -> None:
        super().__init__(f"retcon replay batch {batch_id!r} is already closed")
        self.batch_id = batch_id


class RetconExtractionError(OrchestratorError):
    """Raised when re-extraction fails during a retcon.

    Raised before any state is touched: the post text and the turn's deltas
    are exactly as they were, so the caller can simply retry (#583).
    """

    http_status = 502

    def __init__(self, post_id: str) -> None:
        super().__init__(
            f"retcon aborted: could not re-extract state changes for post {post_id!r}; "
            "the post text and campaign state are unchanged"
        )
        self.post_id = post_id


class RetconStateError(OrchestratorError):
    """Raised when the atomic delta swap fails during a retcon.

    The swap (reverse old turn deltas + apply re-extracted ones) is
    all-or-nothing, so campaign state is exactly as it was before the
    retcon; the post text edit has been rolled back as well (#583).
    """

    http_status = 500

    def __init__(self, post_id: str) -> None:
        super().__init__(
            f"retcon aborted: could not replace the turn's state deltas for post "
            f"{post_id!r}; campaign state was left unchanged"
        )
        self.post_id = post_id


class CampaignIdExists(OrchestratorError):
    """Raised when a fork target campaign id already exists."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"campaign already exists: {campaign_id!r}")
        self.campaign_id = campaign_id


class AuxiliaryNotFoundError(OrchestratorError):
    """Raised when an accept/discard targets an unknown auxiliary result id."""

    http_status = 404

    def __init__(self, result_id: str) -> None:
        super().__init__(f"auxiliary result not found: {result_id!r}")
        self.result_id = result_id


class AuxiliaryAlreadyCommittedError(OrchestratorError):
    """Raised when a second accept arrives for an already-committed auxiliary."""

    def __init__(self, result_id: str) -> None:
        super().__init__(f"auxiliary result already committed: {result_id!r}")
        self.result_id = result_id


__all__ = [
    "AlternateNotFoundError",
    "AuxiliaryAlreadyCommittedError",
    "AuxiliaryNotFoundError",
    "CampaignIdExists",
    "CannotDeletePrimaryError",
    "LatestPostOnlyError",
    "NoTurnsToUndoError",
    "OrchestratorError",
    "RetconBatchClosedError",
    "RetconBatchNotFoundError",
    "RetconExtractionError",
    "RetconInFlightError",
    "RetconStateError",
    "SceneClosedError",
    "TurnAlreadyInProgressError",
    "TurnCancelledError",
    "TurnTimeoutError",
    "UnknownCampaignError",
    "UnknownPCError",
]
