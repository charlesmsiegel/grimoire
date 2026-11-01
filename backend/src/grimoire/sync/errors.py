"""Named sync outcomes. A store error that is not listed here propagates unchanged."""

from __future__ import annotations


class SyncError(Exception):
    """Base of every sync refusal."""


class NoDeviceIdentity(SyncError):
    """A library has no (or a damaged) ``.sync/device.json``; call ``identify`` first."""


class SameDevice(SyncError):
    """Both libraries claim one device ID: the same library twice, or a folder copied with its ``.sync``."""


class IdentityExists(SyncError):
    """``identify`` would change a valid device ID; pass ``replace=True`` to do it on purpose."""


class ArchiveTooLarge(SyncError):
    """The pair's archive would exceed the store's file size limit (compaction is a later milestone)."""


class ResolveRefused(SyncError):
    """A ``resolve`` request that cannot be applied safely; nothing was written."""


class SyncInterrupted(SyncError):
    """A sync stopped before it wrote its archive: a race, a refusal or a failed verification.

    Nothing already published is lost, no archive was written after the stop, and the next sync
    re-plans from the older archive. ``report`` is what the sync knew when it stopped.
    """

    def __init__(self, message: str, report: object = None) -> None:
        super().__init__(message)
        self.report = report


class SyncNotQuiet(SyncError):
    """``sync_until_quiet`` ran out of attempts while every sync still wrote something."""

    def __init__(self, message: str, report: object = None) -> None:
        super().__init__(message)
        self.report = report
