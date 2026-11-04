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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

from grimoire.core.identity import (
    ChatRef, IdentityConflict, InstanceRef, KnowledgeRef, RelationRef, ScopeRef, SourceRef, validate_segment,
)
from grimoire.core.index import DefinitionIndex, SourceDescriptor
from grimoire.library import LibraryPathError, check_write_destination
from grimoire.security import Permission, ResourcePath

from . import engine, loader, replica
from .diskops import DiskOps, FileOps, is_link, lstat_or_none
from .engine import Access, Settlement
from .envelope import (
    EnvelopeError, KnowledgeRecord, RecordEnvelope, ScopeRecord, SourceManifest, knowledge_record_document,
    parse_knowledge_record, parse_record, parse_scope_record, parse_source, record_document, scope_record_document,
    source_document,
)
from .errors import (
    CommitIncomplete, ImmutableRevision, InvalidEnvelope, InvalidSourceState, LinkRefused, NameCollision,
    NotALibrary, LedgerBusy, PendingRecovery, RecoveryBlocked, RecoveryFailed, StaleWrite, StoreError, WriteConflict,
    WriteDenied,
)
from .replica import CanonicalScan
from .guard import Guard, Limits, RootIdentity
from .journal import Entry, Journal, is_staging_name
from .jsonfmt import MAX_FILE_BYTES, JsonError, etag, is_etag, parse_strict, render
from .layout import (
    CAMPAIGNS, CONFLICTS, DISCARD_MARKER, INSTANCES, JOURNAL, LIBRARY_FORMAT, LOCK, MARKER, REPAIR_MARKER, SYNC,
    TXN, Parts, address_parts, classify, conflict_parts, definition_parts, instance_parts, is_kept_target,
    kept_address, ledger_folder, ledger_name, ledger_parts, ledger_seq, logical, manifest_parts, sync_state_parts,
)
from .ledger import Head, Ledger, read_ledger, render_batches
from .loader import (
    CampaignRelations, KnowledgeScan, LibraryView, StoredDefinition, StoredKnowledgeRecord, StoredRecord,
    StoredScopeRecord, StoredSource,
)
from .lock import Locker, OsLocker

#: Authorization outcomes that concern one path; a partial commit drops those puts (019).
_PATH_DENIALS = frozenset({"not_granted", "approval_required", "reserved_resource"})
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
    dropped: tuple[str, ...] = ()  # kept versions and sync state set aside unpublished

    @classmethod
    def of(cls, settlement: Settlement) -> RecoveryReport:
        return cls(tuple(settlement.published), tuple(settlement.preserved), tuple(settlement.incomplete),
                   settlement.aborted, tuple(settlement.dropped))


