"""Exact-name file access, guarded publication and journal recovery.

Publication and recovery share one decision table, so a commit interrupted at
any operation finishes or preserves exactly as an uninterrupted one would:

=========  ===========  ==========  =====================================
temp       temp digest  target      action
=========  ===========  ==========  =====================================
absent     --           any         already handled; keep the disk
present    != new       any         preserve (tampered staging)
present    = new        = old       publish
present    = new        = new       drop the temp
present    = new        other       preserve (external edit is kept)
=========  ===========  ==========  =====================================

"Preserve" applies to ``publish`` and ``hold`` entries. A ``replica`` (another
library's exact bytes), ``keep`` (a kept copy of another library's version) or
``state`` (``.sync``) entry that would be preserved is *dropped* instead: its bytes
are regenerable, and preserving them would leave copies nobody asked to keep, or
nest conflict copies of conflict copies. A ``hold`` (a kept version whose bytes
exist nowhere else, such as the version ``resolve`` replaces) is preserved.

An ``abort`` journal never publishes. A commit journal whose temps are all
still present and which would preserve any entry is first rewritten as
``abort``, so a crash part-way through preserving can never resume as a
partial publish.
"""

from __future__ import annotations

import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from grimoire.library import LibraryPathError

from .diskops import FileOps, is_link, lstat_or_none
from .errors import LinkRefused, NameCollision, RecoveryFailed, StoreError, UnstorableIdentifier
from .guard import Guard
from .journal import REGENERABLE_KINDS, Entry, Journal, is_staging_name, parse
from .jsonfmt import MAX_FILE_BYTES, etag
from .layout import DISCARD_MARKER, JOURNAL, REPAIR_MARKER, TXN, Parts, classify, conflict_parts, name_key

Presence = Literal["absent", "present", "mismatch"]
#: Distinct versions kept beside one conflict name before giving up (a backstop, never reached).
MAX_CONFLICT_ATTEMPTS = 100
#: The "etag" of a path only a look-alike spelling occupies: equal to no old or new etag.
LOOKALIKE = "lookalike"


