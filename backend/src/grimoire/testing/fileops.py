"""Byte-level file operations seam and tree snapshots for fault-injection tests.

``FileOps`` is a *provisional* harness seam, not a storage contract: the native
file-store sprint owns the production interface and may reshape it. Writers under
test accept any object with these methods (structural typing), so production
code never needs to import :mod:`grimoire.testing`.

Extension policy: a consumer sprint may add an operation, declaring whether it
mutates, its crash/torn/error semantics and its shadow-model effect.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Protocol, Union

PathArg = Union[str, "os.PathLike[str]"]
Tree = dict[str, Union[bytes, None]]
"""Root-relative POSIX path -> file bytes, or ``None`` for a directory."""

MUTATING_KINDS = frozenset({"write_bytes", "make_temp", "replace", "remove", "make_dirs"})
READ_KINDS = frozenset({"read_bytes", "exists", "list_dir"})
OP_KINDS = MUTATING_KINDS | READ_KINDS


class SandboxViolation(ValueError):
    """A path escaped the test root, or a link was found inside it."""


class FileOps(Protocol):
    def read_bytes(self, path: PathArg) -> bytes: ...
    def exists(self, path: PathArg) -> bool: ...
    def list_dir(self, path: PathArg) -> list[str]: ...
    def write_bytes(self, path: PathArg, data: bytes) -> None: ...
    def make_temp(self, directory: PathArg, prefix: str = "", suffix: str = "") -> str: ...
    def replace(self, src: PathArg, dst: PathArg) -> None: ...
    def remove(self, path: PathArg) -> None: ...
    def make_dirs(self, path: PathArg) -> None: ...


class OsFileOps:
    """Real, unconfined operations."""

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

    def remove(self, path: PathArg) -> None:
        os.remove(path)

    def make_dirs(self, path: PathArg) -> None:
        os.makedirs(path, exist_ok=True)


def is_link(path: PathArg) -> bool:
    """True for a symlink or a Windows reparse point such as a junction."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(status.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(status, "st_file_attributes", 0) & reparse)


def snapshot(root: PathArg) -> Tree:
    """Every file and directory below ``root``; links are refused, never followed."""
    root = os.fspath(root)
    tree: Tree = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            full = os.path.join(directory, name)
            key = Path(os.path.relpath(full, root)).as_posix()
            if is_link(full):
                raise SandboxViolation(f"link inside test root: {key}")
            if name in dirnames:
                tree[key] = None
            else:
                with open(full, "rb") as handle:
                    tree[key] = handle.read()
    return dict(sorted(tree.items()))


def wipe(path: PathArg) -> None:
    """Delete a tree without following links or junctions out of it."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if is_link(path):
        try:
            os.unlink(path)
        except OSError:
            os.rmdir(path)  # Windows directory links and junctions
    elif stat.S_ISDIR(status.st_mode):
        for name in os.listdir(path):
            wipe(os.path.join(path, name))
        os.rmdir(path)
    else:
        os.unlink(path)
