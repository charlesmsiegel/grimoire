"""Store refusals and failures.

None of these is an ``OSError``: a refusal is never an I/O error a caller
should retry, and an adapter's ``except OSError`` must not swallow one. Raw
``OSError``s from the disk before a commit point propagate unchanged, because
nothing was published.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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


class ImmutableImage(StoreError):
    """An image object exists; a different picture or new metadata is a new image object."""


class ImmutableBlob(StoreError):
    """A blob already holds exactly these bytes; blobs are only created or repaired."""


class InvalidBlob(InvalidEnvelope):
    """Bytes that cannot be a blob; ``reason`` is ``empty``, ``too_large`` or ``unsupported_media``."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


class BlobCorrupt(StoreError):
    """A blob file's bytes do not hash to its name; ``actual`` is their etag (``None`` if not read)."""

    def __init__(self, sha256: str, actual: str | None) -> None:
        super().__init__(f"blob {sha256} is corrupt (actual {actual or 'unread: too large'})")
        self.sha256 = sha256
        self.actual = actual

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.sha256, self.actual))


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

    def __init__(self, preserved: Sequence[str], published: Sequence[tuple[str, str]],
                 dropped: Sequence[str] = ()) -> None:
        self.preserved = tuple(preserved)
        self.published = tuple(published)
        self.dropped = tuple(dropped)  # kept versions and sync state set aside unpublished
        published_text = ", ".join(path for path, _ in self.published) or "nothing"
        dropped_text = f"; set aside: {', '.join(self.dropped)}" if self.dropped else ""
        super().__init__(f"external edit kept; store version preserved at {', '.join(self.preserved) or 'nothing'}; "
                         f"published: {published_text}{dropped_text}")


class ImpreciseNumber(StoreError):
    """A rewrite would replace a stored number with its rounded float; nothing was written.

    ``reason`` is ``imprecise_number``; ``where`` maps each file to the JSON pointers of its numbers
    whose text a ``float`` cannot hold exactly (excess digits, underflow to zero, a huge value).
    The file still reads (rounded); fix the number by hand, or write the value it should hold."""

    reason = "imprecise_number"

    def __init__(self, where: Mapping[str, Sequence[str]]) -> None:
        self.where = {path: tuple(pointers) for path, pointers in where.items()}
        super().__init__("imprecise_number: rewriting would round "
                         + "; ".join(f"{path} at {', '.join(pointers)}" for path, pointers in self.where.items()))

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.where,))


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


class LedgerBroken(StoreError):
    """A ledger's chain is broken at ``seq``; never read partially (see ``repair_ledger``)."""

    def __init__(self, name: str, seq: int | None, reason: str) -> None:
        self.name = name
        self.seq = seq
        self.reason = reason
        super().__init__(f"ledger {name} is broken at batch {seq}: {reason}")


class LedgerBusy(StoreError):
    """Another writer owns this ledger in this store session."""


class LedgerInvalid(StoreError):
    """A well-formed ledger holds an entry the authority rules reject; nothing is trusted."""


class AuthorityUnavailable(StoreError):
    """The durable authority failed to persist and refuses everything until it is reopened."""


class RevisionReferenced(StoreError):
    """A definition revision cannot be replaced: something on this library may pin it."""


class InstanceIdConflict(StoreError):
    """A new instance file would share its id with an instance of another kind in the same campaign.

    ``reason`` is ``instance_id_conflict``; ``paths`` are the refused new files. An instance id is
    unique within a campaign across kinds (IDENTITY.md), so the commit is refused per path and
    nothing is written. Edits of an existing pair's members stay allowed so a human can repair it.
    """

    reason = "instance_id_conflict"

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = tuple(paths)
        super().__init__(f"instance id already used by another kind in its campaign: {', '.join(self.paths)}")

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.paths,))


class TakenIdsUnlisted(StoreError):
    """A file or folder that could name a campaign's instance ids cannot be listed or read, so those
    ids cannot be reserved: allocating a new instance id fails closed rather than guess.

    ``paths`` names what could not be read (store paths, never file content).
    """

    def __init__(self, message: str, paths: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.paths = tuple(paths)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.paths))
