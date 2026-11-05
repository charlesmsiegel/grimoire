"""V2 native record store: human-editable JSON with revisioned, guarded writes.

A filesystem adapter, not part of the pure core. It stores facet-neutral
record envelopes for source-qualified definitions and campaign instances and
never interprets their payloads. See docs/STORAGE.md for the contract.
"""

from .diskops import DiskOps, FileOps
from .envelope import AssetRecord, KnowledgeRecord, LinkSpec, RecordEnvelope, ScopeRecord, SourceManifest
from .errors import (
    BlobCorrupt, CommitIncomplete, CrossDevice, ImmutableBlob, ImmutableImage, ImmutableRevision, ImpreciseNumber,
    InstanceIdConflict, InvalidBlob, InvalidEnvelope, InvalidSourceState, LibraryLocked,
    LinkRefused, LockUnavailable, MalformedRecord, NameCollision, NotALibrary, PendingRecovery,
    RecordNotFound, RecoveryBlocked, RecoveryFailed, RevisionReferenced, StaleWrite, StoreError,
    TakenIdsUnlisted, UnstorableIdentifier,
    UnsupportedFormat, WriteConflict, WriteDenied,
)
from .jsonfmt import etag
from .library import CommitResult, LibraryStore, RecoveryReport, SourceFacts, Transaction
from .loader import (
    BrokenRef, CampaignHoldingRecords, CampaignRecords, CampaignRelations, KnowledgeScan, LibraryView, LoadProblem,
    StoredAssetRecord, StoredDefinition,
    StoredKnowledgeRecord, StoredRecord, StoredScopeRecord, StoredSource,
)
from .media import MAX_BLOB_BYTES, MEDIA, ImageBlob, sniff
from .lock import Locker, OsLocker, ProcessLocker
from .replica import CanonicalScan

__all__ = [
    "AssetRecord", "BlobCorrupt", "ImageBlob", "ImmutableBlob", "ImmutableImage", "InvalidBlob", "MAX_BLOB_BYTES",
    "MEDIA", "SourceFacts", "StoredAssetRecord", "sniff",
    "BrokenRef", "CampaignHoldingRecords", "CampaignRecords", "CampaignRelations", "CanonicalScan",
    "CommitIncomplete", "CommitResult",
    "CrossDevice", "DiskOps", "FileOps",
    "ImmutableRevision", "ImpreciseNumber", "InstanceIdConflict", "InvalidEnvelope", "InvalidSourceState", "LibraryLocked",
    "LibraryStore",
    "KnowledgeRecord", "KnowledgeScan",
    "LibraryView", "LinkRefused", "LinkSpec", "LoadProblem", "LockUnavailable", "Locker",
    "MalformedRecord", "NameCollision", "NotALibrary", "OsLocker", "PendingRecovery", "ProcessLocker",
    "RecordEnvelope", "RecordNotFound", "RecoveryBlocked", "RecoveryFailed", "RecoveryReport",
    "RevisionReferenced", "ScopeRecord", "SourceManifest", "StaleWrite", "StoreError", "StoredDefinition",
    "StoredKnowledgeRecord", "StoredRecord", "StoredScopeRecord", "StoredSource",
    "TakenIdsUnlisted", "Transaction", "UnstorableIdentifier", "UnsupportedFormat", "WriteConflict", "WriteDenied", "etag",
]
