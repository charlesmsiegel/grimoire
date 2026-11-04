"""The V2 native record store: open a library, read records, commit guarded transactions.

``LibraryStore`` is trusted application code and is never handed to a worker.
Every write is a ``Transaction`` that commits in a fixed order: nothing is
mutated until the root, links, V1 isolation, compare-and-swap preconditions,
name collisions, source consistency and authorization have all passed; the
journal replace is the commit point; publication and recovery share one
guarded decision table (see ``engine``). See ``docs/STORAGE.md``.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from grimoire.core.identity import (
    ChatRef, IdentityConflict, ImageRef, InstanceRef, KnowledgeRef, PlacementRef, RelationRef, ScopeRef, SourceRef,
    validate_segment,
)
from grimoire.core.index import DefinitionIndex, SourceDescriptor
from grimoire.library import LibraryPathError, check_write_destination
from grimoire.security import Permission, ResourcePath

from . import engine, loader
from .diskops import DiskOps, FileOps, is_link, lstat_or_none
from .engine import Access, Settlement
from .envelope import (
    AssetRecord, EnvelopeError, KnowledgeRecord, RecordEnvelope, ScopeRecord, SourceManifest, asset_record_document,
    knowledge_record_document, parse_asset_record, parse_knowledge_record, parse_record, parse_scope_record,
    parse_source, record_document, scope_record_document,
    source_document,
)
from .errors import (
    BlobCorrupt, CommitIncomplete, ImmutableBlob, ImmutableImage, ImmutableRevision, InvalidEnvelope,
    InvalidSourceState, LinkRefused, NameCollision, NotALibrary, LedgerBusy, PendingRecovery, RecordNotFound,
    RecoveryBlocked, RecoveryFailed, StaleWrite, StoreError, UnstorableIdentifier, WriteConflict, WriteDenied,
)
from .guard import Guard, Limits, RootIdentity
from .journal import Entry, Journal, is_staging_name
from .jsonfmt import JsonError, etag, is_etag, parse_strict, render
from .layout import (
    CAMPAIGNS, CONFLICTS, DISCARD_MARKER, INSTANCES, JOURNAL, LIBRARY_FORMAT, LOCK, MARKER, REPAIR_MARKER, TXN, Parts,
    IMAGES, PLACEMENTS, address_parts, blob_parts, classify, conflict_parts, definition_parts, image_parts,
    name_key,
    instance_parts, ledger_folder, ledger_name, ledger_parts, ledger_seq, logical, manifest_parts,
)
from .ledger import Head, Ledger, read_ledger, render_batches
from .loader import (
    CampaignRelations, KnowledgeScan, LibraryView, LoadProblem, StoredAssetRecord, StoredDefinition,
    StoredKnowledgeRecord, StoredRecord, StoredScopeRecord, StoredSource,
)
from .media import MAX_BLOB_BYTES, ImageBlob
from .lock import Locker, OsLocker

DISCARD_FORMAT = "grimoire.discard/1"
DISCARD_PREFIX = "discard"  # staging names the store gives a marker before it is committed
REPAIR_FORMAT = "grimoire.repair/1"
REPAIR_PREFIX = "repair"


@dataclass(frozen=True)
class RecoveryReport:
    published: tuple[tuple[str, str], ...] = ()
    preserved: tuple[str, ...] = ()
    incomplete: tuple[str, ...] = ()
    aborted: bool = False

    @classmethod
    def of(cls, settlement: Settlement) -> RecoveryReport:
        return cls(tuple(settlement.published), tuple(settlement.preserved), tuple(settlement.incomplete),
                   settlement.aborted)


@dataclass(frozen=True)
class CommitResult:
    """The new content revision of every target, by logical path."""

    etags: Mapping[str, str]


def _absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


class LibraryStore:
    """One open V2 library. Writable stores hold the library's writer lock until closed."""

    def __init__(self, access: Access, *, writable: bool, locker: Locker | None, lock: object) -> None:
        self._access = access
        self._writable = writable
        self._locker = locker
        self._lock = lock
        self._closed = False
        self.last_recovery = RecoveryReport()
        #: One re-entrant lock for every commit and for a bound authority's sink and flushes:
        #: a ledger flush from inside a commit re-enters; another thread waits its turn.
        self.lock = threading.RLock()
        self._authority: object | None = None
        #: per-source (fingerprint, slim scan), per-source (fingerprint, view) and the whole-library
        #: exclusions computed from them; see source_facts()
        self._scans: dict[str, tuple[tuple, loader.SourceScan]] = {}
        self._views: dict[str, tuple[tuple, _View]] = {}
        self._exclusions: tuple[dict[str, tuple], frozenset[str]] | None = None
        #: bumped by every commit and recovery of this store, so a caller's SourceFacts expire
        self.generation = 0
        #: inside read_pass(): the facts are verified once and reused until a commit
        self._pass_depth = 0
        self._pass_facts: SourceFacts | None = None
        self._capability: object | None = None
        self._writers: dict[str, LedgerWriter] = {}
        self._staging = False  # a record commit has staged temps or a journal

    # -- opening -------------------------------------------------------------
    @classmethod
    def create(cls, path: str | os.PathLike[str], *, ops: FileOps | None = None, locker: Locker | None = None,
               limits: Limits | None = None) -> LibraryStore:
        """Create a library at ``path``; its parent must exist. Retryable after a crash."""
        ops = ops or DiskOps()
        locker = locker or OsLocker()
        root = check_write_destination(_absolute(path))  # resolved: no link remains in it
        if not os.path.isdir(root.parent):
            raise LibraryPathError(f"{root.parent} must be an existing directory")
        info = lstat_or_none(root)
        if info is None:
            ops.make_dirs(root)  # the final component only: its parent exists
        elif not stat.S_ISDIR(info.st_mode):
            raise LibraryPathError(f"{root} exists and is not a directory")
        else:
            cls._require_restartable(ops, root)
        check_write_destination(root)  # the post-creation re-check
        access = Access(root, ops, Guard(root, RootIdentity.capture(root), limits))
        lock = cls._lock(access, locker)
        try:
            engine.clean_staging(access)
            temp = ops.make_temp(root / TXN, prefix="marker", suffix=".new")
            ops.write_bytes(temp, render({"format": LIBRARY_FORMAT}))
            ops.replace(temp, root / MARKER)
        except BaseException:
            locker.release(lock)
            raise
        return cls(access, writable=True, locker=locker, lock=lock)

    @staticmethod
    def _lock(access: Access, locker: Locker) -> object:
        """Make sure ``.txn`` and its lock file are real entries of this library, then lock."""
        access.check_staging()
        if access.presence((TXN,)) != "present":
            access.ops.make_dirs(access.path((TXN,)))
        if access.presence((TXN, LOCK)) != "present":
            access.ops.write_bytes(access.path((TXN, LOCK)), b"")
        return locker.acquire(access.path((TXN, LOCK)))

    @staticmethod
    def _require_restartable(ops: FileOps, root: Path) -> None:
        names = ops.list_dir(root)
        if not names:
            return
        if names == [TXN] and is_link(root / TXN):  # never list a linked .txn
            raise LinkRefused(f"refusing to follow a link inside the library: {root / TXN}")
        if names == [TXN] and all(name == LOCK or is_staging_name(name) for name in ops.list_dir(root / TXN)):
            return  # an interrupted create
        raise LibraryPathError(f"{root} is not empty; refusing to create a library there")

    @classmethod
    def open(cls, path: str | os.PathLike[str], *, ops: FileOps | None = None, locker: Locker | None = None,
             writable: bool = True, limits: Limits | None = None) -> LibraryStore:
        """Open an existing library; a writable open takes the writer lock and recovers."""
        ops = ops or DiskOps()
        root = check_write_destination(_absolute(path))
        access = Access(root, ops, Guard(root, RootIdentity.capture(root), limits))
        cls._require_marker(access)
        if not writable:
            return cls(access, writable=False, locker=None, lock=None)
        locker = locker or OsLocker()
        lock = cls._lock(access, locker)
        store = cls(access, writable=True, locker=locker, lock=lock)
        try:
            store.recover()
        except BaseException:
            store.close()
            raise
        return store

    @staticmethod
    def _require_marker(access: Access) -> None:
        data = access.read((MARKER,))
        try:
            document = parse_strict(data) if data is not None else None
        except JsonError:
            document = None
        if document is None or document.get("format") != LIBRARY_FORMAT:
            raise NotALibrary(f"{access.root} is not a V2 library ({MARKER} missing or unreadable)")

    @classmethod
    def discard_journal(cls, path: str | os.PathLike[str], *, ops: FileOps | None = None,
                        locker: Locker | None = None) -> tuple[str, ...]:
        """A human's explicit way out of ``RecoveryBlocked`` or an unreadable journal.

        Takes the writer lock and, without opening or interpreting the journal (it may
        be a FIFO or garbage), moves it and every staged file into the first unused
        ``.conflicts/discarded-<N>/``, so an earlier discard is never overwritten.
        The intent is made durable first (``.txn/discard.json`` names the folder):
        until the discard finishes, recovery refuses to publish anything, and running
        this again resumes into the same folder. Anything already published stays
        published, and ``load`` reports what is inconsistent.
        """
        ops = ops or DiskOps()
        locker = locker or OsLocker()
        root = check_write_destination(_absolute(path))
        access = Access(root, ops, Guard(root, RootIdentity.capture(root)))
        cls._require_marker(access)
        lock = cls._lock(access, locker)
        try:
            access.guard.check_session()
            if access.presence((CONFLICTS,)) == "mismatch":  # renaming the child can never resolve it
                raise NameCollision(f"{CONFLICTS} collides with a differently spelled folder")
            marker = root / TXN / DISCARD_MARKER
            if access.discard_pending():  # resume an interrupted discard into its own folder
                folder = cls._discard_folder(access, marker)
            elif access.journal_exists():
                folder = cls._new_discard_folder(access)
                access.guard.check_write(folder + ("x",), staging_device=None)
                temp = ops.make_temp(root / TXN, prefix=DISCARD_PREFIX, suffix=".new")
                ops.write_bytes(temp, render({"format": DISCARD_FORMAT, "folder": folder[-1]}))
                ops.replace(temp, marker)  # durable intent: from here on nothing publishes
            else:
                return ()
            access.guard.check_write(folder + ("x",), staging_device=None)
            if access.presence(folder) != "present":
                ops.make_dirs(access.path(folder))
            moved = []
            for name in ops.list_dir(root / TXN):
                if name.startswith(DISCARD_PREFIX) and is_staging_name(name):
                    ops.remove(root / TXN / name)  # a marker that was never committed
                elif name == JOURNAL or is_staging_name(name):
                    ops.replace(root / TXN / name, access.path(folder + (name,)))
                    moved.append("/".join(folder + (name,)))
            ops.remove(marker)
            return tuple(moved)
        finally:
            locker.release(lock)

    @staticmethod
    def _new_discard_folder(access: Access) -> Parts:
        number = 1
        while access.presence((CONFLICTS, f"discarded-{number}")) != "absent":
            number += 1  # a look-alike child only takes that number
            if number > 10_000:
                raise StoreError("no free discard folder name")
        return (CONFLICTS, f"discarded-{number}")

    @staticmethod
    def _discard_folder(access: Access, marker: Path) -> Parts:
        """The folder an interrupted discard chose; the marker is validated, never trusted."""
        access.check_staged_file(DISCARD_MARKER)
        try:
            document = parse_strict(access.ops.read_bytes(marker))
        except JsonError as error:
            raise RecoveryFailed(f"the discard marker is unreadable: {error}") from None
        folder = document.get("folder")
        if (document.get("format") != DISCARD_FORMAT or type(folder) is not str
                or not re.fullmatch(r"discarded-[1-9][0-9]{0,8}", folder)):
            raise RecoveryFailed("the discard marker does not name a discard folder")
        return (CONFLICTS, folder)

    @classmethod
    def repair_ledger(cls, path: str | os.PathLike[str], name: str, from_seq: int, *,
                      ops: FileOps | None = None, locker: Locker | None = None) -> tuple[str, ...]:
        """A human's explicit way out of a broken ledger: move batches ``>= from_seq`` aside.

        **Warning:** revocations, consumed approvals and other history in the moved
        batches are undone; ``from_seq=1`` resets the ledger (the next authority open
        writes a fresh genesis). The intent is recorded first (``.txn/repair.json``);
        until the repair finishes nothing reads or recovers, and running it again
        resumes into the same folder. Batches move newest first.
        """
        ops = ops or DiskOps()
        locker = locker or OsLocker()
        name = ledger_name(name)
        if type(from_seq) is not int or from_seq < 1:
            raise ValueError("from_seq must be a positive int")
        root = check_write_destination(_absolute(path))
        access = Access(root, ops, Guard(root, RootIdentity.capture(root)))
        cls._require_marker(access)
        lock = cls._lock(access, locker)
        try:
            access.guard.check_session()
            if access.journal_exists() or access.discard_pending():
                raise PendingRecovery("finish the pending commit or discard before repairing a ledger")
            if access.presence((CONFLICTS,)) == "mismatch":
                raise NameCollision(f"{CONFLICTS} collides with a differently spelled folder")
            marker = root / TXN / REPAIR_MARKER
            batches = ledger_folder(name)
            if access.repair_pending():
                folder = cls._repair_folder(access, marker, name, from_seq)
            elif access.presence(batches) != "present" or not any(
                    (seq := ledger_seq(file)) is not None and seq >= from_seq for file in access.list_dir(batches)):
                return ()  # nothing to move
            else:
                flat = "ledger-" + name.replace("/", "-")
                number = 1
                while access.presence((CONFLICTS, f"{flat}-{number}")) != "absent":
                    number += 1
                    if number > 10_000:
                        raise StoreError("no free repair folder name")
                folder = (CONFLICTS, f"{flat}-{number}")
                access.guard.check_write(folder + ("x",), staging_device=None)
                temp = ops.make_temp(root / TXN, prefix=REPAIR_PREFIX, suffix=".new")
                ops.write_bytes(temp, render({"format": REPAIR_FORMAT, "ledger": name, "from_seq": from_seq,
                                              "folder": folder[-1]}))
                ops.replace(temp, marker)  # durable intent: the ledger is undefined until this finishes
            access.guard.check_write(folder + ("x",), staging_device=None)
            if access.presence(folder) != "present":
                ops.make_dirs(access.path(folder))
            seqs = []
            if access.presence(batches) == "present":
                seqs = sorted((seq for file in access.list_dir(batches)
                               if (seq := ledger_seq(file)) is not None and seq >= from_seq), reverse=True)
            moved = []
            for seq in seqs:  # newest first: an interruption leaves a shorter valid prefix
                parts = ledger_parts(name, seq)
                access.guard.check_read(parts)
                ops.replace(access.path(parts), access.path(folder + (parts[-1],)))
                moved.append("/".join(folder + (parts[-1],)))
            for leftover in ops.list_dir(root / TXN):
                if leftover.startswith(REPAIR_PREFIX) and is_staging_name(leftover):
                    ops.remove(root / TXN / leftover)
            ops.remove(marker)
            return tuple(moved)
        finally:
            locker.release(lock)

    @staticmethod
    def _repair_folder(access: Access, marker: Path, name: str, from_seq: int) -> Parts:
        access.check_staged_file(REPAIR_MARKER)
        try:
            document = parse_strict(access.ops.read_bytes(marker))
        except JsonError as error:
            raise RecoveryFailed(f"the repair marker is unreadable: {error}") from None
        folder = document.get("folder")
        if (document.get("format") != REPAIR_FORMAT or type(folder) is not str
                or not re.fullmatch(r"ledger-[a-z0-9._-]+-[1-9][0-9]{0,8}", folder)):
            raise RecoveryFailed("the repair marker does not name a repair folder")
        if document.get("ledger") != name or document.get("from_seq") != from_seq:
            raise StoreError(f"an unfinished repair of {document.get('ledger')} from "
                             f"{document.get('from_seq')} must be finished with the same arguments")
        return (CONFLICTS, folder)

    # -- lifecycle ------------------------------------------------------------
    def close(self) -> None:
        """Release the writer lock. Always succeeds, even after a crash (as process death would)."""
        if not self._closed and self._locker is not None:
            self._closed = True
            self._locker.release(self._lock)
        self._closed = True

    def __enter__(self) -> LibraryStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def root(self) -> Path:
        return self._access.root

    @property
    def writable(self) -> bool:
        return self._writable

    def _require_open(self) -> None:
        if self._closed:
            raise StoreError("the library store is closed")

    def _require_consistent(self) -> None:
        self._require_open()
        self._access.guard.check_root()  # a swapped root fails the read, not each source in it
        if self._access.journal_exists() or self._access.discard_pending() or self._access.repair_pending():
            raise PendingRecovery("a commit, discard or ledger repair awaits recovery; "
                                  "reopen the library writable to finish it")

    # -- ledgers and the durable authority ------------------------------------
    @property
    def authority(self) -> object | None:
        return self._authority

    def bind_authority(self, authority: object) -> object:
        """Bind the library's durable authority for the rest of this session.

        Record commits then require handles it issued and authorize inside its
        consequential scope; only the returned capability can obtain ledger
        writers. Permanent; refused while any ledger writer is outstanding.
        """
        self._require_open()
        with self.lock:
            if self._authority is not None:
                raise StoreError("this store is already bound to an authority")
            if self._writers:
                raise StoreError("a ledger writer is outstanding; bind before taking writers")
            for method in ("issued", "consequential_scope"):
                if not callable(getattr(authority, method, None)):
                    raise TypeError(f"an authority needs {method}()")
            self._authority, self._capability = authority, object()
            return self._capability

    def read_ledger(self, name: str) -> Ledger:
        with self.lock:  # never overlaps this process's commits
            self._require_consistent()
            return read_ledger(self._access, name)

    def ledger_writer(self, name: str, *, capability: object | None = None) -> LedgerWriter:
        """The only writer of ledger ``name`` for this session (trusted code only)."""
        self._require_open()
        if not self._writable:
            raise StoreError("the library was opened read-only")
        name = ledger_name(name)
        with self.lock:
            if self._capability is not None and capability is not self._capability:
                raise StoreError("ledgers of a bound library are written only through its authority")
            if name in self._writers:
                raise LedgerBusy(f"ledger {name} already has a writer in this session")
            writer = LedgerWriter(self, name, self.read_ledger(name).head, capability)
            self._writers[name] = writer
            return writer

    def release_ledger_writers(self, capability: object) -> None:
        with self.lock:
            if capability is self._capability:
                self._writers.clear()

    def recover(self) -> RecoveryReport:
        """Finish or preserve an interrupted commit. Persistent I/O errors are ``RecoveryBlocked``."""
        self._require_open()
        if not self._writable:
            raise StoreError("a read-only store cannot recover")
        with self.lock:  # waits for this process's commits; it would clean their staging
            try:
                report = RecoveryReport.of(engine.recover(self._access))
            except OSError as error:
                raise RecoveryBlocked(f"recovery could not finish: {error}") from error
            finally:
                self.generation += 1
                self._pass_facts = None
            self.last_recovery = report
            return report

    # -- reading -----------------------------------------------------------------
    def read(self, ref: SourceRef | InstanceRef | ScopeRef | ChatRef | RelationRef | KnowledgeRef | ImageRef
             | PlacementRef) -> StoredRecord | StoredScopeRecord | StoredKnowledgeRecord | StoredAssetRecord:
        """One record by exact address (a definition must be pinned)."""
        if isinstance(ref, SourceRef) and not ref.pinned:
            raise ValueError("read a pinned revision; resolve unpinned references with load().index")
        with self.lock:  # reads wait for this process's commits, so they never see one half-done
            self._require_consistent()
            if isinstance(ref, (ScopeRef, ChatRef, RelationRef)):
                return loader.read_scope_record(self._access, address_parts(ref))
            if isinstance(ref, KnowledgeRef):
                return loader.read_knowledge_record(self._access, address_parts(ref))
            if isinstance(ref, (ImageRef, PlacementRef)):
                return loader.read_asset_record(self._access, address_parts(ref))
            return loader.read_record(self._access, address_parts(ref))

    # -- images and blobs (018) ---------------------------------------------------
    def campaign_exists(self, campaign_id: str) -> bool:
        """A canonical ``scope.json`` or at least one canonical instance file exists (018 §11.4)."""
        validate_segment(campaign_id, "campaign_id")
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                return loader.campaign_exists(self._access, campaign_id)

    def _blob_info(self, sha256: str, ext: str) -> tuple[Parts, str, os.stat_result | None]:
        parts = blob_parts(sha256, ext)
        presence = self._access.presence(parts)
        if presence != "present":
            return parts, "lookalike" if presence == "mismatch" else "absent", None
        self._access.guard.check_read(parts)  # LinkRefused for a linked ancestor or the file itself
        info = lstat_or_none(self._access.path(parts))
        if info is None:
            return parts, "absent", None
        if not stat.S_ISREG(info.st_mode):
            raise UnstorableIdentifier(f"{'/'.join(parts)} is not a regular file")
        return parts, "present", info

    def blob_stat(self, sha256: str, ext: str) -> tuple[str, int | None]:
        """``(presence, size)``: presence is ``absent``, ``present`` or ``lookalike`` (another spelling).

        Never reads the bytes; a link raises ``LinkRefused`` and a non-regular file ``UnstorableIdentifier``.
        """
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                _, presence, info = self._blob_info(sha256, ext)
        return presence, None if info is None else info.st_size

    def blob_etag(self, sha256: str, ext: str) -> str | None:
        """The etag of the blob file's current bytes, or ``None`` if it is absent.

        A file over ``MAX_BLOB_BYTES`` is corrupt by definition and raises ``BlobCorrupt`` without
        being read; a look-alike spelling raises ``NameCollision``.
        """
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                parts, presence, info = self._blob_info(sha256, ext)
            if presence == "lookalike":
                raise NameCollision(f"{'/'.join(parts)} collides with a differently spelled existing entry")
            if info is None:
                return None
            if info.st_size > MAX_BLOB_BYTES:
                raise BlobCorrupt(sha256, None)
            data = self._access.read(parts)
        return None if data is None else etag(data)

    def read_blob(self, sha256: str, ext: str) -> bytes:
        """The blob's exact bytes, verified against its name; ``BlobCorrupt`` otherwise."""
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                parts, presence, info = self._blob_info(sha256, ext)
            if info is None:
                raise RecordNotFound("/".join(parts))
            if info.st_size > MAX_BLOB_BYTES:
                raise BlobCorrupt(sha256, None)
            data = self._access.read(parts)
        if data is None:
            raise RecordNotFound("/".join(parts))
        if len(data) > MAX_BLOB_BYTES:
            raise BlobCorrupt(sha256, None)
        if etag(data) != "sha256:" + sha256:
            raise BlobCorrupt(sha256, etag(data))
        return data

    def list_images(self, scope: str) -> tuple[tuple[ImageRef, ...], tuple[LoadProblem, ...]]:
        """Canonical image files of one source or ``campaign:<id>``, plus problems; contents are not read."""
        folder = image_parts(ImageRef(scope, "x"))[:-1]
        return self._list_assets(folder, "image", ImageRef(scope, "x").source)  # type: ignore[return-value]

    def list_placements(self, subject: SourceRef | InstanceRef,
                        ) -> tuple[tuple[PlacementRef, ...], tuple[LoadProblem, ...]]:
        """Canonical placement files of one subject, plus problems; contents are not read."""
        folder = address_parts(PlacementRef(subject, "x"))[:-1]
        source = subject.source if isinstance(subject, SourceRef) else None
        return self._list_assets(folder, "placement", source)  # type: ignore[return-value]

    def _list_assets(self, folder: Parts, kind: str, source: str | None) -> tuple[tuple, tuple[LoadProblem, ...]]:
        problems: list[LoadProblem] = []
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                try:
                    for depth in range(1, len(folder) + 1):  # a file where a folder belongs is damage
                        info = lstat_or_none(self._access.path(folder[:depth]))
                        if info is None:
                            break
                        if not stat.S_ISDIR(info.st_mode) and not is_link(self._access.path(folder[:depth])):
                            return (), (LoadProblem("/".join(folder[:depth]), "unrecognized",
                                                    "a file where a folder belongs", source),)
                    presence = self._access.presence(folder)
                    if presence == "mismatch":  # only a differently spelled folder: never silent
                        return (), (LoadProblem("/".join(folder), "name_mismatch",
                                                "only a differently spelled folder exists", source),)
                    if presence != "present":
                        return (), ()
                    self._access.guard.check_read(folder)
                    problems.extend(self._lookalike_siblings(folder, source))
                    found = loader.walk_assets(self._access, folder, 0, kind, problems, source)
                except LinkRefused as error:
                    return (), (LoadProblem("/".join(folder), "link", str(error), source),)
                except OSError as error:
                    return (), (LoadProblem("/".join(folder), "unreadable", f"cannot list: {error}", source),)
        return tuple(sorted(found, key=str)), tuple(problems)

    def _lookalike_siblings(self, folder: Parts, source: str | None) -> list[LoadProblem]:
        """Differently spelled siblings of the asset folders on the way down (``Wren-b`` beside
        ``wren-b``): they may hold records under another spelling, so they are reported."""
        found = []
        top = next((at for at, part in enumerate(folder) if part in (IMAGES, PLACEMENTS)), len(folder))
        for depth in range(top + 1, len(folder) + 1):
            parent, name = folder[:depth - 1], folder[depth - 1]
            key = name_key(name)
            for other in self._access.list_dir(parent):
                if other != name and name_key(other) == key:
                    found.append(LoadProblem("/".join(parent + (other,)), "name_mismatch",
                                             "a differently spelled folder beside the canonical one", source))
        return found

    def campaign_presence(self, campaign_id: str) -> str:
        """``absent``, ``present`` or ``mismatch`` (only a look-alike folder) for ``campaigns/<id>``."""
        validate_segment(campaign_id, "campaign_id")
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                return self._access.presence((CAMPAIGNS, campaign_id))

    def campaign_instances(self, campaign_id: str) -> tuple[InstanceRef, ...]:
        """Every canonically named instance file of one campaign, across kinds; reads no content."""
        validate_segment(campaign_id, "campaign_id")
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                return loader.campaign_instances(self._access, campaign_id)

    def campaign_relations(self, campaign_id: str) -> CampaignRelations:
        """One campaign's relation records (017) and their problems, as ``load()`` reports them."""
        validate_segment(campaign_id, "campaign_id")
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                return loader.campaign_relations(self._access, campaign_id)

    def campaign_knowledge(self, campaign_id: str) -> KnowledgeScan:
        """One campaign's events/evidence files as found (020): director-level, store-internal.

        Never interpreted here: ``grimoire.evidencestore`` assembles and projects them. No graph,
        index or retrieval builder may read this; they consume actor projections only.
        """
        validate_segment(campaign_id, "campaign_id")
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                return loader.campaign_knowledge(self._access, campaign_id)

    def source_facts(self) -> SourceFacts:
        """One verified answer to "which sources would ``load()`` exclude?", reusable by a caller.

        Incremental and cached: each source is scanned again only when a stat-only fingerprint of
        its manifest and revision files changed (art and campaign writes never count), and
        ``load()``'s own exclusion algorithm (``loader.admit``) then runs in memory over the
        cached scans. An unchanged library costs one stat per manifest and revision entry, and
        inside ``read_pass()`` only once per pass. A scan that failed with an I/O error is never
        cached, so a transient error affects one call only.

        Pass the result to ``check_source``/``check_definition`` to validate many references
        against one verification. It expires with ``generation`` (any commit or recovery of this
        store); an edit made by another program after it was taken is not seen until then.
        """
        with self.lock:
            self._require_consistent()
            if self._pass_depth and self._pass_facts is not None and self._pass_facts.generation == self.generation:
                return self._pass_facts
            with self._access.cached_listings():
                ids = loader.discover_sources(self._access)
                prints = {source_id: loader.source_fingerprint(self._access, source_id) for source_id in ids}
                if self._exclusions is not None and self._exclusions[0] == prints:
                    excluded = self._exclusions[1]
                else:
                    scans = {}
                    for source_id in ids:
                        cached = self._scans.get(source_id)
                        scans[source_id] = (cached[1] if cached is not None and cached[0] == prints[source_id]
                                            else loader.slim(loader.contained_scan(self._access, source_id)))
                    self._scans = {source_id: (prints[source_id], scan) for source_id, scan in scans.items()
                                   if not loader.transient(scan)}
                    excluded = frozenset(loader.admit(scans, []).reported)
                    transient = any(loader.transient(scan) for scan in scans.values())
                    self._exclusions = None if transient else (prints, excluded)
                    self._views = {source_id: entry for source_id, entry in self._views.items()
                                   if source_id in self._scans and entry[0] == self._scans[source_id][0]}
            facts = SourceFacts(self.generation, MappingProxyType(dict(prints)), excluded)
            if self._pass_depth:
                self._pass_facts = facts
            return facts

    def excluded_sources(self) -> frozenset[str]:
        """Every source ``load()`` would report as excluded, including by a cross-source
        ``identity_conflict`` that no one-source scan can see (see ``source_facts``)."""
        return self.source_facts().excluded

    @contextlib.contextmanager
    def read_pass(self) -> Iterator[None]:
        """Hold the store lock and verify ``source_facts`` once for the whole block.

        For a service validating many references at once; a commit inside the block makes the
        next check verify again.
        """
        with self.lock:
            self._pass_depth += 1
            try:
                yield
            finally:
                self._pass_depth -= 1
                if not self._pass_depth:
                    self._pass_facts = None

    def _current(self, facts: SourceFacts | None) -> SourceFacts:
        return facts if facts is not None and facts.generation == self.generation else self.source_facts()

    def _view(self, source_id: str, facts: SourceFacts) -> _View:
        """One source's admission facts, derived once per fingerprint from its cached scan."""
        fingerprint = facts.prints.get(source_id)
        cached = self._views.get(source_id)
        if fingerprint is not None and cached is not None and cached[0] == fingerprint:
            if self._manifest_unchanged(source_id, cached[1].stored):
                return cached[1]
            self._forget(source_id)  # edited without changing its stat (a restore, coarse times)
        entry = self._scans.get(source_id)
        if fingerprint is not None and entry is not None and entry[0] == fingerprint \
                and self._manifest_unchanged(source_id, entry[1].stored):
            scan = entry[1]
        else:  # not a discovered source (missing), or its scan was transient: scan it now
            with self._access.cached_listings():
                scan = loader.slim(loader.contained_scan(self._access, source_id))
        verdict, index, dropped = _scan_verdict(scan), None, frozenset()
        if verdict is None:
            definitions, heads, gone, _ = loader.evaluate(scan)
            dropped = frozenset(gone)
            manifest = scan.stored.manifest  # type: ignore[union-attr]
            try:
                index = DefinitionIndex([SourceDescriptor(source_id, manifest.owner, manifest.title)],
                                        definitions, heads)
            except IdentityConflict:
                index = None
        view = _View(verdict, scan.stored, index, dropped)
        if fingerprint is not None and not loader.transient(scan):
            self._views[source_id] = (fingerprint, view)
        return view

    def _manifest_unchanged(self, source_id: str, stored: StoredSource | None) -> bool:
        """One small read: the manifest (heads, links, owner) still has the bytes the view used."""
        if stored is None:
            return True  # absence is in the fingerprint
        try:
            return self._access.etag_of(manifest_parts(source_id)) == stored.etag
        except (StoreError, ValueError, OSError):
            return False

    def _forget(self, source_id: str) -> None:
        self._views.pop(source_id, None)
        self._scans.pop(source_id, None)
        self._exclusions = None

    def check_source(self, source_id: str, facts: SourceFacts | None = None) -> StoredSource | str:
        """One source as ``load()`` would admit it, or why not (``missing_source``/``excluded_source``).

        Agrees with ``load()``, including whole-library exclusions. While the source and ``facts``
        (default: verified now, see ``source_facts``) are unchanged it parses nothing: it reads only
        the manifest, to compare its etag.
        """
        with self.lock:
            self._require_consistent()
            facts = self._current(facts)
            view = self._view(source_id, facts)
            verdict = view.verdict or ("excluded_source" if source_id in facts.excluded else None)
        return verdict if verdict is not None else view.stored  # type: ignore[return-value]

    def check_definition(self, ref: SourceRef, facts: SourceFacts | None = None, *,
                         _retried: bool = False) -> StoredDefinition | str:
        """The definition ``ref`` resolves to under ``load()``'s rules, or a reason.

        Reasons: ``missing_source``, ``excluded_source``, ``entity_dropped``, ``missing_entity``,
        ``missing_revision``. An unpinned ref follows the declared head, or the only revision.
        While the source and ``facts`` are unchanged it parses nothing but reads the manifest (an
        etag check) and the one resolved revision file.
        """
        with self.lock:
            self._require_consistent()
            facts = self._current(facts)
            view = self._view(ref.source, facts)
            if view.verdict is not None:
                return view.verdict
            if ref.source in facts.excluded or view.index is None:
                return "excluded_source"
            if (ref.kind, ref.entity_id) in view.dropped:
                return "entity_dropped"
            broken = view.index.check(ref)
            if broken is not None:
                return broken.reason
            found = view.index.resolve(ref)
            try:
                stored = loader.read_record(self._access, definition_parts(found.ref))  # type: ignore[attr-defined]
            except (StoreError, ValueError, OSError):
                stored = None
            if stored is None or stored.etag != found.etag:  # type: ignore[attr-defined]
                # changed (or unreadable) since it was scanned: forget this source, decide afresh once
                self._forget(ref.source)
                if _retried:
                    return "missing_revision" if stored is None else "excluded_source"
                return self.check_definition(ref, self.source_facts(), _retried=True)
            return StoredDefinition(found.ref, found.name, stored.envelope, stored.etag)  # type: ignore[attr-defined]

    def read_source(self, source_id: str) -> StoredSource:
        with self.lock:
            self._require_consistent()
            return loader.read_source(self._access, source_id)

    def load(self) -> LibraryView:
        with self.lock:
            self._require_consistent()
            return loader.load(self._access)

    def conflicts(self) -> tuple[str, ...]:
        """Preserved versions awaiting a human, as root-relative paths (files only)."""
        self._require_open()
        found, stack = [], [(CONFLICTS,)]
        while stack:
            parts = stack.pop()
            if self._access.presence(parts) != "present":
                continue
            self._access.guard.check_read(parts)  # never list a linked .conflicts (LinkRefused)
            for name in self._access.ops.list_dir(self._access.path(parts)):
                child = parts + (name,)
                info = lstat_or_none(self._access.path(child))
                if info is not None and stat.S_ISDIR(info.st_mode) and not is_link(self._access.path(child)):
                    stack.append(child)
                else:
                    found.append("/".join(child))
        return tuple(sorted(found))

    # -- writing -----------------------------------------------------------------
    def transaction(self) -> Transaction:
        self._require_open()
        if not self._writable:
            raise StoreError("the library was opened read-only")
        return Transaction(self)


