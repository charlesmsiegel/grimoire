"""One writer per library: an OS advisory lock the kernel drops when a process dies.

The lock file is created by the store through its file seam and is only ever
opened here, never created, so locking is not an unmediated write. ``Locker``
is injectable: crash-exploration tests, which run "restarted processes" inside
one interpreter, use ``ProcessLocker``.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Protocol

from .errors import LibraryLocked, LockUnavailable

_HELD = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", errno.EDEADLK)})
_WINDOWS_LOCK_OFFSET = 1 << 30  # beyond any byte of the (empty) lock file


class Locker(Protocol):
    def acquire(self, path: Path) -> object: ...
    def release(self, handle: object) -> None: ...


class OsLocker:
    """``flock`` on POSIX, ``msvcrt.locking`` on Windows; non-blocking."""

    def acquire(self, path: Path) -> object:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        try:
            self._lock(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _lock(descriptor: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in _HELD:
                raise LibraryLocked("another writer holds this library") from error
            raise LockUnavailable(f"this filesystem cannot lock the library: {error}") from error

    def release(self, handle: object) -> None:
        descriptor = int(handle)  # type: ignore[call-overload]
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # closing the descriptor releases it anyway
        finally:
            os.close(descriptor)


class ProcessLocker:
    """Exclusion within one interpreter, for tests that simulate restarts in-process."""

    def __init__(self) -> None:
        self.held: set[str] = set()

    def acquire(self, path: Path) -> object:
        key = os.path.normcase(os.path.realpath(path))
        if key in self.held:
            raise LibraryLocked("another writer holds this library")
        self.held.add(key)
        return key

    def release(self, handle: object) -> None:
        self.held.discard(handle)  # type: ignore[arg-type]
