"""A read-only snapshot of a frozen V0.2.1 library.

Nothing here writes, creates, renames or follows a link. On POSIX the walk and
every later read go through directory descriptors opened ``O_NOFOLLOW`` from a
root descriptor held for the whole scan, and each directory and file is checked
by ``(st_dev, st_ino)`` against what the walk recorded, so an entry swapped for
a link after it was listed is never followed. Windows has no descriptor-relative
walk, so there each step is a path ``lstat`` before and an ``fstat`` identity
check after; the quiescence pass is the backstop there.

Every regular file that is not excluded or ignored OS litter is hashed during
the walk. An
interpreter that wants the bytes calls `Snapshot.read`, which re-opens the
file, and the bytes it returns must hash to the walk's digest: what is parsed
is what is reported.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field

from . import _layout
from ._layout import Layout

#: Largest file an interpreter reads into memory; larger ones are only hashed.
READ_CAP = 16 << 20
_CHUNK = 1 << 20

_FLAG = lambda name: getattr(os, name, 0)  # noqa: E731
_FILE_FLAGS = (os.O_RDONLY | _FLAG("O_NOFOLLOW") | _FLAG("O_NONBLOCK") | _FLAG("O_NOCTTY")
               | _FLAG("O_CLOEXEC") | _FLAG("O_BINARY") | _FLAG("O_NOINHERIT"))
_DIR_FLAGS = os.O_RDONLY | _FLAG("O_DIRECTORY") | _FLAG("O_NOFOLLOW") | _FLAG("O_CLOEXEC")
#: Descriptor-relative traversal: every POSIX platform CPython supports here.
FD_WALK = (os.scandir in os.supports_fd and os.open in os.supports_dir_fd
           and os.stat in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
           and hasattr(os, "O_NOFOLLOW"))
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class SourceChanged(RuntimeError):
    """The library changed while it was being read; the inventory would be untrue."""


class TooLarge(ValueError):
    """A file over the read cap: hashed, but not parsed."""


class NotADirectory(ValueError):
    """The chosen source is not a directory."""


@dataclass
class Entry:
    path: str
    kind: str                  # "dir", "file", "link", "fifo", "socket", "device", "other"
    layout: Layout
    disposition: str
    identity: tuple[int, int]  # (st_dev, st_ino)
    stamp: tuple               # what the quiescence pass compares
    size: int | None = None
    sha256: str | None = None
    reason: str | None = None  # why refused, unreadable or excluded


@dataclass
class WalkNote:
    """Something the walk itself has to report (a finding, in report terms)."""
    code: str
    severity: str
    path: str
    detail: str = ""


@dataclass
class Options:
    #: The device an entry lives on (a seam: tests fake a mount point).
    device_of: Callable[[str, os.stat_result], int] | None = None
    read_cap: int = READ_CAP
    #: ``(path, operation) -> errno or None``: inject an I/O failure (a seam).
    fail: Callable[[str, str], int | None] | None = None
    #: Called between the first walk and the quiescence walk (a seam).
    after_first_pass: Callable[[], None] | None = None
    #: Force the path-based walk even where descriptors are available (a seam).
    path_walk: bool = False


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        return "device"
    return "other"


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def errno_name(error: OSError) -> str:
    return errno.errorcode.get(error.errno or 0, "EIO")


@dataclass
class Snapshot:
    root: str
    options: Options = field(default_factory=Options)
    entries: dict[str, Entry] = field(default_factory=dict)
    notes: list[WalkNote] = field(default_factory=list)
    read_paths: set[str] = field(default_factory=set)
    _root_fd: int | None = None
    _root_identity: tuple[int, int] = (0, 0)
    _root_device: int = 0

    # ---- lifecycle ----------------------------------------------------------------

    @classmethod
    def take(cls, root: str, options: Options | None = None) -> Snapshot:
        snapshot = cls(root, options or Options())
        snapshot._open_root()
        try:
            snapshot._walk()
        except BaseException:
            snapshot.close()
            raise
        return snapshot

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    @property
    def descriptor_walk(self) -> bool:
        return FD_WALK and not self.options.path_walk

    def _open_root(self) -> None:
        info = os.lstat(self.root)
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise NotADirectory(self.root)
        self._root_identity = (info.st_dev, info.st_ino)
        self._root_device = info.st_dev
        if self.descriptor_walk:
            fd = os.open(self.root, _DIR_FLAGS)
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != self._root_identity:
                os.close(fd)
                raise SourceChanged("the source root changed while it was opened")
            self._root_fd = fd

    def verify_unchanged(self) -> None:
        """Walk again; any difference in what was seen means the source was live."""
        if self.options.after_first_pass is not None:
            self.options.after_first_pass()
        again = Snapshot.take(self.root, Options(device_of=self.options.device_of,
                                                 read_cap=self.options.read_cap,
                                                 fail=self.options.fail,
                                                 path_walk=self.options.path_walk))
        try:
            if again._root_identity != self._root_identity or again.signature() != self.signature():
                raise SourceChanged("the source changed during the scan")
        finally:
            again.close()

    def signature(self) -> list[tuple]:
        return sorted((e.path, e.kind, e.disposition, e.size, e.sha256, e.stamp)
                      for e in self.entries.values())

    # ---- walking --------------------------------------------------------------------

    def _fail(self, path: str, operation: str) -> None:
        hook = self.options.fail
        code = hook(path, operation) if hook is not None else None
        if code is not None:
            raise OSError(code, os.strerror(code))

    def _walk(self) -> None:
        stack: list[tuple[str, ...]] = [()]
        while stack:
            parts = stack.pop()
            for child in reversed(self._list(parts)):
                stack.append(child)

    def _list(self, parts: tuple[str, ...]) -> list[tuple[str, ...]]:
        """Record the children of the directory at `parts`; return the subdirectories to walk."""
        rel = "/".join(parts)
        dfd = None
        try:
            self._fail(rel, "list")
            if self.descriptor_walk:
                dfd = self._open_dir(parts)
                with os.scandir(dfd) as listing:
                    names = sorted(item.name for item in listing)
            else:
                self._same_directory(parts)
                with os.scandir(self._path(parts)) as listing:
                    names = sorted(item.name for item in listing)
                self._same_directory(parts)
        except SourceChanged:
            if dfd is not None:
                os.close(dfd)
            raise
        except OSError as error:
            if dfd is not None and dfd != self._root_fd:
                os.close(dfd)
            if not parts:
                raise
            self._unreadable(self.entries[rel], error)
            return []
        try:
            return [child for name in names
                    if (child := self._visit(parts + (name,), dfd)) is not None]
        finally:
            if dfd is not None and dfd != self._root_fd:
                os.close(dfd)

    def _same_directory(self, parts: tuple[str, ...]) -> None:
        """Path walk only: the directory is still the one recorded (not swapped for a junction)."""
        info = os.lstat(self._path(parts))
        expected = self._root_identity if not parts else self.entries["/".join(parts)].identity
        if _is_reparse(info) or not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected:
            raise SourceChanged(f"{'/'.join(parts) or 'the source root'} changed during the scan")

    def _path(self, parts: tuple[str, ...]) -> str:
        return os.path.join(self.root, *parts)

    def _lstat(self, parts: tuple[str, ...], dfd: int | None) -> os.stat_result:
        self._fail("/".join(parts), "stat")
        if dfd is not None:
            return os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
        return os.lstat(self._path(parts))

    def _visit(self, parts: tuple[str, ...], dfd: int | None) -> tuple[str, ...] | None:
        rel = "/".join(parts)
        try:
            info = self._lstat(parts, dfd)
        except FileNotFoundError:
            raise SourceChanged(f"{rel} vanished during the scan") from None
        except OSError as error:
            entry = Entry(rel, "other", _layout.classify(parts, False), _layout.UNREADABLE, (0, 0), ())
            self.entries[rel] = entry
            self._unreadable(entry, error)
            return None
        kind = "link" if _is_reparse(info) else _kind(info.st_mode)
        layout = _layout.classify(parts, kind == "dir")
        entry = Entry(rel, kind, layout, layout.disposition, (info.st_dev, info.st_ino),
                      (kind, info.st_size if kind == "file" else None, info.st_mtime_ns,
                       info.st_ctime_ns, info.st_ino, info.st_dev),
                      size=info.st_size if kind == "file" else None)
        self.entries[rel] = entry
        if layout.disposition == _layout.EXCLUDED:
            if kind == "link":
                self.notes.append(WalkNote("excluded_link", "info", rel))
            entry.reason = "excluded"
            return None
        if kind == "link":
            return self._refuse(entry, "link")
        if kind in ("fifo", "socket", "device", "other"):
            return self._refuse(entry, kind)
        device = self.options.device_of(rel, info) if self.options.device_of else info.st_dev
        if device != self._root_device:
            return self._refuse(entry, "mount_point")
        if kind == "dir":
            return parts
        if layout.disposition == _layout.IGNORED:
            return None
        if info.st_nlink > 1:
            self.notes.append(WalkNote("hard_link", "info", rel, str(info.st_nlink)))
        try:
            entry.sha256 = self._hash(parts, dfd, entry)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
                raise SourceChanged(f"{rel} changed during the scan") from None
            self._unreadable(entry, error)
        return None

    def _refuse(self, entry: Entry, reason: str) -> None:
        entry.disposition = _layout.REFUSED
        entry.reason = reason
        entry.size = None
        code = "mount_point" if reason == "mount_point" else "refused_entry"
        self.notes.append(WalkNote(code, "blocking", entry.path, reason))
        return None

    def _unreadable(self, entry: Entry, error: OSError) -> None:
        entry.disposition = _layout.UNREADABLE
        entry.reason = errno_name(error)
        entry.sha256 = None
        self.notes.append(WalkNote("unreadable", "blocking", entry.path, entry.reason))

    # ---- descriptor helpers ---------------------------------------------------------

    def _open_dir(self, parts: tuple[str, ...]) -> int:
        """The directory at `parts`, reached one verified component at a time."""
        assert self._root_fd is not None
        current = self._root_fd
        try:
            for depth in range(len(parts)):
                fd = os.open(parts[depth], _DIR_FLAGS, dir_fd=current)
                if current != self._root_fd:
                    os.close(current)
                current = fd
                expected = self.entries.get("/".join(parts[:depth + 1]))
                opened = os.fstat(fd)
                if expected is None or (opened.st_dev, opened.st_ino) != expected.identity:
                    raise SourceChanged(f"{'/'.join(parts[:depth + 1])} changed during the scan")
        except OSError as error:
            if current != self._root_fd:
                os.close(current)
            if error.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
                raise SourceChanged(f"{'/'.join(parts)} changed during the scan") from None
            raise
        except SourceChanged:
            if current != self._root_fd:
                os.close(current)
            raise
        return current

    def _open_file(self, parts: tuple[str, ...], dfd: int | None, entry: Entry) -> int:
        self._fail(entry.path, "open")
        if dfd is not None:
            fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=dfd)
        else:
            fd = os.open(self._path(parts), _FILE_FLAGS)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != entry.identity:
            os.close(fd)
            raise SourceChanged(f"{entry.path} changed during the scan")
        return fd

    def _hash(self, parts: tuple[str, ...], dfd: int | None, entry: Entry) -> str:
        fd = self._open_file(parts, dfd, entry)
        digest = hashlib.sha256()
        try:
            while chunk := os.read(fd, _CHUNK):
                digest.update(chunk)
        finally:
            os.close(fd)
        return digest.hexdigest()

    # ---- interpreter reads ------------------------------------------------------------

    def read(self, path: str) -> bytes:
        """The bytes of a hashed file, verified to be the ones the walk hashed."""
        entry = self.entries[path]
        if entry.kind != "file" or entry.sha256 is None:
            raise ValueError(f"{path} was not hashed by the walk")
        self.read_paths.add(path)
        if entry.size is not None and entry.size > self.options.read_cap:
            raise TooLarge(path)
        parts = tuple(path.split("/"))
        dfd = self._open_dir(parts[:-1]) if self.descriptor_walk else None
        try:
            fd = self._open_file(parts, dfd, entry)
        except OSError:
            raise SourceChanged(f"{path} changed during the scan") from None
        finally:
            if dfd is not None and dfd != self._root_fd:
                os.close(dfd)
        chunks = []
        try:
            remaining = self.options.read_cap + 1
            while remaining > 0 and (chunk := os.read(fd, min(_CHUNK, remaining))):
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(fd)
        data = b"".join(chunks)
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise SourceChanged(f"{path} changed during the scan")
        return data

    def files(self, family: str) -> list[Entry]:
        """Readable entries of one layout family, in path order."""
        return sorted((e for e in self.entries.values()
                       if e.layout.family == family and e.disposition not in
                       (_layout.REFUSED, _layout.UNREADABLE, _layout.EXCLUDED)),
                      key=lambda e: e.path)

    def is_dir(self, path: str) -> bool:
        entry = self.entries.get(path)
        return entry is not None and entry.kind == "dir" and entry.disposition == _layout.DIRECTORY

    def is_file(self, path: str) -> bool:
        entry = self.entries.get(path)
        return entry is not None and entry.kind == "file" and entry.sha256 is not None