@dataclass(frozen=True)
class SourceFacts:
    """One verified answer to which sources ``load()`` excludes (``LibraryStore.source_facts``)."""

    generation: int
    prints: Mapping[str, tuple]
    excluded: frozenset[str]


@dataclass(frozen=True)
class _View:
    """One source's admission facts, from its slim scan: never holds record payloads."""

    verdict: str | None  # missing_source / excluded_source from its own scan
    stored: StoredSource | None
    index: DefinitionIndex | None  # None: an identity conflict inside the source
    dropped: frozenset


def _scan_verdict(scan: loader.SourceScan) -> str | None:
    """Why ``load()`` would not admit this source, or ``None``."""
    if scan.excluded_by:
        return "excluded_source"
    if scan.stored is None:
        return "excluded_source" if scan.revisions else "missing_source"
    return None


@dataclass(frozen=True)
class _Put:
    parts: Parts
    address: ResourcePath
    data: bytes
    expected: str | None
    envelope: RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord | AssetRecord | ImageBlob | None  # None: a ledger batch

    @property
    def ledger(self) -> bool:
        return self.envelope is None


class LedgerWriter:
    """Appends chained batches to one ledger; obtained only from ``LibraryStore.ledger_writer``."""

    def __init__(self, store: LibraryStore, name: str, head: Head | None, capability: object | None) -> None:
        self._store = store
        self.name = name
        self.head = head
        self._capability = capability

    def append(self, entries: Sequence[Mapping[str, object]], expected_head: Head | None) -> Head:
        """Write ``entries`` after ``expected_head`` in one transaction; ``StaleWrite`` if it moved."""
        store = self._store
        store._require_open()
        if not entries:
            raise ValueError("nothing to append")
        with store.lock:
            if store._capability is not None and self._capability is not store._capability:
                raise StoreError("ledgers of a bound library are written only through its authority")
            if store._writers.get(self.name) is not self:
                raise LedgerBusy(f"this writer for {self.name} was released")
            if store._staging:
                raise StoreError("a ledger cannot be written while a record commit is staging")
            if store._access._listings is not None:
                raise StoreError("a ledger cannot be written during a read-only phase")
            self._check_head(expected_head)
            puts = []
            for parts, data in render_batches(self.name, expected_head, entries):
                found = classify(parts)
                if found is None or found[0] != "ledger" or found[1][0] != self.name:
                    raise StoreError(f"{'/'.join(parts)} is not a batch of ledger {self.name}")
                puts.append(_Put(parts, logical(parts), data, None, None))
            _Commit(store, tuple(puts), None, {}).run()
            self.head = (int(puts[-1].parts[-1][:10]), etag(puts[-1].data))
            return self.head

    def _check_head(self, expected: Head | None) -> None:
        """O(1): the expected newest batch is on disk unchanged and nothing follows it."""
        access = self._store._access
        seq = expected[0] if expected else 0
        if expected is not None:
            data = access.read(ledger_parts(self.name, seq))
            if data is None or etag(data) != expected[1]:
                raise StaleWrite([("/".join(ledger_parts(self.name, seq)), "changed")])
        following = ledger_parts(self.name, seq + 1)
        if access.presence(following) != "absent":
            raise StaleWrite([("/".join(following), "exists")])


