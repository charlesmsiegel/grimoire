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

An ``abort`` journal never publishes. A commit journal whose temps are all
still present and which would preserve any entry is first rewritten as
``abort``, so a crash part-way through preserving can never resume as a
partial publish.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from grimoire.library import LibraryPathError

from .diskops import FileOps, is_link, lstat_or_none
from .errors import LinkRefused, NameCollision, RecoveryFailed, StoreError, UnstorableIdentifier
from .guard import Guard
from .journal import Entry, Journal, is_staging_name, parse
from .jsonfmt import MAX_FILE_BYTES, etag
from .layout import JOURNAL, TXN, Parts, conflict_parts, name_key

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
        self._listings: dict[Parts, list[str]] | None = None

    @contextmanager
    def cached_listings(self) -> Iterator[None]:
        """Reuse directory listings while nothing is written (one load or one preflight)."""
        outer, self._listings = self._listings, {} if self._listings is None else self._listings
        try:
            yield
        finally:
            self._listings = outer

    def list_dir(self, parts: Parts) -> list[str]:
        if self._listings is None:
            return self.ops.list_dir(self.path(parts))
        if parts not in self._listings:
            self._listings[parts] = self.ops.list_dir(self.path(parts))
        return self._listings[parts]

    def path(self, parts: Parts) -> Path:
        return self.root.joinpath(*parts)

    def presence(self, parts: Parts) -> Presence:
        """Whether ``parts`` exists spelled exactly so; ``mismatch`` if only a look-alike does.

        Decided from directory listings, never by opening a composed path: a
        case-insensitive filesystem would find ``Harbor`` when asked for ``harbor``.
        """
        for index, part in enumerate(parts):
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

    def make_parent(self, parts: Parts) -> None:
        if self.presence(parts[:-1]) != "present":
            self.ops.make_dirs(self.path(parts[:-1]))


@dataclass
class Settlement:
    """What publication or recovery did, entry by entry."""

    published: list[tuple[str, str]] = field(default_factory=list)  # (target, etag)
    preserved: list[str] = field(default_factory=list)  # conflict paths
    incomplete: list[str] = field(default_factory=list)  # targets whose staging vanished
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
        access.guard.check_write(conflict_parts(entry.target, entry.new), staging_device=device)
        access.check_conflict_folder(conflict_parts(entry.target, entry.new))


def _replace_journal(access: Access, journal: Journal) -> None:
    temp = access.ops.make_temp(access.path((TXN,)), prefix="journal", suffix=".new")
    access.ops.write_bytes(temp, journal.render())
    access.ops.replace(temp, access.journal)


def _preserve(access: Access, entry: Entry, settlement: Settlement, digest: str) -> None:
    """Move a staged version into ``.conflicts``, named by its *actual* digest; never drop distinct bytes."""
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


def _publish(access: Access, entry: Entry, settlement: Settlement) -> None:
    staging = lstat_or_none(access.path((TXN,)))
    access.guard.check_write(entry.target, staging_device=staging.st_dev if staging else None)
    access.make_parent(entry.target)
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
    for entry in journal.entries:
        if entry.temp not in staged:
            current = access.etag_of(entry.target)
            vanished = current != entry.new if live else (entry.old != entry.new and current == entry.old)
            if journal.state == "commit" and vanished:
                settlement.incomplete.append(_text(entry.target))
            continue
        digest = temp_digest(entry)
        if journal.state == "abort" or digest != entry.new:
            _preserve(access, entry, settlement, digest)
            continue
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


def clean_staging(access: Access) -> None:
    """Remove uncommitted staging; only valid while no journal exists."""
    for name in access.ops.list_dir(access.path((TXN,))):
        if is_staging_name(name):
            access.ops.remove(access.path((TXN, name)))


def recover(access: Access) -> Settlement:
    """Validate the root, the journal and every path it names before changing anything."""
    try:
        access.guard.check_session()  # even with no journal: cleanup must not follow a swapped root
        access.check_staging()
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
