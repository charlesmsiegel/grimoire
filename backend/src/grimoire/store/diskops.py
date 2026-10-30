"""The file-operation seam the store writes through, and its production implementation.

The store depends only on this structural protocol (the same operations as
the fault harness's seam), so tests run the real writer under injected crashes
and production code never imports the harness. Link and identity checks are
read-only metadata calls made outside the seam.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from typing import Protocol, Union

PathArg = Union[str, "os.PathLike[str]"]

# A directory fsync these errors reject is unsupported, not failed.
_UNSUPPORTED_DIRECTORY_SYNC = frozenset(
    code for code in (errno.EINVAL, getattr(errno, "ENOTSUP", None), getattr(errno, "EOPNOTSUPP", None),
                      errno.EBADF) if code is not None)
_UNOPENABLE_DIRECTORY = frozenset({errno.EACCES, errno.EISDIR})


class FileOps(Protocol):
    def read_bytes(self, path: PathArg) -> bytes: ...
    def exists(self, path: PathArg) -> bool: ...
    def list_dir(self, path: PathArg) -> list[str]: ...
    def write_bytes(self, path: PathArg, data: bytes) -> None: ...
    def make_temp(self, directory: PathArg, prefix: str = "", suffix: str = "") -> str: ...
    def replace(self, src: PathArg, dst: PathArg) -> None: ...
    def remove(self, path: PathArg) -> None: ...
    def make_dirs(self, path: PathArg) -> None: ...


def _sync_directory(path: PathArg) -> None:
    """Make a rename durable on POSIX; best effort where the OS cannot."""
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as error:
        if error.errno in _UNOPENABLE_DIRECTORY:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in _UNSUPPORTED_DIRECTORY_SYNC:
            raise
    finally:
        os.close(descriptor)


class DiskOps:
    """Real file operations: data and renames are synced before returning."""

    def read_bytes(self, path: PathArg) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def exists(self, path: PathArg) -> bool:
        return os.path.lexists(path)

    def list_dir(self, path: PathArg) -> list[str]:
        return sorted(os.listdir(path))

    def write_bytes(self, path: PathArg, data: bytes) -> None:
        with open(path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def make_temp(self, directory: PathArg, prefix: str = "", suffix: str = "") -> str:
        descriptor, name = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=directory)
        os.close(descriptor)
        return os.path.abspath(name)

    def replace(self, src: PathArg, dst: PathArg) -> None:
        os.replace(src, dst)
        _sync_directory(os.path.dirname(os.path.abspath(dst)))

    def remove(self, path: PathArg) -> None:
        os.remove(path)

    def make_dirs(self, path: PathArg) -> None:
        os.makedirs(path, exist_ok=True)


def is_link(path: PathArg) -> bool:
    """True for a symlink or a Windows reparse point such as a junction."""
    try:
        status = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(status.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(status, "st_file_attributes", 0) & reparse)


def lstat_or_none(path: PathArg) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