class Transaction:
    """Records to write together, each with the content revision it expects to replace."""

    def __init__(self, store: LibraryStore) -> None:
        self._store = store
        self._puts: dict[Parts, _Put] = {}
        self._committed = False

    def put(self, envelope: RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord | AssetRecord | ImageBlob,
            expected: str | None) -> str:
        """Stage a write; ``expected`` is the etag read, or ``None`` for "must not exist yet"."""
        if self._committed:
            raise StoreError("this transaction was already committed")
        if expected is not None and not is_etag(expected):
            raise InvalidEnvelope("expected must be None or an etag")
        if isinstance(envelope, SourceManifest):
            envelope.check_writable()
            parts = manifest_parts(envelope.source)
            document = source_document(envelope, expected)
        elif isinstance(envelope, RecordEnvelope):
            envelope.check_writable()
            if envelope.is_definition and expected is not None:
                raise ImmutableRevision(f"{envelope.address} exists; add a new revision and move the head")
            parts = address_parts(envelope.address)
            document = record_document(envelope, expected)
        elif isinstance(envelope, ScopeRecord):
            envelope.check_writable()
            parts = address_parts(envelope.address)
            document = scope_record_document(envelope, expected)
        elif isinstance(envelope, KnowledgeRecord):
            if expected is not None:
                raise ImmutableRevision(f"{envelope.address} is create-only; correct it by appending")
            envelope.check_writable()
            parts = address_parts(envelope.address)
            document = knowledge_record_document(envelope)
        elif isinstance(envelope, AssetRecord):
            envelope.check_writable()
            if envelope.is_image and expected is not None:
                raise ImmutableImage(f"{envelope.address} exists; a different picture is a new image object")
            parts = address_parts(envelope.address)
            document = asset_record_document(envelope, expected)
        elif isinstance(envelope, ImageBlob):
            if expected == envelope.etag:
                raise ImmutableBlob(f"blob {envelope.sha256} already holds exactly these bytes")
            # None creates; any other etag repairs a file whose bytes no longer hash to its name (CAS)
            parts = blob_parts(envelope.sha256, envelope.ext)
            document = None
        else:
            raise InvalidEnvelope("put takes a RecordEnvelope, SourceManifest, ScopeRecord, KnowledgeRecord, AssetRecord "
                                  "or ImageBlob")
        if parts in self._puts:
            raise InvalidEnvelope(f"{'/'.join(parts)} is written twice in one transaction")
        address = logical(parts)
        data = envelope.data if isinstance(envelope, ImageBlob) else render(document)  # type: ignore[arg-type]
        self._puts[parts] = _Put(parts, address, data, expected, envelope)
        return str(address)

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each target will hold, keyed by logical path: what a human approves."""
        return {str(put.address): put.data for put in self._puts.values()}

    def commit(self, handle: object, approvals: Mapping[str, str] | None = None) -> CommitResult:
        if self._committed:
            raise StoreError("this transaction was already committed")
        if not self._puts:
            raise InvalidEnvelope("nothing to commit")
        store = self._store
        with store.lock:  # the binding cannot change between these checks and the commit
            authority = store._authority
            if authority is not None and not authority.issued(handle):  # type: ignore[attr-defined]
                raise WriteDenied("the handle was not issued by this library's authority; nothing was written",
                                  (), ())
            if authority is None and store._access.presence(ledger_parts("security", 1)) != "absent":
                raise StoreError("this library has a security ledger: open its durable authority before writing")
            self._committed = True
            try:
                return _Commit(store, tuple(self._puts.values()), handle, dict(approvals or {})).run()
            finally:
                store.generation += 1  # a commit may change admission: callers' facts expire
                store._pass_facts = None


class _Commit:
    def __init__(self, store: LibraryStore, puts: tuple[_Put, ...], handle: object,
                 approvals: dict[str, str]) -> None:
        self.store = store
        self.access: Access = store._access
        self.puts = puts
        self.handle = handle
        self.approvals = approvals
        self.consumed: list[str] = []

    def run(self) -> CommitResult:
        with self.store.lock:
            ledger = {put.ledger for put in self.puts}
            if len(ledger) != 1:
                raise InvalidEnvelope("a commit writes either ledger batches or records, never both")
            outer, self.store._staging = self.store._staging, self.store._staging
            try:
                return self._run(ledger.pop())
            finally:
                self.store._staging = outer

    def _run(self, ledger: bool) -> CommitResult:
        self.store._require_consistent()                 # 1. no pending journal
        with self.access.cached_listings():               # steps 3-6 only read
            staging = self._check_paths()                 # 3. root, links, V1, preflight
            self._check_revisions()                       # 4. compare-and-swap
            self._check_names()                           # 5. collisions
            if not ledger:
                self._check_sources()                     # 6. loader-consistent sources
        if not ledger:                                    # 7. authorization (ledgers: trusted)
            authority = self.store._authority
            scope = authority.consequential_scope() if authority is not None else contextlib.nullcontext()
            with scope:  # type: ignore[attr-defined]
                self._authorize()
        self.store._staging = True                        # nested ledger commits are refused from here
        entries = self._stage(staging)                    # 8. directories and temps
        journal = Journal("commit", entries)
        self._recheck(staging)                            # still ours, just before the commit point
        self._commit_point(journal)                       # 9. journal replace
        try:
            settlement = engine.settle(self.access, journal, live=True)  # 10-12
        except (OSError, StoreError, ValueError) as error:
            raise CommitIncomplete(f"failed after the commit point; recovery will finish it: {error}") from error
        if settlement.preserved:
            raise WriteConflict(settlement.preserved, settlement.published)
        if settlement.incomplete:
            raise CommitIncomplete("staged files vanished before publication (removed by another program?); "
                                   f"not written: {', '.join(settlement.incomplete)}")
        return CommitResult(MappingProxyType({str(put.address): etag(put.data) for put in self.puts}))

    def _check_paths(self) -> int:
        guard = self.access.guard
        guard.check_session()
        self.access.check_staging()
        staging = lstat_or_none(self.access.path((TXN,)))
        if staging is None:
            raise StoreError("the library has no staging directory; reopen it")
        guard.check_write((TXN, "x.new"), staging_device=None)
        for put in self.puts:
            guard.check_write(put.parts, staging_device=staging.st_dev)
            guard.check_write(conflict_parts(put.parts, etag(put.data)), staging_device=staging.st_dev)
        return staging.st_dev

    def _check_revisions(self) -> None:
        stale = []
        for put in self.puts:
            data = self.access.read(put.parts)
            current = None if data is None else etag(data)
            if current != put.expected:
                stale.append(("/".join(put.parts), "exists" if put.expected is None else
                              "absent" if current is None else "changed"))
            elif data is not None and not put.ledger and _unsupported(data, put.envelope):
                stale.append(("/".join(put.parts), "unsupported_format"))
        if stale:
            raise StaleWrite(stale)

    def _check_names(self) -> None:
        for put in self.puts:
            if self.access.presence(put.parts) == "mismatch":
                raise NameCollision(f"{'/'.join(put.parts)} collides with a differently spelled existing entry")
            self.access.check_conflict_folder(conflict_parts(put.parts, etag(put.data)))

    def _check_sources(self) -> None:
        touched: dict[str, tuple[SourceManifest | None, list[SourceRef]]] = {}
        art: set[str] = set()  # sources receiving images or placements
        for put in self.puts:
            if isinstance(put.envelope, SourceManifest):
                entry = touched.setdefault(put.envelope.source, (None, []))
                touched[put.envelope.source] = (put.envelope, entry[1])
            elif isinstance(put.envelope, RecordEnvelope) and put.envelope.is_definition:
                ref = put.envelope.address
                assert isinstance(ref, SourceRef)
                touched.setdefault(ref.source, (None, []))[1].append(ref)
            elif isinstance(put.envelope, AssetRecord):
                owner = _asset_owner(put.envelope)
                if owner[0] == "source":
                    touched.setdefault(owner[1], (None, []))  # an admitted manifest is required (018)
                    art.add(owner[1])
                else:
                    self._check_campaign(owner[1])
        proposed = {source_id: manifest for source_id, (manifest, _) in touched.items() if manifest is not None}
        facts = None  # verified once for the whole commit, and only if an art-only source needs it
        for source_id, (manifest, new_refs) in touched.items():
            if manifest is None and not new_refs:  # art only: the source's own admission, from its cached view
                facts = facts or self.store.source_facts()
                verdict = self.store._view(source_id, facts).verdict
                if verdict == "missing_source":
                    raise InvalidSourceState(f"{source_id} has no manifest; write one in this transaction")
                if verdict is not None:
                    raise InvalidSourceState(f"{source_id} is unreadable or excluded on disk; repair it first")
                continue
            _validate_source(self.access, source_id, manifest, new_refs, proposed)
            if source_id in art:  # a manifest rewrite cannot clear exclusions its records cause (ownership)
                manifest_path = "/".join(manifest_parts(source_id))
                blocking = [problem for problem in loader.contained_scan(self.access, source_id).excluded_by
                            if problem.path != manifest_path]
                if blocking:
                    raise InvalidSourceState(f"{source_id} is excluded by its own files ({blocking[0].code}); "
                                             "repair them before adding art")

    def _check_campaign(self, campaign: str) -> None:
        """A campaign asset needs a campaign: on disk, or this transaction's scope record or instance."""
        for put in self.puts:
            envelope = put.envelope
            if isinstance(envelope, ScopeRecord) and envelope.address == ScopeRef(campaign):
                return
            if isinstance(envelope, RecordEnvelope) and isinstance(envelope.address, InstanceRef) \
                    and envelope.address.campaign_id == campaign:
                return
        if not loader.campaign_exists(self.access, campaign):
            raise InvalidSourceState(f"campaign {campaign} does not exist: create its scope or an instance first")

    def _authorize(self) -> None:
        decisions, denied = [], []
        for put in self.puts:
            text = str(put.address)
            decision = self.handle.authorize(Permission.APPLY, text, payload=put.data,  # type: ignore[attr-defined]
                                             approval_id=self.approvals.get(text))
            decisions.append(decision)
            if getattr(decision, "approval_id", None) is not None:
                self.consumed.append(decision.approval_id)
            if not getattr(decision, "allowed", False) or decision.resource != put.address:
                denied.append(text)
        if denied:
            unavailable = any(getattr(d, "reason", None) == "internal_error" for d in decisions)
            why = "the authority is unavailable (reopen it)" if unavailable else "not authorized"
            raise WriteDenied(f"{why} to write {', '.join(denied)}; nothing was written",
                              decisions, self.consumed)

    def _note(self, error: BaseException) -> None:
        if self.consumed:
            error.add_note(f"approvals consumed without a write: {', '.join(self.consumed)}")

    def _recheck(self, staging: int) -> None:
        """Repeat the point-in-time checks just before a phase that mutates (authorization or
        staging may have taken a while): root, ``.txn`` and every target's path."""
        self.access.guard.check_session()
        self.access.check_staging()
        for put in self.puts:
            self.access.guard.check_write(put.parts, staging_device=staging)

    def _stage(self, staging: int) -> tuple[Entry, ...]:
        self._recheck(staging)
        access, temps = self.access, []
        try:
            entries = []
            for put in self.puts:
                access.make_parent(put.parts)
                temp = access.ops.make_temp(access.path((TXN,)), prefix="rec", suffix=".new")
                temps.append(temp)
                access.ops.write_bytes(temp, put.data)
                entries.append(Entry(put.parts, os.path.basename(temp), put.expected, etag(put.data)))
            return tuple(entries)
        except OSError as error:
            self._discard(temps)
            self._note(error)
            raise

    def _discard(self, temps: list[str]) -> None:
        for temp in temps:
            try:
                self.access.ops.remove(temp)
            except OSError:
                pass  # left for the next recovery's staging cleanup

    def _commit_point(self, journal: Journal) -> None:
        access, temp = self.access, None
        try:
            temp = access.ops.make_temp(access.path((TXN,)), prefix="journal", suffix=".new")
            access.ops.write_bytes(temp, journal.render())
            access.ops.replace(temp, access.journal)
        except OSError as error:
            try:
                committed = access.journal_exists()
            except (OSError, StoreError, ValueError):
                committed = True  # unknown: let recovery decide
            if committed:
                raise CommitIncomplete(f"the commit point may have been reached: {error}") from error
            self._discard([*(access.path((TXN, entry.temp)) for entry in journal.entries),
                           *([temp] if temp else [])])
            self._note(error)
            raise


