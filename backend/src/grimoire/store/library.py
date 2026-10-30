"""The V2 native record store: open a library, read records, commit guarded transactions.

``LibraryStore`` is trusted application code and is never handed to a worker.
Every write is a ``Transaction`` that commits in a fixed order: nothing is
mutated until the root, links, V1 isolation, compare-and-swap preconditions,
name collisions, source consistency and authorization have all passed; the
journal replace is the commit point; publication and recovery share one
guarded decision table (see ``engine``). See ``docs/STORAGE.md``.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.library import LibraryPathError, check_write_destination
from grimoire.security import Permission, ResourcePath

from . import engine, loader
from .diskops import DiskOps, FileOps, is_link, lstat_or_none
from .engine import Access, Settlement
from .envelope import (
    EnvelopeError, link_problem, RecordEnvelope, SourceManifest, parse_record, parse_source, record_document, source_document,
)
from .errors import (
    CommitIncomplete, ImmutableRevision, InvalidEnvelope, InvalidSourceState, NameCollision, NotALibrary,
    PendingRecovery, RecoveryBlocked, StaleWrite, StoreError, WriteConflict, WriteDenied,
)
from .guard import Guard, Limits, RootIdentity
from .journal import Entry, Journal, is_staging_name
from .jsonfmt import JsonError, etag, is_etag, parse_strict, render
from .layout import (
    CONFLICTS, LIBRARY_FORMAT, LOCK, MARKER, TXN, Parts, address_parts, conflict_parts, definition_parts,
    instance_parts, logical, manifest_parts,
)
from .loader import LibraryView, StoredRecord, StoredSource
from .lock import Locker, OsLocker


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

        Takes the writer lock and, without interpreting the journal, moves it and
        every staged file into ``.conflicts/discarded-<digest>/``. Anything already
        published stays published, and ``load`` reports what is inconsistent.
        """
        ops = ops or DiskOps()
        locker = locker or OsLocker()
        root = check_write_destination(_absolute(path))
        access = Access(root, ops, Guard(root, RootIdentity.capture(root)))
        cls._require_marker(access)
        lock = cls._lock(access, locker)
        try:
            access.guard.check_session()
            if not access.journal_exists():
                return ()
            digest = etag(ops.read_bytes(access.journal)).removeprefix("sha256:")[:12]
            folder = (CONFLICTS, f"discarded-{digest}")
            access.guard.check_write(folder + ("x",), staging_device=None)
            moved = [name for name in ops.list_dir(root / TXN) if is_staging_name(name)] + [access.journal.name]
            if access.presence(folder) != "present":
                ops.make_dirs(access.path(folder))
            for name in moved:
                ops.replace(root / TXN / name, access.path(folder + (name,)))
            return tuple("/".join(folder + (name,)) for name in moved)
        finally:
            locker.release(lock)

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
        if self._access.journal_exists():
            raise PendingRecovery("a commit awaits recovery; reopen the library writable to finish it")

    def recover(self) -> RecoveryReport:
        """Finish or preserve an interrupted commit. Persistent I/O errors are ``RecoveryBlocked``."""
        self._require_open()
        if not self._writable:
            raise StoreError("a read-only store cannot recover")
        try:
            report = RecoveryReport.of(engine.recover(self._access))
        except OSError as error:
            raise RecoveryBlocked(f"recovery could not finish: {error}") from error
        self.last_recovery = report
        return report

    # -- reading -----------------------------------------------------------------
    def read(self, ref: SourceRef | InstanceRef) -> StoredRecord:
        """One record by exact address (a definition must be pinned)."""
        self._require_consistent()
        if isinstance(ref, SourceRef) and not ref.pinned:
            raise ValueError("read a pinned revision; resolve unpinned references with load().index")
        return loader.read_record(self._access, address_parts(ref))

    def read_source(self, source_id: str) -> StoredSource:
        self._require_consistent()
        return loader.read_source(self._access, source_id)

    def load(self) -> LibraryView:
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
class _Put:
    parts: Parts
    address: ResourcePath
    data: bytes
    expected: str | None
    envelope: RecordEnvelope | SourceManifest