@dataclass(frozen=True)
class CommitResult:
    """The new content revision of every written target, by preview key.

    A partial commit (``on_denied``) also lists the ``denied`` keys and every approval it
    consumed, including those of puts it dropped as dependents.
    """

    etags: Mapping[str, str]
    denied: frozenset[str] = frozenset()
    consumed_approvals: tuple[str, ...] = ()
    dropped: frozenset[str] = field(default_factory=frozenset)


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
            self.last_recovery = report
            return report

    # -- reading -----------------------------------------------------------------
    def read(self, ref: SourceRef | InstanceRef | ScopeRef | ChatRef | RelationRef | KnowledgeRef
             ) -> StoredRecord | StoredScopeRecord | StoredKnowledgeRecord:
        """One record by exact address (a definition must be pinned)."""
        if isinstance(ref, SourceRef) and not ref.pinned:
            raise ValueError("read a pinned revision; resolve unpinned references with load().index")
        with self.lock:  # reads wait for this process's commits, so they never see one half-done
            self._require_consistent()
            if isinstance(ref, (ScopeRef, ChatRef, RelationRef)):
                return loader.read_scope_record(self._access, address_parts(ref))
            if isinstance(ref, KnowledgeRef):
                return loader.read_knowledge_record(self._access, address_parts(ref))
            return loader.read_record(self._access, address_parts(ref))

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

    def check_source(self, source_id: str) -> StoredSource | str:
        """One source as ``load()`` would admit it, or why not (``missing_source``/``excluded_source``).

        Scans only that source, so its cost does not grow with the library. Exclusions that
        only a whole-library build can see (two sources' ``identity_conflict``) are not detected.
        """
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                scan = loader.contained_scan(self._access, source_id)
        verdict = _scan_verdict(scan)
        return verdict if verdict is not None else scan.stored  # type: ignore[return-value]

    def check_definition(self, ref: SourceRef) -> StoredDefinition | str:
        """The definition ``ref`` resolves to under ``load()``'s rules for its own source, or a reason.

        Reasons: ``missing_source``, ``excluded_source``, ``entity_dropped``, ``missing_entity``,
        ``missing_revision``. An unpinned ref follows the declared head, or the only revision.
        Same scope limit as ``check_source``.
        """
        with self.lock:
            self._require_consistent()
            with self._access.cached_listings():
                scan = loader.contained_scan(self._access, ref.source)
        verdict = _scan_verdict(scan)
        if verdict is not None:
            return verdict
        definitions, heads, dropped, _ = loader.evaluate(scan)
        if (ref.kind, ref.entity_id) in dropped:
            return "entity_dropped"
        manifest = scan.stored.manifest  # type: ignore[union-attr]
        try:
            index = DefinitionIndex([SourceDescriptor(ref.source, manifest.owner, manifest.title)], definitions, heads)
        except IdentityConflict:
            return "excluded_source"
        broken = index.check(ref)
        if broken is not None:
            return broken.reason
        return index.resolve(ref)  # type: ignore[return-value]

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

    # -- synchronization (019) --------------------------------------------------
    @contextlib.contextmanager
    def cached_reads(self) -> Iterator[None]:
        """Reuse directory listings for a read-only phase of this thread (a sync plan).

        Nothing may be written through this store inside the block; a commit's own compare-and-swap
        still re-reads every target, so a change made meanwhile by another program is caught there.
        """
        with self.lock, self._access.cached_listings():
            yield

    def scan(self) -> CanonicalScan:
        """Every canonical record file's exact bytes; blocked and skipped paths are named."""
        with self.lock:
            self._require_consistent()
            return replica.scan(self._access)

    def read_sync_state(self, name: str) -> bytes | None:
        """The exact bytes of ``.sync/<name>`` (``device.json``, ``peers/<id>/{archive,pending}.json``).

        ``None`` only when the file does not exist; a read error propagates (an unreadable marker
        must never look like a missing one).
        """
        parts = sync_state_parts(name)
        with self.lock:
            self._require_consistent()
            return self._access.read(parts)

    def read_exact(self, path: str) -> bytes | None:
        """The exact bytes of a canonical record or kept-version file, or ``None``; never through a link."""
        parts = tuple(path.split("/"))
        found = classify(parts)
        if (found is None or found[0] == "ledger") and not is_kept_target(parts):
            raise ValueError(f"{path} is not a canonical record or kept-version path")
        with self.lock:
            self._require_consistent()
            return self._access.read(parts)

    def collides(self, path: str) -> bool:
        """Whether ``path`` (or a missing ancestor) would collide with a differently spelled entry."""
        with self.lock:
            self._require_consistent()
            return self._access.presence(tuple(path.split("/"))) == "mismatch"

    # -- writing -----------------------------------------------------------------
    def transaction(self) -> Transaction:
        self._require_open()
        if not self._writable:
            raise StoreError("the library was opened read-only")
        return Transaction(self)


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
    address: ResourcePath | None  # what is authorized; None for trusted ledger batches and sync state
    data: bytes
    expected: str | None
    envelope: RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord | None  # records only
    kind: str = "record"  # record | ledger | keep (a kept version) | state (``.sync``)
    key: str = ""  # the preview / approval / result key
    replace_unreferenced: bool = False
    regenerable: bool = False  # exact bytes another library still holds: dropped, never preserved

    @property
    def ledger(self) -> bool:
        return self.kind == "ledger"

    @property
    def journal_kind(self) -> str:
        if self.kind == "record" and self.regenerable:
            return "replica"
        if self.kind == "keep":
            return "keep" if self.regenerable else "hold"
        return {"state": "state"}.get(self.kind, "publish")

    @property
    def preservable(self) -> bool:
        return self.journal_kind in ("publish", "hold")


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
                puts.append(_Put(parts, logical(parts), data, None, None, "ledger", str(logical(parts))))
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
        self._kept: set[Parts] = set()  # records with a kept version in this transaction
        self._keys: set[str] = set()  # preview keys staged so far
        self._committed = False

    def put(self, envelope: RecordEnvelope | SourceManifest | ScopeRecord | KnowledgeRecord,
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
        else:
            raise InvalidEnvelope("put takes a RecordEnvelope, a SourceManifest, a ScopeRecord or a KnowledgeRecord")
        if parts in self._puts:
            raise InvalidEnvelope(f"{'/'.join(parts)} is written twice in one transaction")
        address = logical(parts)
        self._add(_Put(parts, address, render(document), expected, envelope, "record", str(address)))
        return str(address)

    def _add(self, put: _Put) -> None:
        if self._committed:
            raise StoreError("this transaction was already committed")
        if put.parts in self._puts:
            raise InvalidEnvelope(f"{'/'.join(put.parts)} is written twice in one transaction")
        if put.key in self._keys:
            raise InvalidEnvelope(f"{put.key} is written twice in one transaction")
        self._puts[put.parts] = put
        self._keys.add(put.key)

    def put_exact(self, path: str, data: bytes, expected: str | None, *,
                  replace_unreferenced: bool = False, regenerable: bool = False) -> str:
        """Stage another library's exact bytes at canonical ``path`` (sync, 019).

        The bytes are validated as ``put`` would validate the envelope they hold (strict JSON,
        supported format, address equal to the path) and published verbatim, so two libraries
        converge on one etag. With ``regenerable`` (the bytes still exist in another library, as for
        sync), an aborted commit or an external edit of the target drops them rather than
        preserving them under ``.conflicts`` (journal kind ``replica``); without it they are
        preserved like any ``put``. A definition revision is create-only unless
        ``replace_unreferenced``: then ``expected`` must be its current etag, and the commit
        refuses (``RevisionReferenced``) unless nothing on this library can pin it.
        """
        if expected is not None and not is_etag(expected):
            raise InvalidEnvelope("expected must be None or an etag")
        if type(path) is not str:
            raise InvalidEnvelope("path must be text")
        parts = tuple(path.split("/"))
        envelope = replica.parse_exact(parts, data)
        if isinstance(envelope, KnowledgeRecord):
            if expected is not None or replace_unreferenced:
                raise ImmutableRevision(f"{envelope.address} is create-only; correct it by appending")
        elif isinstance(envelope, RecordEnvelope) and envelope.is_definition:
            if replace_unreferenced and expected is None:
                raise InvalidEnvelope("replacing a revision needs its current etag")
            if expected is not None and not replace_unreferenced:
                raise ImmutableRevision(f"{envelope.address} exists; add a new revision and move the head")
        elif replace_unreferenced:
            raise InvalidEnvelope("only a definition revision is replaced as unreferenced")
        address = logical(parts)
        self._add(_Put(parts, address, bytes(data), expected, envelope, "record", str(address),
                       replace_unreferenced, regenerable=regenerable))
        return str(address)

    def keep_version(self, path: str, data: bytes, slot: int | None = None, *, regenerable: bool = False) -> str:
        """Stage ``data`` as a kept version of canonical ``path`` under ``.conflicts``; returns its path.

        With ``slot=None`` the first slot that is absent or already holds these bytes is used (a
        reused slot stages nothing). A planned ``slot`` holding other bytes is ``StaleWrite``.
        Authorized as ``APPLY`` on its own ``kept/...`` address, never the record's. With
        ``regenerable`` (another library still holds these bytes, as for sync) an aborted commit
        drops the kept version (journal kind ``keep``); without it, it is preserved like a
        ``put`` (``hold``), because it may be the only copy (``resolve``'s replaced version).
        """
        if type(path) is not str:
            raise InvalidEnvelope("path must be text")
        parts = tuple(path.split("/"))
        found = classify(parts)
        if found is None or found[0] == "ledger":
            raise InvalidEnvelope(f"{path} is not a canonical record path")
        if type(data) is not bytes or len(data) > MAX_FILE_BYTES:
            raise InvalidEnvelope("a kept version is bytes within the file size limit")
        try:
            parse_strict(data)
        except JsonError as error:
            raise InvalidEnvelope(f"a kept version must be readable JSON: {error}") from None
        if parts in self._kept:
            raise InvalidEnvelope(f"{path} already has a kept version in this transaction")
        self._kept.add(parts)
        tag, access = etag(data), self._store._access
        attempts = range(engine.MAX_CONFLICT_ATTEMPTS) if slot is None else (slot,)
        for attempt in attempts:
            target = conflict_parts(parts, tag, attempt)
            current = access.etag_of(target)
            if current == tag:
                return "/".join(target)  # already kept: nothing to stage
            if current is None:
                address = kept_address(parts, tag, attempt)
                self._add(_Put(target, address, bytes(data), None, None, "keep", str(address),
                               regenerable=regenerable))
                return "/".join(target)
            if slot is not None:
                raise StaleWrite([("/".join(target), "changed")])
        raise StoreError(f"no free kept-version slot for {path}")

    def put_sync_state(self, name: str, data: bytes, expected: str | None) -> str:
        """Stage a device-local ``.sync`` file (trusted bookkeeping: not authorized)."""
        if expected is not None and not is_etag(expected):
            raise InvalidEnvelope("expected must be None or an etag")
        parts = sync_state_parts(name)
        try:
            parse_strict(data)
        except JsonError as error:
            raise InvalidEnvelope(f"sync state must be JSON: {error}") from None
        if len(data) > MAX_FILE_BYTES:
            raise InvalidEnvelope("sync state exceeds the file size limit")
        key = "/".join(parts)
        self._add(_Put(parts, None, bytes(data), expected, None, "state", key))
        return key

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each target will hold, keyed by what is approved: a record's logical
        address, a kept version's ``kept/...`` address, or a ``.sync/...`` path (not authorized)."""
        return {put.key: put.data for put in self._puts.values()}

    def check(self) -> None:
        """Run commit steps 1-6 read-only; raises what ``commit`` would raise before authorizing.

        Consumes no approval and writes nothing. With no puts it checks the library itself
        (root, V1, ``.txn``, ``.conflicts`` and ``.sync``).
        """
        store = self._store
        store._require_open()
        if not store._writable:
            raise StoreError("the library was opened read-only")
        with store.lock:
            puts = tuple(self._puts.values())
            if any(put.kind != "state" for put in puts) or not puts:
                self._require_writable_records(None, check_handle=False)
            _Commit(store, puts, None, {}).preflight()

    def _require_writable_records(self, handle: object, *, check_handle: bool = True) -> None:
        store = self._store
        authority = store._authority
        if check_handle and authority is not None and not authority.issued(handle):  # type: ignore[attr-defined]
            raise WriteDenied("the handle was not issued by this library's authority; nothing was written",
                              (), ())
        if authority is None and store._access.presence(ledger_parts("security", 1)) != "absent":
            raise StoreError("this library has a security ledger: open its durable authority before writing")

    def commit(self, handle: object, approvals: Mapping[str, str] | None = None, *,
               on_denied: Callable[[frozenset[str]], Mapping[str, bytes | None]] | None = None) -> CommitResult:
        """Commit every put, or none.

        With ``on_denied`` (partial mode, 019) each put is still authorized exactly once, but a
        put denied for its path (not granted, approval required, reserved) is dropped instead
        of failing the commit. ``on_denied(denied_keys)`` may drop more puts (``None``) or replace
        the bytes of state puts; steps 4-6 then run again on what remains. Denials that are not
        about a path (an unissued handle, an unavailable authority) still raise ``WriteDenied``.
        """
        if self._committed:
            raise StoreError("this transaction was already committed")
        if not self._puts:
            raise InvalidEnvelope("nothing to commit")
        store = self._store
        with store.lock:  # the binding cannot change between these checks and the commit
            if store._access._listings is not None:
                raise StoreError("a commit cannot run inside a read-only phase (cached_reads): its checks would "
                                 "see stale listings")
            puts = tuple(self._puts.values())
            if any(put.kind != "state" for put in puts):  # sync state alone is trusted bookkeeping
                self._require_writable_records(handle)
            self._committed = True
            return _Commit(store, puts, handle, dict(approvals or {}), on_denied).run()


class _Commit:
    def __init__(self, store: LibraryStore, puts: tuple[_Put, ...], handle: object,
                 approvals: dict[str, str],
                 on_denied: Callable[[frozenset[str]], Mapping[str, bytes | None]] | None = None) -> None:
        self.store = store
        self.access: Access = store._access
        self.puts = puts
        self.handle = handle
        self.approvals = approvals
        self.on_denied = on_denied
        self.consumed: list[str] = []
        self.denied: frozenset[str] = frozenset()
        self.dropped: frozenset[str] = frozenset()

    def preflight(self) -> int:
        """Steps 1-6 (and 6b): everything decidable before authorization; reads only."""
        self.store._require_consistent()                 # 1. no pending journal
        with self.access.cached_listings():
            staging = self._check_paths()                 # 3. root, links, V1, preflight
            self._check_local_roots()
            self._check_revisions()                       # 4. compare-and-swap
            self._check_names()                           # 5. collisions
            if not any(put.ledger for put in self.puts):
                self._check_sources()                     # 6. loader-consistent sources
        return staging

    def _check_local_roots(self) -> None:
        """``.conflicts`` / ``.sync`` are spelled exactly and are not links, where this commit needs them.

        A transaction with no puts checks both (the library check); otherwise ``.sync`` only for
        sync-state puts and ``.conflicts`` only for kept versions, so a stray sync folder never
        blocks an ordinary write.
        """
        names = []
        if not self.puts or any(put.kind == "state" for put in self.puts):
            names.append(SYNC)
        if not self.puts or any(put.kind == "keep" for put in self.puts):
            names.append(CONFLICTS)
        for name in names:
            if self.access.presence((name,)) == "mismatch":
                raise NameCollision(f"{name} collides with a differently spelled folder")
            self.access.guard.check_read((name,))

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
        staging = self.preflight()                        # 1-6, read only
        if not ledger:                                    # 7. authorization (ledgers: trusted)
            authority = self.store._authority
            scope = authority.consequential_scope() if authority is not None else contextlib.nullcontext()
            with scope:  # type: ignore[attr-defined]
                denied = self._authorize()
            if denied:
                self._prune(denied)
                if not self.puts:
                    return self._result()
                try:
                    staging = self.preflight()            # 4-6 again on what remains; never re-authorized
                except BaseException as error:
                    self._note(error)
                    raise
        self.store._staging = True                        # nested ledger commits are refused from here
        entries = self._stage(staging)                    # 8. directories and temps
        journal = Journal("commit", entries)
        self._recheck(staging)                            # still ours, just before the commit point
        self._commit_point(journal)                       # 9. journal replace
        try:
            settlement = engine.settle(self.access, journal, live=True)  # 10-12
        except (OSError, StoreError, ValueError) as error:
            raise CommitIncomplete(f"failed after the commit point; recovery will finish it: {error}") from error
        if settlement.preserved or settlement.dropped or settlement.aborted:
            raise WriteConflict(settlement.preserved, settlement.published, settlement.dropped)
        if settlement.incomplete:
            raise CommitIncomplete("staged files vanished before publication (removed by another program?); "
                                   f"not written: {', '.join(settlement.incomplete)}")
        return self._result()

    def _result(self) -> CommitResult:
        return CommitResult(MappingProxyType({put.key: etag(put.data) for put in self.puts}),
                            self.denied, tuple(self.consumed), self.dropped)

    def _prune(self, denied: list[int]) -> None:
        """Partial mode: drop the denied puts and whatever the caller says depends on them."""
        assert self.on_denied is not None
        keys = frozenset(self.puts[index].key for index in denied)
        try:
            changes = dict(self.on_denied(keys))
        except BaseException as error:
            self._note(error)
            raise
        known = {put.key for put in self.puts}
        if not set(changes) <= known:
            raise InvalidEnvelope(f"on_denied named unknown puts: {sorted(set(changes) - known)}")
        remaining, dropped = [], set(keys)
        for index, put in enumerate(self.puts):
            if index in denied:
                continue
            if put.key in changes:
                data = changes[put.key]
                if data is None:
                    dropped.add(put.key)
                    continue
                if put.kind != "state":
                    raise InvalidEnvelope("on_denied may replace the bytes of sync state only")
                put = replace(put, data=bytes(data))
            remaining.append(put)
        self.puts, self.denied, self.dropped = tuple(remaining), keys, frozenset(dropped - keys)

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
            if put.preservable:  # replicas, regenerable kept versions and sync state are dropped instead
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
            elif data is not None and put.kind == "record" and _unsupported(data, put.envelope):
                stale.append(("/".join(put.parts), "unsupported_format"))
        if stale:
            raise StaleWrite(stale)

    def _check_names(self) -> None:
        for put in self.puts:
            if self.access.presence(put.parts) == "mismatch":
                raise NameCollision(f"{'/'.join(put.parts)} collides with a differently spelled existing entry")
            if put.preservable:
                self.access.check_conflict_folder(conflict_parts(put.parts, etag(put.data)))

    def _check_sources(self) -> None:
        touched: dict[str, tuple[SourceManifest | None, list[SourceRef]]] = {}
        for put in self.puts:
            if put.kind != "record":
                continue
            if put.replace_unreferenced:  # 6b: nothing on this library may pin the replaced revision
                ref = put.envelope.address  # type: ignore[union-attr]
                assert isinstance(ref, SourceRef)
                replica.check_unreferenced(self.access, ref)
            if isinstance(put.envelope, SourceManifest):
                entry = touched.setdefault(put.envelope.source, (None, []))
                touched[put.envelope.source] = (put.envelope, entry[1])
            elif isinstance(put.envelope, RecordEnvelope) and put.envelope.is_definition:
                ref = put.envelope.address
                assert isinstance(ref, SourceRef)
                touched.setdefault(ref.source, (None, []))[1].append(ref)
        proposed = {source_id: manifest for source_id, (manifest, _) in touched.items() if manifest is not None}
        for source_id, (manifest, new_refs) in touched.items():
            _validate_source(self.access, source_id, manifest, new_refs, proposed)

    def _authorize(self) -> list[int]:
        """Authorize every put once; the positions denied in partial mode (else raise)."""
        decisions, denied, positions, partial = [], [], [], self.on_denied is not None
        for index, put in enumerate(self.puts):
            if put.address is None:  # sync state: trusted bookkeeping, like the journal
                continue
            text = str(put.address)
            decision = self.handle.authorize(Permission.APPLY, text, payload=put.data,  # type: ignore[attr-defined]
                                             approval_id=self.approvals.get(text))
            decisions.append(decision)
            if getattr(decision, "approval_id", None) is not None:
                self.consumed.append(decision.approval_id)
            if not getattr(decision, "allowed", False) or decision.resource != put.address:
                denied.append(text)
                positions.append(index)
                if str(getattr(decision, "reason", "")) not in _PATH_DENIALS:
                    partial = False  # not about this path: never drop silently
        if denied and not partial:
            unavailable = any(getattr(d, "reason", None) == "internal_error" for d in decisions)
            why = "the authority is unavailable (reopen it)" if unavailable else "not authorized"
            raise WriteDenied(f"{why} to write {', '.join(denied)}; nothing was written",
                              decisions, self.consumed)
        return positions

    def _note(self, error: BaseException) -> None:
        if self.consumed:
            error.add_note(f"approvals consumed without a write: {', '.join(self.consumed)}")
            if not hasattr(error, "consumed_approvals"):
                error.consumed_approvals = tuple(self.consumed)  # type: ignore[attr-defined]

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
                entries.append(Entry(put.parts, os.path.basename(temp), put.expected, etag(put.data),
                                     put.journal_kind))  # type: ignore[arg-type]
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
    parse = (parse_source if isinstance(envelope, SourceManifest) else
             parse_scope_record if isinstance(envelope, ScopeRecord) else
             # Unreachable for a create-only knowledge put (an existing file is already `exists`);
             # kept so a future revisioned family cannot silently fall through to parse_record.
             parse_knowledge_record if isinstance(envelope, KnowledgeRecord) else parse_record)
    try:
        parse(parse_strict(data))
    except EnvelopeError as error:
        return error.code == "unsupported_format"
    except JsonError:
        return False
    return False


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
