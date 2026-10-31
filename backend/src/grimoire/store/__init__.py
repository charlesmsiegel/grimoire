"""V2 native record store: human-editable JSON with revisioned, guarded writes.

A filesystem adapter, not part of the pure core. It stores facet-neutral
record envelopes for source-qualified definitions and campaign instances and
never interprets their payloads. See docs/STORAGE.md for the contract.
"""

from .diskops import DiskOps, FileOps
from .envelope import LinkSpec, RecordEnvelope, SourceManifest
from .errors import (
    CommitIncomplete, CrossDevice, ImmutableRevision, InvalidEnvelope, InvalidSourceState, LibraryLocked,
    LinkRefused, LockUnavailable, MalformedRecord, NameCollision, NotALibrary, PendingRecovery,
    RecordNotFound, RecoveryBlocked, RecoveryFailed, StaleWrite, StoreError, UnstorableIdentifier,
    UnsupportedFormat, WriteConflict, WriteDenied,
)
from .jsonfmt import etag
from .library import CommitResult, LibraryStore, RecoveryReport, Transaction
from .loader import BrokenRef, LibraryView, LoadProblem, StoredDefinition, StoredRecord, StoredSource
from .lock import Locker, OsLocker, ProcessLocker

__all__ = [
    "BrokenRef", "CommitIncomplete", "CommitResult", "CrossDevice", "DiskOps", "FileOps",
    "ImmutableRevision", "InvalidEnvelope", "InvalidSourceState", "LibraryLocked", "LibraryStore",
    "LibraryView", "LinkRefused", "LinkSpec", "LoadProblem", "LockUnavailable", "Locker",
    "MalformedRecord", "NameCollision", "NotALibrary", "OsLocker", "PendingRecovery", "ProcessLocker",
    "RecordEnvelope", "RecordNotFound", "RecoveryBlocked", "RecoveryFailed", "RecoveryReport",
    "SourceManifest", "StaleWrite", "StoreError", "StoredDefinition", "StoredRecord", "StoredSource",
    "Transaction", "UnstorableIdentifier", "UnsupportedFormat", "WriteConflict", "WriteDenied", "etag",
]