class Transaction:
    """Records to write together, each with the content revision it expects to replace."""

    def __init__(self, store: LibraryStore) -> None:
        self._store = store
        self._puts: dict[Parts, _Put] = {}
        self._committed = False

    def put(self, envelope: RecordEnvelope | SourceManifest, expected: str | None) -> str:
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
        else:
            raise InvalidEnvelope("put takes a RecordEnvelope or a SourceManifest")
        if parts in self._puts:
            raise InvalidEnvelope(f"{'/'.join(parts)} is written twice in one transaction")
        address = logical(parts)
        self._puts[parts] = _Put(parts, address, render(document), expected, envelope)
        return str(address)

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each target will hold, keyed by logical path: what a human approves."""
        return {str(put.address): put.data for put in self._puts.values()}

    def commit(self, handle: object, approvals: Mapping[str, str] | None = None) -> CommitResult:
        if self._committed:
            raise StoreError("this transaction was already committed")
        if not self._puts:
            raise InvalidEnvelope("nothing to commit")
        self._committed = True
        return _Commit(self._store, tuple(self._puts.values()), handle, dict(approvals or {})).run()


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
        self.store._require_consistent()                 # 1. no pending journal
        staging = self._check_paths()                     # 3. root, links, V1, preflight
        self._check_revisions()                           # 4. compare-and-swap
        self._check_names()                               # 5. collisions
        self._check_sources()                             # 6. loader-consistent sources
        self._authorize()                                 # 7. authorization
        entries = self._stage(staging)                    # 8. directories and temps
        journal = Journal("commit", entries)
        self._commit_point(journal)                       # 9. journal replace
        try:
            settlement = engine.settle(self.access, journal)  # 10-12
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
            elif data is not None and _unsupported(data, isinstance(put.envelope, SourceManifest)):
                stale.append(("/".join(put.parts), "unsupported_format"))
        if stale:
            raise StaleWrite(stale)

    def _check_names(self) -> None:
        for put in self.puts:
            if self.access.presence(put.parts) == "mismatch":
                raise NameCollision(f"{'/'.join(put.parts)} collides with a differently spelled existing entry")

    def _check_sources(self) -> None:
        touched: dict[str, tuple[SourceManifest | None, list[SourceRef]]] = {}
        for put in self.puts:
            if isinstance(put.envelope, SourceManifest):
                entry = touched.setdefault(put.envelope.source, (None, []))
                touched[put.envelope.source] = (put.envelope, entry[1])
            elif put.envelope.is_definition:
                ref = put.envelope.address
                assert isinstance(ref, SourceRef)
                touched.setdefault(ref.source, (None, []))[1].append(ref)
        for source_id, (manifest, new_refs) in touched.items():
            _validate_source(self.access, source_id, manifest, new_refs)

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
            raise WriteDenied(f"not authorized to write {', '.join(denied)}; nothing was written",
                              decisions, self.consumed)

    def _note(self, error: BaseException) -> None:
        if self.consumed:
            error.add_note(f"approvals consumed without a write: {', '.join(self.consumed)}")

    def _stage(self, staging: int) -> tuple[Entry, ...]:
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


def _unsupported(data: bytes, manifest: bool) -> bool:
    """Whether the current file is a format this store must not overwrite (a newer version)."""
    try:
        (parse_source if manifest else parse_record)(parse_strict(data))
    except EnvelopeError as error:
        return error.code == "unsupported_format"
    except JsonError:
        return False
    return False


def _validate_source(access: Access, source_id: str, manifest: SourceManifest | None,
                     new_refs: list[SourceRef]) -> None:
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
        _validate_links(access, source_id, disk_manifest, manifest)


def _validate_links(access: Access, source_id: str, before: SourceManifest | None,
                    after: SourceManifest) -> None:
    """Refuse links the loader would drop that are not already dropped today (duplicates,
    reversed symmetric duplicates, derivation cycles, also against every other source)."""
    with access.cached_listings():
        others = []
        for other in loader.discover_sources(access):
            if other == source_id:
                continue
            try:
                manifest = loader.read_source(access, other).manifest
                manifest.head_refs()
            except (StoreError, ValueError):
                continue  # an unreadable source declares nothing the loader would register
            if any(found is not None and found[0] == "ownership"
                   for found in (link_problem(link, other) for link in manifest.links)):
                continue  # excluded by the loader, so its links are never registered
            others.append(manifest)
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