def _unsupported(data: bytes, envelope: object) -> bool:
    """Whether the current file is a format this store must not overwrite (a newer version)."""
    if isinstance(envelope, ImageBlob):
        return False  # bytes, never parsed
    parse = (parse_source if isinstance(envelope, SourceManifest) else
             parse_scope_record if isinstance(envelope, ScopeRecord) else
             # Unreachable for a create-only knowledge put (an existing file is already `exists`);
             # kept so a future revisioned family cannot silently fall through to parse_record.
             parse_knowledge_record if isinstance(envelope, KnowledgeRecord) else
             parse_asset_record if isinstance(envelope, AssetRecord) else parse_record)
    try:
        parse(parse_strict(data))
    except EnvelopeError as error:
        return error.code == "unsupported_format"
    except JsonError:
        return False
    return False


def _asset_owner(record: AssetRecord) -> tuple[str, str]:
    """``("source", id)`` or ``("campaign", id)``: who an asset record belongs to, by its address."""
    address = record.address
    if isinstance(address, ImageRef):
        return ("campaign", address.campaign_id) if address.campaign_id is not None else ("source", address.scope)
    subject = address.subject
    if isinstance(subject, InstanceRef):
        return ("campaign", subject.campaign_id)
    return ("source", subject.source)


def _validate_source(access: Access, source_id: str, manifest: SourceManifest | None,
                     new_refs: list[SourceRef], proposed: Mapping[str, SourceManifest]) -> None:
    """Refuse new or worsened head, kind and link problems; leave unrelated old ones alone."""
    scan = loader.scan_source(access, source_id)
    disk_manifest = scan.stored.manifest if scan.stored else None
    if scan.excluded_by and manifest is None:
        raise InvalidSourceState(f"{source_id} is unreadable on disk ({scan.excluded_by[0].detail}); "
                                 "write its manifest to repair it first")
    after = manifest or disk_manifest
    if after is None:
        raise InvalidSourceState(f"{source_id} has no manifest; write one in this transaction")
    try:
        old_heads = disk_manifest.head_refs() if disk_manifest else {}
    except ValueError:  # an unreadable head map on disk: every new head counts as touched
        old_heads = {}
    new_heads = after.head_refs()
    revisions = {key: dict(value) for key, value in scan.revisions.items()}
    for ref in new_refs:
        revisions.setdefault((ref.kind, ref.entity_id), {})[ref.revision] = True  # type: ignore[assignment]
        reused = sorted({kind for kind, entity in revisions if entity == ref.entity_id} - {ref.kind})
        if reused:
            raise InvalidSourceState(f"{ref} reuses entity id {ref.entity_id} already used for {reused}")
    touched = {(ref.kind, ref.entity_id) for ref in new_refs}
    touched |= {(ref.kind, ref.entity_id) for ref in set(old_heads) | set(new_heads)
                if old_heads.get(ref) != new_heads.get(ref)}
    for key in sorted(touched):
        unpinned = SourceRef(source_id, *key)
        files = revisions.get(key, {})
        head = new_heads.get(unpinned)
        if head is not None and not files.get(head):
            raise InvalidSourceState(f"head of {unpinned} would name missing or unreadable revision {head}")
        if head is None and len(files) > 1:
            raise InvalidSourceState(f"{unpinned} would have {len(files)} revisions and no head")
    if manifest is not None:
        _validate_links(access, source_id, disk_manifest, manifest, proposed)


