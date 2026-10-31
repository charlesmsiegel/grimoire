"""Write-boundary checks: the library root, V1 isolation, links and portability.

`grimoire.library`'s checks are point-in-time, so the store re-runs them at
the start of every write session and before publishing: the root must still be
the directory it opened, still outside every V1 library, and no existing
component between the root and a target may be a link, junction or reparse
point. These checks are read-only metadata calls, outside the file seam.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from grimoire.library import LibraryPathError, check_write_destination

from .diskops import is_link, lstat_or_none
from .errors import CrossDevice, LinkRefused, UnstorableIdentifier
from .layout import Parts

Limits = Callable[[Path], "tuple[int | None, int | None]"]
"""``(longest absolute path, longest name)`` allowed under an existing directory."""

WINDOWS_MAX_PATH = 259


def default_limits(directory: Path) -> tuple[int | None, int | None]:
    if os.name == "nt":
        return WINDOWS_MAX_PATH, 255
    limits = []
    for name in ("PC_PATH_MAX", "PC_NAME_MAX"):
        try:
            value = os.pathconf(directory, name)
        except (OSError, ValueError, AttributeError):
            value = None
        limits.append(value if isinstance(value, int) and value > 0 else None)
    return limits[0], limits[1]


def path_length(path: Path | str) -> int:
    """Length in the units the OS limits: UTF-16 code units on Windows, bytes elsewhere."""
    if os.name == "nt":
        return len(str(path).encode("utf-16-le")) // 2
    return len(os.fsencode(path))


def _stat(path: Path) -> os.stat_result | None:
    try:
        return lstat_or_none(path)
    except OSError as error:
        raise LibraryPathError(f"cannot inspect {path}: {error}") from error


@dataclass(frozen=True)
class RootIdentity:
    """The opened root's identity; a swapped root (even for a link into V1) is noticed."""

    device: int
    key: object

    @classmethod
    def capture(cls, root: Path) -> RootIdentity:
        info = _stat(root)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise LibraryPathError(f"library root {root} is not a directory")
        # Some network filesystems report no inode; fall back to the resolved path.
        key = info.st_ino if info.st_ino else os.path.normcase(os.path.realpath(root))
        return cls(info.st_dev, key)


class Guard:
    def __init__(self, root: Path, identity: RootIdentity, limits: Limits | None = None) -> None:
        self.root = root
        self.identity = identity
        self.limits = limits or default_limits
        self._isolated: set[Path] = set()  # directories checked against V1 this session

    def check_session(self) -> None:
        """The root is unchanged and still not, inside or around a V1 library.

        Also starts a new check session: every directory is checked against V1
        again (once) after this call.
        """
        self._isolated.clear()
        self.check_root()
        check_write_destination(self.root)

    def check_root(self) -> None:
        """The root is still the directory that was opened: not a link, not replaced."""
        if is_link(self.root):
            raise LinkRefused(f"library root {self.root} became a link")
        try:
            current = RootIdentity.capture(self.root)
        except LibraryPathError as error:
            raise LibraryPathError(f"library root changed: {error}") from error
        if current != self.identity:
            raise LibraryPathError(f"library root {self.root} was replaced after it was opened")

    def check_read(self, parts: Parts) -> None:
        """The root is unchanged and no existing component of ``parts`` is a link; never follow one."""
        self.check_root()
        path = self.root
        for part in parts:
            path = path / part
            info = _stat(path)
            if info is None:
                return
            if is_link(path):
                raise LinkRefused(f"refusing to follow a link inside the library: {path}")

    def check_write(self, parts: Parts, *, staging_device: int | None) -> None:
        """Everything decidable before authorization about writing a file at ``parts``."""
        path, deepest = self.root, self.root
        for index, part in enumerate(parts):
            path = path / part
            info = _stat(path)
            if info is None:
                break
            if is_link(path):
                raise LinkRefused(f"refusing to write through a link inside the library: {path}")
            final = index == len(parts) - 1
            if stat.S_ISDIR(info.st_mode):
                if final:
                    raise UnstorableIdentifier(f"{path} is a directory, not a file")
                deepest = path
            elif not final:
                raise UnstorableIdentifier(f"{path} exists but is not a directory")
        if deepest not in self._isolated:  # sees bind mounts that give a V1 directory a name here
            check_write_destination(deepest)
            self._isolated.add(deepest)
        if staging_device is not None and _stat(deepest).st_dev != staging_device:  # type: ignore[union-attr]
            raise CrossDevice(f"{path} is on a different filesystem from the staging directory")
        target = self.root.joinpath(*parts)
        max_path, max_name = self.limits(deepest)
        if max_path is not None and path_length(target) > max_path:
            raise UnstorableIdentifier(f"{target} is longer than this system allows ({max_path})")
        if max_name is not None and any(path_length(part) > max_name for part in parts):
            raise UnstorableIdentifier(f"a name in {target} is longer than this system allows ({max_name})")