class Access:
    """The store's view of one library root through a file seam, by exact spelling."""

    def __init__(self, root: Path, ops: FileOps, guard: Guard) -> None:
        self.root = root
        self.ops = ops
        self.guard = guard
        self._local = threading.local()  # listing caches belong to the thread that opened them

    @property
    def _listings(self) -> dict[Parts, list[str]] | None:
        return getattr(self._local, "listings", None)

    @_listings.setter
    def _listings(self, value: dict[Parts, list[str]] | None) -> None:
        self._local.listings = value

    @contextmanager
    def cached_listings(self) -> Iterator[None]:
        """Reuse directory listings while nothing is written (one load or one preflight)."""
        outer, self._listings = self._listings, {} if self._listings is None else self._listings
        try:
            yield
        finally:
            self._listings = outer

    def list_dir(self, parts: Parts) -> list[str]:
        """``parts``'s entries; ``LinkRefused`` if it or an ancestor is a link (never listed)."""
        if self._listings is None:
            self.guard.check_read(parts)
            return self.ops.list_dir(self.path(parts))
        if parts not in self._listings:
            self.guard.check_read(parts)
            self._listings[parts] = self.ops.list_dir(self.path(parts))
        return self._listings[parts]

    def path(self, parts: Parts) -> Path:
        return self.root.joinpath(*parts)

    def presence(self, parts: Parts) -> Presence:
        """Whether ``parts`` exists spelled exactly so; ``mismatch`` if only a look-alike does.

        Decided from directory listings, never by opening a composed path: a
        case-insensitive filesystem would find ``Harbor`` when asked for ``harbor``.
        An ancestor that is a link is refused (``LinkRefused``) before it is listed.
        """
        for index, part in enumerate(parts):
            if index and is_link(self.path(parts[:index])):  # never list through a link
                raise LinkRefused(f"refusing to follow a link inside the library: {self.path(parts[:index])}")
            try:
                names = self.list_dir(parts[:index])
            except (FileNotFoundError, NotADirectoryError):
                return "absent"
            if part in names:
                continue
            key = name_key(part)
            return "mismatch" if any(name_key(name) == key for name in names) else "absent"
        return "present"

    def read(self, parts: Parts) -> bytes | None:
        """The exact bytes of the regular file at ``parts``, or ``None``; never through a link.

        Anything else there (a directory, a FIFO, a device) is refused rather than
        opened, so a stray entry can neither crash nor hang a reader.
        """
        if self.presence(parts) != "present":
            return None
        self.guard.check_read(parts)
        info = lstat_or_none(self.path(parts))
        if info is None:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise UnstorableIdentifier(f"{'/'.join(parts)} is not a regular file")
        return self.ops.read_bytes(self.path(parts))

    def too_large(self, parts: Parts) -> bool:
        info = lstat_or_none(self.path(parts))
        return info is not None and info.st_size > MAX_FILE_BYTES

    def etag_of(self, parts: Parts) -> str | None:
        """The current etag, ``None`` if absent, or ``LOOKALIKE`` if only a look-alike exists."""
        if self.presence(parts) == "mismatch":
            return LOOKALIKE
        data = self.read(parts)
        return None if data is None else etag(data)

    def check_staged_file(self, name: str) -> None:
        """A file in ``.txn`` the store will read or move: a real regular file, never a link."""
        path = self.path((TXN, name))
        if is_link(path):
            raise LinkRefused(f"refusing a staged file that is a link: {path}")
        info = lstat_or_none(path)
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise UnstorableIdentifier(f"{path} is not a regular file")

    def check_conflict_folder(self, conflict: Parts) -> None:
        """Preserving needs ``.conflicts/<hash>`` spelled exactly; a look-alike folder is refused."""
        if self.presence(conflict[:-1]) == "mismatch":
            raise NameCollision(f"{'/'.join(conflict[:-1])} collides with a differently spelled folder")

    def check_staging(self) -> None:
        """``.txn`` and its lock are real entries of this library, never links elsewhere."""
        staging = self.path((TXN,))
        if is_link(staging):
            raise LinkRefused(f"refusing a staging directory that is a link: {staging}")
        info = lstat_or_none(staging)
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise LibraryPathError(f"{staging} is not a directory")
        lock = staging / "lock"
        if is_link(lock):
            raise LinkRefused(f"refusing a lock file that is a link: {lock}")

    @property
    def journal(self) -> Path:
        return self.path((TXN, JOURNAL))

    def journal_exists(self) -> bool:
        self.check_staging()  # never look through a linked staging directory
        return self.ops.exists(self.journal)

    def repair_pending(self) -> bool:
        """An interrupted ``repair_ledger``: the ledger is undefined until it finishes."""
        self.check_staging()
        return self.ops.exists(self.path((TXN, REPAIR_MARKER)))

    def discard_pending(self) -> bool:
        """An interrupted ``discard_journal``: nothing may publish until it is finished."""
        self.check_staging()
        return self.ops.exists(self.path((TXN, DISCARD_MARKER)))

    def make_parent(self, parts: Parts) -> list[Parts]:
        """Create ``parts``'s missing directories; returns the ones created, outermost first."""
        folder = parts[:-1]
        if self.presence(folder) == "present":
            return []
        first = next(index for index in range(1, len(folder) + 1) if self.presence(folder[:index]) != "present")
        self.ops.make_dirs(self.path(folder))
        return [folder[:index] for index in range(first, len(folder) + 1)]

    def remove_empty(self, created: list[Parts]) -> None:
        """Undo `make_parent` for a commit refused before its commit point: remove, innermost
        first, each directory it created that is still an empty real directory. Anything that
        appeared in one since (another writer's file) keeps it and every directory above it."""
        for folder in sorted(set(created), key=len, reverse=True):
            path = self.path(folder)
            try:
                self.guard.check_read(folder)  # never through a link (or a swapped root) into elsewhere
                info = lstat_or_none(path)
                if info is None or not stat.S_ISDIR(info.st_mode) or is_link(path):
                    continue
                if not self.ops.list_dir(path):
                    self.ops.remove_dir(path)
            except (OSError, StoreError, ValueError):  # LibraryPathError is a ValueError
                continue  # left behind, harmless: an empty directory holds no record


@dataclass
class Settlement:
    """What publication or recovery did, entry by entry."""

    published: list[tuple[str, str]] = field(default_factory=list)  # (target, etag)
    preserved: list[str] = field(default_factory=list)  # conflict paths
    incomplete: list[str] = field(default_factory=list)  # targets whose staging vanished
    dropped: list[str] = field(default_factory=list)  # keep/state targets set aside unpublished
    aborted: bool = False


def _text(parts: Parts) -> str:
    return "/".join(parts)


def check_entry_paths(access: Access, journal: Journal) -> None:
    """Every target and conflict path is writable without following a link or entering V1."""
    access.guard.check_session()
    access.check_staging()
    access.guard.check_write((TXN, JOURNAL), staging_device=None)
    staging = lstat_or_none(access.path((TXN,)))
    device = staging.st_dev if staging is not None else None
    staged = set(access.ops.list_dir(access.path((TXN,))))
    for entry in journal.entries:
        if entry.temp in staged:
            access.check_staged_file(entry.temp)
        access.guard.check_write(entry.target, staging_device=device)
        if entry.kind not in REGENERABLE_KINDS and entry.kind != "hold":  # dropped, or (a hold) own slots
            access.guard.check_write(conflict_parts(entry.target, entry.new), staging_device=device)
            access.check_conflict_folder(conflict_parts(entry.target, entry.new))


