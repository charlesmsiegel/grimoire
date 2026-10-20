"""Location and control snapshots, durable markers and settled fences."""

from __future__ import annotations

import functools
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

from .. import atomic, statcache
from . import _contracts, _path_checks, _runtime, _sqlite_support


def _location(root: Path, device: str) -> Path | None:
    # Every path _location proves lives in .cache/compiled or above it, so
    # nothing it checks can change without moving the control snapshot: a
    # sidecar or a link arriving moves the directory, a marker or intent its
    # own entry. A proved location stands while the snapshot does.
    taken = _wall_ns()
    snapshot = _control_snapshot(root)
    memo = _runtime._state.locations.get((root, device))
    if snapshot is not None and memo is not None and memo[0] == snapshot:
        return memo[1]
    _runtime._state.locations.pop((root, device), None)
    path = _prove_location(root, device)
    if path is not None and snapshot is not None and _settled(snapshot, taken):
        _runtime._state.locations[(root, device)] = (snapshot, path)
    return path


def _prove_location(root: Path, device: str) -> Path | None:
    try:
        parent = root / ".cache"
        if not _path_checks._directory(parent) or not _path_checks._directory(parent / "compiled"):
            _runtime._warn_once("unsafe_path")
            return None
        path = parent / "compiled" / f"{device}-v{_contracts.SCHEMA}.sqlite"
        # SQLite can inspect a hot sidecar while opening an existing file.
        # Reject every recognized sidecar before even the ownership read.
        candidates = [path, *(Path(str(path) + suffix) for suffix in
                              ("-journal", "-wal", "-shm", ".corrupt"))]
        if not _path_checks._controls_safe(parent / "compiled") or not _path_checks._leaf(path.with_suffix(".alive")) or not all(_path_checks._leaf(candidate) for candidate in candidates):
            _runtime._warn_once("unsafe_path")
            return None
        return path
    except OSError:
        _runtime._warn_once("storage")
        return None


def _read_marker(root: Path) -> str | None:
    try:
        for parent in (root / '.cache', root / '.cache' / 'compiled'):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                return ''
            if _path_checks._linked(info) or not stat.S_ISDIR(info.st_mode):
                return None
        path = root / '.cache' / 'compiled' / 'purge'
        if not _path_checks._leaf(path):
            return None
        try:
            with path.open('r', encoding='ascii') as stream:
                value = stream.read(66).strip()
        except FileNotFoundError:
            return ''
        return value if _contracts._DEVICE_KEY.fullmatch(value) else None
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('marker')
        return None


def _read_generation(conn: _sqlite_support._sqlite_available.Connection) -> str | None:
    row = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_generation',)).fetchone()
    if row is not None and isinstance(row[0], str) and (row[0] == '' or _contracts._DEVICE_KEY.fullmatch(row[0])):
        return row[0]
    return None


def _publish_marker(root: Path, token: str | None = None) -> str | None:
    try:
        parent = _path_checks._cache_parent(root)
        if parent is None:
            raise OSError('unsafe cache controls')
        token = token or secrets.token_hex(32)
        atomic.write_text(parent / 'purge', token + '\n')
        return token
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('marker_publish')
        return None


def _read_intents(root: Path, *, token: str | None = None) -> list[tuple[str, str]] | None:
    parent = root / '.cache' / 'compiled' / 'delete-intents'
    try:
        info = parent.lstat()
    except FileNotFoundError:
        return []
    except _sqlite_support._CACHE_ERRORS:
        return None
    if _path_checks._linked(info) or not stat.S_ISDIR(info.st_mode):
        return None
    found: list[tuple[str, str]] = []
    try:
        if token is not None:
            return _read_intent(parent, token)
        with os.scandir(parent) as entries:
            for inspected, entry in enumerate(entries):
                if inspected >= 128:
                    # Vanished leaves still cost a probe. Without a surviving
                    # intent, keep the fence until a later pass proves empty.
                    return found or None
                if not re.fullmatch(r'[0-9a-f]{32}', entry.name):
                    return None
                # Retirement checks this exact file rather than membership of
                # the first page; completions outside that page must progress.
                item = _read_intent(parent, entry.name)
                if item is None:
                    return None
                found.extend(item)
        return found
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('intent_read')
        return None


