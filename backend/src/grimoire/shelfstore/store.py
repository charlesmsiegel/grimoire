"""The shelf's files inside one library: ``config/shelf/{entries,chats,filters}/<name>.json``.

One small file per annotated entry, per pinned chat and per saved filter, so two devices that change different
things never touch the same file. A record that says nothing any more (no tag, not pinned) is removed. Every
write names the etag of the file it read (``None``: it saw none) and is refused ``StaleWrite`` when the file
moved on. A write is one temp file renamed over its target, a removal one ``remove``: a crash leaves the old
record or the new one, and at worst a temp file that the next write of that folder sweeps (``recover``).

Reading never fails on one bad file: it is listed in ``ShelfView.problems`` with its path and reason, and left
exactly as it is. A write to a record whose file cannot be read is refused (``unreadable_record``) rather than
written over, so a hand edit or a copy another tool left is never lost.

All file access goes through the ``FileOps`` seam (``grimoire.store.DiskOps`` by default), so the writer runs
under the fault harness (docs/TESTING.md). The caller serializes writers (the app's one writer lock).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Generic, TypeVar

from grimoire.core.identity import ChatRef, SourceRef
from grimoire.shelf import (
    MAX_FILTERS, ChatMarks, EntryMarks, SavedFilter, ShelfError, chat_from_document, chat_to_document,
    check_filter_id, edit_tags, entry_from_document, entry_to_document, filter_from_document, filter_to_document,
    record_name, tag_key,
)
from grimoire.store import DiskOps, FileOps, StaleWrite, etag
from grimoire.store.diskops import is_link
from grimoire.store.jsonfmt import JsonError, parse_strict, render, thaw
from grimoire.store.layout import is_noise

#: Where the shelf lives under a library root. ``config`` is a root name the store reserves and never reads.
CONFIG, SHELF = "config", "shelf"
ENTRIES, CHATS, FILTERS = "entries", "chats", "filters"
FOLDERS = (ENTRIES, CHATS, FILTERS)
SUFFIX = ".json"
TEMP_PREFIX, TEMP_SUFFIX = ".shelf-", ".tmp"
#: A shelf record is a few lines; a larger file is not one.
MAX_RECORD_BYTES = 64 * 1024

T = TypeVar("T")


@dataclass(frozen=True)
class Stored(Generic[T]):
    """One record as it is on disk: its value and the etag of its exact bytes."""

    value: T
    etag: str


@dataclass(frozen=True)
class Problem:
    """A file under the shelf that is not a readable record: where, a reason and what it says."""

    path: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ShelfView:
    """Everything the shelf holds now. A file that could not be read is in ``problems``, never in the rest."""

    entries: Mapping[SourceRef, Stored[EntryMarks]] = field(default_factory=dict)
    chats: Mapping[ChatRef, Stored[ChatMarks]] = field(default_factory=dict)
    filters: Mapping[str, Stored[SavedFilter]] = field(default_factory=dict)
    problems: tuple[Problem, ...] = ()


def _temp(name: str) -> bool:
    return name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX)


class ShelfStore:
    """The shelf of the library at ``root``. Holds no state between calls: every call reads the files."""

    def __init__(self, root: str | os.PathLike[str], *, ops: FileOps | None = None,
                 check_read: Callable[[], None] | None = None, check_write: Callable[[], None] | None = None) -> None:
        """``check_read`` and ``check_write`` are the open library's own root guards (``LibraryStore.check_root``
        and ``check_destination``): called before this store reads and before it changes anything, they refuse
        when the root is no longer the directory that was opened (a root swapped for a link would otherwise be
        followed, since the link checks below start under it). Without them only the links under the root are
        refused, which is all a bare folder can promise."""
        self.root = Path(root)
        self.ops: FileOps = ops if ops is not None else DiskOps()
        self._check_read = check_read or (lambda: None)
        self._check_write = check_write or (lambda: None)

    # -- paths -------------------------------------------------------------------------------------------
    def _folder(self, folder: str) -> Path:
        return self.root / CONFIG / SHELF / folder

    @staticmethod
    def path_of(folder: str, stem: str) -> str:
        """The root-relative path of one record, as problems and refusals name it."""
        return f"{CONFIG}/{SHELF}/{folder}/{stem}{SUFFIX}"

    def _linked(self, folder: str, name: str | None = None) -> str | None:
        """The first part of the way to a record that is a link, or ``None``. A link is never followed: it could
        lead out of the library."""
        steps = [CONFIG, SHELF, folder] + ([name] if name is not None else [])
        at = self.root
        for index, step in enumerate(steps):
            at = at / step
            if is_link(at):
                return "/".join(steps[:index + 1])
        return None

    # -- reading -----------------------------------------------------------------------------------------
    def _bytes(self, folder: str, stem: str) -> bytes | None:
        """One record file's exact bytes, or ``None`` when there is none."""
        self._check_read()
        linked = self._linked(folder, stem + SUFFIX)
        if linked is not None:
            raise ShelfError("link", f"{linked} is a link; the shelf does not follow links")
        path = self._folder(folder) / (stem + SUFFIX)
        if not self.ops.exists(path):
            return None
        return self.ops.read_bytes(path)

    @staticmethod
    def _decode(folder: str, stem: str, data: bytes):  # type: ignore[no-untyped-def]
        """The record ``data`` holds at ``folder/stem``; ``ShelfError`` with the reason it is not one."""
        if len(data) > MAX_RECORD_BYTES:
            raise ShelfError("too_large", f"larger than {MAX_RECORD_BYTES} bytes")
        try:
            document = thaw(parse_strict(data))
        except JsonError as error:
            raise ShelfError("malformed", str(error)) from None
        if folder == ENTRIES:
            value = entry_from_document(document)
            named = record_name(value.ref)
        elif folder == CHATS:
            value = chat_from_document(document)
            named = record_name(value.chat)
        else:
            value = filter_from_document(document)
            named = value.id
        if named != stem:
            raise ShelfError("address_mismatch", f"it holds the record that belongs in {named}{SUFFIX}")
        return value

    def _scan(self, folder: str, problems: list[Problem]) -> dict[str, Stored]:  # type: ignore[type-arg]
        found: dict[str, Stored] = {}  # type: ignore[type-arg]
        self._check_read()
        linked = self._linked(folder)
        if linked is not None:
            problems.append(Problem(linked, "link", "a link is not followed"))
            return found
        directory = self._folder(folder)
        if not self.ops.exists(directory):
            return found
        try:
            names = self.ops.list_dir(directory)
        except OSError as error:
            problems.append(Problem(f"{CONFIG}/{SHELF}/{folder}", "unreadable", error.strerror or type(error).__name__))
            return found
        for name in names:
            if _temp(name) or is_noise(name):
                continue
            path = f"{CONFIG}/{SHELF}/{folder}/{name}"
            if not name.endswith(SUFFIX) or len(name) == len(SUFFIX):
                problems.append(Problem(path, "unexpected_file", "not a shelf record; it is left as it is"))
                continue
            stem = name[:-len(SUFFIX)]
            try:
                data = self._bytes(folder, stem)
                if data is None:
                    continue
                value = self._decode(folder, stem, data)
            except ShelfError as error:
                problems.append(Problem(path, error.reason, str(error)))
                continue
            except OSError as error:
                problems.append(Problem(path, "unreadable", error.strerror or type(error).__name__))
                continue
            found[stem] = Stored(value, etag(data))
        return found

    def view(self) -> ShelfView:
        """Every record, and every file that is not one."""
        problems: list[Problem] = []
        for name in (CONFIG, f"{CONFIG}/{SHELF}"):
            if is_link(self.root / name):
                return ShelfView(problems=(Problem(name, "link", "a link is not followed"),))
        entries = {stored.value.ref: stored for stored in self._scan(ENTRIES, problems).values()}
        chats = {stored.value.chat: stored for stored in self._scan(CHATS, problems).values()}
        filters = {stored.value.id: stored for stored in self._scan(FILTERS, problems).values()}
        return ShelfView(MappingProxyType(entries), MappingProxyType(chats), MappingProxyType(filters),
                         tuple(sorted(problems, key=lambda problem: problem.path)))

    def _current(self, folder: str, stem: str):  # type: ignore[no-untyped-def]
        """(the record at ``folder/stem`` or ``None``, its bytes or ``None``); a file there that is not a record
        is refused, never treated as absent."""
        data = self._bytes(folder, stem)
        if data is None:
            return None, None
        try:
            return self._decode(folder, stem, data), data
        except ShelfError as error:
            raise ShelfError("unreadable_record", f"{self.path_of(folder, stem)} cannot be read ({error.reason}: "
                             f"{error}); mend or remove that file first") from None

    # -- writing -----------------------------------------------------------------------------------------
    def _check(self, folder: str, stem: str, data: bytes | None, expected: str | None) -> None:
        held = None if data is None else etag(data)
        if held != expected:
            raise StaleWrite([(self.path_of(folder, stem), "changed" if held is not None else "removed")])

    def _publish(self, folder: str, stem: str, current: bytes | None, data: bytes | None, expected: str | None
                 ) -> str | None:
        """Replace the record at ``folder/stem`` (``data`` ``None``: remove it) if it is still at ``expected``.
        Returns the new etag, or ``None`` when no record is left."""
        if expected is not None and (type(expected) is not str or not expected.startswith("sha256:")):
            raise ShelfError("invalid_field", "expected must be the etag that was read, or null")
        self._check(folder, stem, current, expected)
        directory = self._folder(folder)
        target = directory / (stem + SUFFIX)
        if data is not None and data == current:
            return etag(data)
        self._check_write()  # before anything is made, removed or replaced: the root is still the library's own
        if data is None:
            if current is not None:
                self.ops.remove(target, durable=True)
            return None
        if data == current:
            return etag(data)
        linked = self._linked(folder)
        if linked is not None:
            raise ShelfError("link", f"{linked} is a link; the shelf does not follow links")
        self.ops.make_dirs(directory)
        self._sweep(folder)
        temp = self.ops.make_temp(directory, prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX)
        try:
            self.ops.write_bytes(temp, data)
            self._check(folder, stem, self._bytes(folder, stem), expected)  # an edit made meanwhile wins: refused
            self._check_write()
            self.ops.replace(temp, target)
        except Exception:  # an I/O error or a refusal; a dead process cleans nothing (``recover`` sweeps)
            try:
                self.ops.remove(temp)
            except OSError:
                pass
            raise
        return etag(data)

    def _sweep(self, folder: str) -> None:
        directory = self._folder(folder)
        if self._linked(folder) is not None or not self.ops.exists(directory):
            return
        for name in self.ops.list_dir(directory):
            if _temp(name):
                self.ops.remove(directory / name)

    def recover(self) -> None:
        """Remove what a write that died left behind: its temp file. Records are never touched."""
        self._check_write()
        if any(is_link(self.root / name) for name in (CONFIG, f"{CONFIG}/{SHELF}")):
            return
        for folder in FOLDERS:
            self._sweep(folder)

    def _put(self, folder: str, stem: str, current: bytes | None, value, expected: str | None,  # type: ignore[no-untyped-def]
             document: Callable[[object], Mapping[str, object]]):
        data = None if value is None else render(document(value))
        new = self._publish(folder, stem, current, data, expected)
        return None if new is None else Stored(value, new)

    # -- entries -----------------------------------------------------------------------------------------
    def _entry(self, ref: SourceRef, change: Callable[[EntryMarks], EntryMarks], expected: str | None
               ) -> Stored[EntryMarks] | None:
        blank = EntryMarks(ref)  # refuses a pinned ref before anything is read
        stem = record_name(ref)
        held, data = self._current(ENTRIES, stem)
        marks = change(held if held is not None else blank)
        return self._put(ENTRIES, stem, data, None if marks.empty else marks, expected, entry_to_document)

    def set_tags(self, ref: SourceRef, tags: Iterable[object], expected: str | None) -> Stored[EntryMarks] | None:
        """Give the entry exactly ``tags``. Returns its record, or ``None`` when nothing is left to say of it."""
        return self._entry(ref, lambda held: EntryMarks(ref, tuple(tags), held.pinned), expected)  # type: ignore[arg-type]

    def edit_tags(self, ref: SourceRef, add: Iterable[object], remove: Iterable[object], expected: str | None
                  ) -> Stored[EntryMarks] | None:
        """Add and take away tags, leaving the entry's others as they are."""
        return self._entry(ref, lambda held: EntryMarks(ref, edit_tags(held.tags, add, remove), held.pinned), expected)

    def pin_entry(self, ref: SourceRef, pinned: bool, expected: str | None) -> Stored[EntryMarks] | None:
        return self._entry(ref, lambda held: EntryMarks(ref, held.tags, pinned), expected)

    # -- chats -------------------------------------------------------------------------------------------
    def pin_chat(self, chat: ChatRef, pinned: bool, expected: str | None) -> Stored[ChatMarks] | None:
        marks = ChatMarks(chat, pinned)
        stem = record_name(chat)
        _held, data = self._current(CHATS, stem)
        return self._put(CHATS, stem, data, None if marks.empty else marks, expected, chat_to_document)

    # -- saved filters -----------------------------------------------------------------------------------
    def _name_free(self, name: str, but: str | None = None) -> None:
        problems: list[Problem] = []
        others = [stored.value for stored in self._scan(FILTERS, problems).values() if stored.value.id != but]
        if any(tag_key(other.name) == tag_key(name) for other in others):
            raise ShelfError("filter_name_taken", f"a saved filter is already called {name!r}")
        if but is None and len(others) >= MAX_FILTERS:
            raise ShelfError("too_many_filters", f"at most {MAX_FILTERS} saved filters")

    def save_filter(self, saved: SavedFilter) -> Stored[SavedFilter]:
        """Keep a new saved filter. Its id must be unused and its name unlike every other's (any case)."""
        if self._bytes(FILTERS, saved.id) is not None:
            raise ShelfError("filter_exists", f"a saved filter {saved.id} already exists")
        self._name_free(saved.name)
        return self._put(FILTERS, saved.id, None, saved, None, filter_to_document)

    def rename_filter(self, filter_id: str, name: object, expected: str | None) -> Stored[SavedFilter]:
        held, data = self._current(FILTERS, check_filter_id(filter_id))
        if held is None:
            raise ShelfError("no_filter", f"no saved filter {filter_id}")
        renamed = held.renamed(name)
        self._check(FILTERS, filter_id, data, expected)
        self._name_free(renamed.name, but=filter_id)
        return self._put(FILTERS, filter_id, data, renamed, expected, filter_to_document)

    def delete_filter(self, filter_id: str, expected: str | None) -> None:
        held, data = self._current(FILTERS, check_filter_id(filter_id))
        if held is None:
            raise ShelfError("no_filter", f"no saved filter {filter_id}")
        self._put(FILTERS, filter_id, data, None, expected, filter_to_document)


__all__ = ["CHATS", "CONFIG", "ENTRIES", "FILTERS", "FOLDERS", "MAX_RECORD_BYTES", "SHELF", "Problem", "ShelfStore",
           "ShelfView", "Stored"]
