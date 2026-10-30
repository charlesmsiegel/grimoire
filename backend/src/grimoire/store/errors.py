"""Store refusals and failures.

None of these is an ``OSError``: a refusal is never an I/O error a caller
should retry, and an adapter's ``except OSError`` must not swallow one. Raw
``OSError``s from the disk before a commit point propagate unchanged, because
nothing was published.
"""

from __future__ import annotations

from collections.abc import Sequence

from grimoire.library import LibraryPathError


class StoreError(Exception):
    """A store refusal or store-level failure."""


class InvalidEnvelope(StoreError):
    """A caller-supplied record or manifest cannot be written as given."""


class UnstorableIdentifier(StoreError):
    """An identifier or path that cannot be mapped to a portable file name."""


class CrossDevice(StoreError):
    """A target is on a different filesystem from the staging directory."""


class ImmutableRevision(StoreError):
    """A definition revision exists; edit by adding a revision and moving the head."""


class StaleWrite(StoreError):
    """A target no longer has the expected content revision; nothing was written."""

    def __init__(self, stale: Sequence[tuple[str, str]]) -> None:
        self.stale = tuple(stale)
        super().__init__("stale write: " + ", ".join(f"{path} ({reason})" for path, reason in self.stale))


class NameCollision(StoreError):
    """A new name differs only by case or Unicode form from an existing entry."""


class InvalidSourceState(StoreError):
    """The transaction would leave a source in a state the loader must drop."""


class WriteDenied(StoreError):
    """Authorization refused a target; no target was written."""

    def __init__(self, message: str, decisions: Sequence[object], consumed_approvals: Sequence[str]) -> None:
        super().__init__(message)
        self.decisions = tuple(decisions)
        self.consumed_approvals = tuple(consumed_approvals)


class WriteConflict(StoreError):
    """An external edit raced the commit; it was kept and the store's bytes preserved."""

    def __init__(self, preserved: Sequence[str], published: Sequence[tuple[str, str]]) -> None:
        self.preserved = tuple(preserved)
        self.published = tuple(published)
        published_text = ", ".join(path for path, _ in self.published) or "nothing"
        super().__init__(f"external edit kept; store version preserved at {', '.join(self.preserved)}; "
                         f"published: {published_text}")


class CommitIncomplete(StoreError):
    """Failed after the commit point; the next recovery finishes or preserves it."""


class PendingRecovery(StoreError):
    """A journal awaits recovery, so no consistent view or new commit is possible."""


class RecoveryFailed(StoreError):
    """The journal or the library cannot be recovered safely; nothing was changed."""


class RecoveryBlocked(StoreError):
    """Recovery met a persistent I/O error; see ``LibraryStore.discard_journal``."""


class NotALibrary(StoreError):
    """The directory is not a V2 library created by this store."""


class LibraryLocked(StoreError):
    """Another writer holds the library."""


class LockUnavailable(StoreError):
    """The filesystem cannot lock; open the library read-only."""


class RecordNotFound(StoreError, LookupError):
    """No record exists at that address (by exact spelling)."""


class MalformedRecord(StoreError):
    """A file that is not a valid record; ``etag`` names its exact bytes."""

    def __init__(self, message: str, etag: str | None) -> None:
        super().__init__(message)
        self.etag = etag


class UnsupportedFormat(MalformedRecord):
    """A record in a format version this store does not write."""


class AddressMismatch(MalformedRecord):
    """A record whose ``address`` is not the one its path implies."""

    def __init__(self, message: str, etag: str | None, claimed: object) -> None:
        super().__init__(message, etag)
        self.claimed = claimed


class LinkRefused(LibraryPathError):
    """A symlink, junction or reparse point inside the library; never followed."""