def _read_intent(parent: Path, token: str) -> list[tuple[str, str]] | None:
    path = parent / token
    if not re.fullmatch(r'[0-9a-f]{32}', token) or not _path_checks._leaf(path):
        return None
    if not path.exists():
        return []
    with path.open('r', encoding='ascii') as stream:
        value = json.loads(stream.read(256))
    if (not isinstance(value, dict) or set(value) != {'token', 'owner'}
            or value['token'] != token or not isinstance(value['owner'], str)
            or not _contracts._DEVICE_KEY.fullmatch(value['owner'])):
        return None
    # Another device's intent fences every device until its owner finishes
    # it. An owner silent past RETENTION_S is the dormant device section 10
    # already retires (its database file is swept by then), so its intent
    # stops fencing too; this device's own intents never age out.
    mine = _runtime._state.owners.get(parent.parents[2])  # <root>/.cache/compiled/delete-intents
    if mine is not None and value['owner'] != mine:
        try:
            if time.time() - path.stat().st_mtime > _contracts.RETENTION_S:
                return []
        except FileNotFoundError:
            return []
    return [(token, value['owner'])]


def _control_snapshot(root: Path) -> tuple | None:
    """One lstat of each control path, or None when one cannot be read.

    What a fence or a synchronize pass reads -- the marker, the intents and
    which files sit beside the database -- cannot change without moving one of
    these: the marker is replaced by rename (a new inode and ctime), and an
    intent or a cache file arriving or leaving moves its directory's mtime.
    So an unchanged snapshot lets a pass that already succeeded stand, at the
    cost of four lstats instead of the reads, the listing and a probe
    connection per file. SQLite's own journal coming and going moves the
    directory too, which only costs one full pass after each write."""
    parts: list[tuple | None] = []
    for path in _control_paths(root):
        try:
            info = os.lstat(path)
            parts.append((info.st_dev, info.st_ino, info.st_mode, info.st_size,
                          info.st_mtime_ns, info.st_ctime_ns))
        except FileNotFoundError:
            parts.append(None)
        except (*_sqlite_support._CACHE_ERRORS, AttributeError):
            return None  # no snapshot: every caller takes its full path
    return tuple(parts)


@functools.lru_cache(maxsize=16)
def _control_paths(root: Path) -> tuple[str, ...]:
    cache = os.path.join(root, '.cache')
    compiled = os.path.join(cache, 'compiled')
    return (cache, compiled, os.path.join(compiled, 'purge'), os.path.join(compiled, 'delete-intents'))


def _controls(root: Path, *, strict: bool = False) -> tuple[str | None, bool]:
    """(the purge marker, whether no delete intent is pending) for `root`.

    Re-read only when the control snapshot moved since a settled read, or
    when `strict` asks: a commit always reads them again."""
    taken = _wall_ns()
    snapshot = _control_snapshot(root)
    memo = _runtime._state.controls.get(root)
    if not strict and snapshot is not None and memo is not None and memo[0] == snapshot:
        return memo[1], memo[2]
    marker = _read_marker(root)
    clear = _read_intents(root) == []
    if marker is not None and snapshot is not None and _settled(snapshot, taken):
        _runtime._state.controls[root] = (snapshot, marker, clear)
    else:
        _runtime._state.controls.pop(root, None)
    return marker, clear


def _settled(snapshot: tuple, taken_ns: int) -> bool:
    # statcache's racy-clean rule: a timestamp within one coarse tick of when
    # the snapshot was taken could hide a second change landing in that tick.
    newest = max((max(part[4], part[5]) for part in snapshot if part is not None), default=0)
    return newest < taken_ns - statcache.RACY_WINDOW_NS


_wall_ns = time.time_ns  # injectable for the settle rule's tests