def _validate_links(access: Access, source_id: str, before: SourceManifest | None,
                    after: SourceManifest, proposed: Mapping[str, SourceManifest]) -> None:
    """Refuse links the loader would drop that are not already dropped today (duplicates,
    reversed symmetric duplicates, derivation cycles, also against every other source)."""
    with access.cached_listings():
        others = [manifest for other, manifest in proposed.items() if other != source_id]  # the post-commit view
        for other in loader.discover_sources(access):
            if other == source_id or other in proposed:
                continue
            scan = loader.contained_scan(access, other)  # the loader's own exclusion rules
            if scan.excluded_by or scan.stored is None:
                continue  # an excluded source's links are never registered
            others.append(scan.stored.manifest)
    where = {manifest.source: manifest.source for manifest in [*others, after]}

    def dropped(own: SourceManifest | None) -> list[str]:
        if own is None:
            return []
        stated = [(manifest.source, position, link) for manifest in [*others, own]
                  for position, link in enumerate(manifest.links)]
        problems: list[loader.LoadProblem] = []
        loader.accept_links(stated, where, problems)
        return [problem.detail for problem in problems if problem.source == source_id]

    existing = dropped(before)
    for detail in dropped(after):
        if detail in existing:
            existing.remove(detail)
        else:
            raise InvalidSourceState(f"{source_id}: {detail}")