def _replace_journal(access: Access, journal: Journal) -> None:
    access.check_staging()
    temp = access.ops.make_temp(access.path((TXN,)), prefix="journal", suffix=".new")
    access.ops.write_bytes(temp, journal.render())
    access.ops.replace(temp, access.journal)


def _preserve(access: Access, entry: Entry, settlement: Settlement, digest: str) -> None:
    """Move a staged version into ``.conflicts``, named by its *actual* digest; never drop distinct bytes.

    A replica, keep or state entry is dropped instead (its temp removed): see the module docstring.
    """
    if entry.kind in REGENERABLE_KINDS:
        access.ops.remove(access.path((TXN, entry.temp)))
        settlement.dropped.append(_text(entry.target))
        return
    if entry.kind == "hold":  # a kept version: preserve it beside its record's other versions
        _preserve_hold(access, entry, settlement, digest)
        return
    staging = lstat_or_none(access.path((TXN,)))
    temp = access.path((TXN, entry.temp))
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        conflict = conflict_parts(entry.target, digest, attempt)
        access.guard.check_write(conflict, staging_device=staging.st_dev if staging else None)
        access.check_conflict_folder(conflict)  # a renamed folder never resolves by renaming the file
        if access.presence(conflict) == "absent":
            access.make_parent(conflict)
            access.ops.replace(temp, access.path(conflict))
            break
        if access.etag_of(conflict) == digest:
            access.ops.remove(temp)  # the same bytes were preserved by an earlier attempt
            break
        # an edited conflict file or a look-alike name: keep both, try the next name
    else:
        raise StoreError(f"no free conflict name for {_text(entry.target)} after {MAX_CONFLICT_ATTEMPTS} tries")
    settlement.preserved.append(_text(conflict))


def _prepare(access: Access, entry: Entry) -> None:
    """Before the per-entry guard read: re-check the path and create a new file's directories.

    Doing this first keeps the window between the guard read and the ``replace`` to the
    ``replace`` alone (R7). An update never creates directories: its present target proves
    the parent exists, and a parent deleted while the app was down is not re-created.
    """
    staging = lstat_or_none(access.path((TXN,)))
    access.guard.check_write(entry.target, staging_device=staging.st_dev if staging else None)
    if entry.old is None and access.presence(entry.target[:-1]) == "absent":
        access.make_parent(entry.target)  # a look-alike parent is never shadowed: the guard reads LOOKALIKE


def _preserve_hold(access: Access, entry: Entry, settlement: Settlement, digest: str) -> None:
    """A ``hold`` that cannot take its planned slot takes the record's next free one, never a nested name."""
    folder, name = entry.target[:-1], entry.target[-1]
    base = name.rsplit(".", 1)[0] if name.rsplit(".", 1)[-1].isdigit() else name
    staging = lstat_or_none(access.path((TXN,)))
    temp = access.path((TXN, entry.temp))
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        slot = folder + ((base if not attempt else f"{base}.{attempt}"),)
        access.guard.check_write(slot, staging_device=staging.st_dev if staging else None)
        access.check_conflict_folder(slot)
        if access.presence(slot) == "absent":
            access.make_parent(slot)
            access.ops.replace(temp, access.path(slot))
            break
        if access.etag_of(slot) == digest:
            access.ops.remove(temp)
            break
    else:
        raise StoreError(f"no free kept-version slot for {_text(entry.target)} after {MAX_CONFLICT_ATTEMPTS} tries")
    settlement.preserved.append(_text(slot))


def _publish(access: Access, entry: Entry, settlement: Settlement) -> None:
    staging = lstat_or_none(access.path((TXN,)))
    access.guard.check_write(entry.target, staging_device=staging.st_dev if staging else None)
    access.ops.replace(access.path((TXN, entry.temp)), access.path(entry.target))
    settlement.published.append((_text(entry.target), entry.new))


