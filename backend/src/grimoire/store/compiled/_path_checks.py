"""Low filesystem containment and ordinary-file ownership proofs."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from . import _contracts


def _linked(info: os.stat_result) -> bool:
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & _contracts._REPARSE_POINT))


def _directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        path.mkdir()
        info = path.lstat()
    return not _linked(info) and stat.S_ISDIR(info.st_mode)


def _leaf(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    return not _linked(info) and stat.S_ISREG(info.st_mode)


def _controls_safe(parent: Path) -> bool:
    if not all(_leaf(parent / name) for name in ('purge', '.clock')):
        return False
    intents = parent / 'delete-intents'
    try:
        info = intents.lstat()
    except FileNotFoundError:
        return True
    # Fixed controls and the plain directory suffice here. Accessed intent
    # leaves are checked individually in bounded pages (or by exact token).
    # Any nonempty/invalid page retains the global storage fence.
    return not _linked(info) and stat.S_ISDIR(info.st_mode)


def _cache_file_safe(path: Path) -> bool:
    return (_controls_safe(path.parent) and _leaf(path.with_suffix('.alive'))
            and all(_leaf(candidate) for candidate in [path, *(Path(str(path) + suffix)
                     for suffix in ('-journal', '-wal', '-shm', '.corrupt'))]))


def _cache_filename(path: Path) -> str:
    # Owned names are ASCII hex/schema spellings. Canonicalize on every OS:
    # POSIX normcase is identity even on insensitive APFS volumes. Sharing
    # exclusion for distinct case aliases on sensitive volumes is conservative.
    return path.name.lower()


def _cache_parent(root: Path) -> Path | None:
    parent = root / '.cache'
    if not _directory(parent) or not _directory(parent / 'compiled'):
        return None
    parent /= 'compiled'
    return parent if _controls_safe(parent) else None