def settle(access: Access, journal: Journal, *, live: bool = False) -> Settlement:
    """Finish or preserve a committed journal, then remove it and any leftover staging.

    ``live`` is the committing process itself, right after the commit point: none
    of its staging can have been published yet, so a missing staged file is
    incomplete unless the target already holds the new bytes. In recovery a
    missing temp normally means "already published", and the disk is kept.
    """
    settlement = Settlement()
    access.check_staging()  # point-in-time checks: repeat them before every phase that mutates
    staged = {name for name in access.ops.list_dir(access.path((TXN,))) if is_staging_name(name)}

    def temp_digest(entry: Entry) -> str | None:
        if entry.temp not in staged:
            return None
        access.check_staged_file(entry.temp)  # never read through a link or block on a FIFO
        return etag(access.ops.read_bytes(access.path((TXN, entry.temp))))

    if journal.state == "commit" and all(entry.temp in staged for entry in journal.entries):
        for entry in journal.entries:  # pre-publish guard: all or nothing while nothing is published
            if temp_digest(entry) != entry.new or access.etag_of(entry.target) not in (entry.old, entry.new):
                journal = journal.aborted()
                _replace_journal(access, journal)
                break
    vanish_abort = False
    if journal.state == "commit" and not all(entry.temp in staged for entry in journal.entries):
        # A staged file is gone (another program removed it). An entry counts as published only when
        # its target already holds its new bytes; a missing one whose target does not is unwritable.
        # While nothing is published -- always true live, right after the commit point -- the whole
        # journal is abandoned, so a head move never lands without its revision. A revision replace
        # is abandoned with its hold the same way (019): in recovery, a hold that vanished
        # unpublished while another entry is still staged proves nothing was published either.
        current = {entry.target: access.etag_of(entry.target) for entry in journal.entries}
        missing = [entry for entry in journal.entries
                   if entry.temp not in staged and current[entry.target] != entry.new]
        published = any(entry.temp not in staged and current[entry.target] == entry.new
                        for entry in journal.entries)
        held = (any(entry.kind == "hold" for entry in missing)
                and any(entry.temp in staged for entry in journal.entries))
        if missing and (live or not published or held):
            journal, vanish_abort = journal.aborted(), True
            _replace_journal(access, journal)
    unwritten = set()  # targets of entries that will never be published
    for entry in journal.entries:
        if entry.temp not in staged:
            current_etag = access.etag_of(entry.target)
            vanished = current_etag != entry.new if live else (entry.old != entry.new and current_etag == entry.old)
            if (journal.state == "commit" or vanish_abort) and vanished:
                settlement.incomplete.append(_text(entry.target))
                unwritten.add(entry.target)
    held_sources = {_source_of_revision(entry.target) for entry in journal.entries
                    if entry.target in unwritten} - {None}
    for entry in journal.entries:
        if entry.temp not in staged:
            continue
        digest = temp_digest(entry)
        if journal.state == "abort" or digest != entry.new:
            _preserve(access, entry, settlement, digest)
            continue
        if _manifest_source(entry.target) in held_sources:
            # Some entries were published already, so the rest finish -- but never a head move whose
            # revision record will not be: its staged bytes are kept under .conflicts, not lost.
            _preserve(access, entry, settlement, digest)
            settlement.incomplete.append(_text(entry.target))
            continue
        _prepare(access, entry)
        current = access.etag_of(entry.target)  # per-entry guard, immediately before acting
        if current == entry.old:
            _publish(access, entry, settlement)
        elif current == entry.new:
            access.ops.remove(access.path((TXN, entry.temp)))
        else:
            _preserve(access, entry, settlement, digest)
    settlement.aborted = journal.state == "abort"
    access.ops.remove(access.journal)
    clean_staging(access)
    return settlement


def _source_of_revision(target: Parts) -> str | None:
    found = classify(target)
    return found[1].source if found is not None and found[0] == "definition" else None  # type: ignore[union-attr]


def _manifest_source(target: Parts) -> str | None:
    found = classify(target)
    return found[1] if found is not None and found[0] == "manifest" else None  # type: ignore[return-value]


def clean_staging(access: Access) -> None:
    """Remove uncommitted staging; only valid while no journal exists."""
    access.check_staging()
    for name in access.ops.list_dir(access.path((TXN,))):
        if is_staging_name(name):
            access.ops.remove(access.path((TXN, name)))


def recover(access: Access) -> Settlement:
    """Validate the root, the journal and every path it names before changing anything."""
    try:
        access.guard.check_session()  # even with no journal: cleanup must not follow a swapped root
        access.check_staging()
        if access.discard_pending():
            raise RecoveryFailed("a discard was interrupted; run LibraryStore.discard_journal again to finish it")
        if access.repair_pending():
            raise RecoveryFailed("a ledger repair was interrupted; run LibraryStore.repair_ledger again to finish it")
        if access.journal_exists():
            access.check_staged_file(JOURNAL)
    except (StoreError, ValueError) as error:  # LibraryPathError is a ValueError
        raise RecoveryFailed(f"refusing to recover through an unsafe root or staging: {error}") from error
    if not access.journal_exists():
        if access.presence((TXN,)) == "present":
            clean_staging(access)
        return Settlement()
    journal = parse(access.ops.read_bytes(access.journal))
    try:
        check_entry_paths(access, journal)
    except (StoreError, ValueError) as error:  # LibraryPathError is a ValueError
        raise RecoveryFailed(f"refusing to recover through an unsafe path: {error}") from error
    try:
        return settle(access, journal)
    except (StoreError, ValueError) as error:  # a path became unsafe while recovering
        raise RecoveryFailed(f"refusing to recover through an unsafe path: {error}") from error
