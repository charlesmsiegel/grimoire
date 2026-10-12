"""Disposable compiled artifacts, owned by this process and pinned library.

Spec 03's foundation: SQLite is an optional memo, never the live inventory.
Files establish membership; callers will select artifacts with content keys.
The stable application id and metadata distinguish our cache from a foreign
database. Unreadable, linked, busy or unowned storage costs a miss, never the
caller's file-reader answer. Tests/test_compiled.py pins those boundaries.

Keys select a particular derivation; row checks bind canonical JSON to that
key, so even damaged but valid JSON is a miss. Only SQLite's primary CORRUPT
or NOTADB codes permit replacement, under a machine-local nonblocking lock.
Locked, foreign and incompatible databases survive untouched. See spec
docs/superpowers/specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md
and test_compiled's corruption, foreign-file and hostile-row cases.

Ownership is probed immutably before read-write SQLite may recover journals.
Ordinary SQL and replacement share a nonblocking process exclusion; idle
handles recheck their inode under it before use. A failed transaction finishes
its rollback or closes under that exclusion, never across a rename window.

Only this module imports SQLite. Connections stay private and serialized;
parsing never belongs under their lock. Cache-owned directories and leaves
are checked with lstat, including Windows reparse attributes. The chosen
library root is the user's boundary: these checks reject established links,
not a hostile filesystem replacing a component between check and use.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import math
import os
import platform
import re
import secrets
import stat
import sys
import threading
import time
from _thread import LockType
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import NoReturn

from .. import compiled_build, template_paths
from . import atomic, file_stats, locks, logs, maintenance_reports, paths, proclock, statcache

_sqlite: ModuleType | None
try:
    import sqlite3 as _sqlite_available
except (ImportError, OSError):
    _sqlite = None
else:
    _sqlite = _sqlite_available

# ctypes is optional on packaged hosts; unavailable platform helpers cost
# persisted-source hits, while exact-byte hashing remains usable.
_ctypes: ModuleType | None
try:
    import ctypes as _ctypes_available
except (ImportError, OSError):
    _ctypes = None
else:
    _ctypes = _ctypes_available
_WINDOWS_CLOCK = sys.platform == "win32"
_kernel32 = None
if _WINDOWS_CLOCK and _ctypes is not None:
    try:
        _kernel32 = _ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.GetVolumePathNameW.argtypes = [_ctypes.c_wchar_p, _ctypes.c_wchar_p, _ctypes.c_uint]
        _kernel32.GetVolumePathNameW.restype = _ctypes.c_int
        _kernel32.GetDriveTypeW.argtypes = [_ctypes.c_wchar_p]
        _kernel32.GetDriveTypeW.restype = _ctypes.c_uint
    except (AttributeError, OSError):
        _kernel32 = None

# Linux/Android's generic statfs ABI begins with a native word f_type. Only
# these known ABIs are sampled; unproven hosts keep exact-byte artifact keys.
_LINUX_CLOCK = (sys.platform in ("linux", "android") and platform.machine().lower() in
                {"x86_64", "amd64", "aarch64", "arm64", "i386", "i686", "armv7l", "armv8l"})
_libc = None
if _LINUX_CLOCK and _ctypes is not None:
    try:
        _libc = _ctypes.CDLL(None, use_errno=True)
        _libc.statfs.argtypes = [_ctypes.c_char_p, _ctypes.c_void_p]
        _libc.statfs.restype = _ctypes.c_int
    except (AttributeError, OSError):
        _libc = None
# Local kernel filesystems share the sentinel's clock. Network, FUSE and
# overlay layers are excluded: utime(None) can still transmit CLIENT time
# on CIFS, and a layered mount does not establish its backing clock (S2 gate).
_LOCAL_FILESYSTEMS = frozenset({
    0xEF53, 0x9123683E, 0x58465342, 0x01021994, 0x858458F6,
    0xF2F52010, 0x4D44, 0x2011BAB0,  # ext, btrfs, xfs, tmpfs, ramfs, f2fs, FAT, exFAT
})

_CACHE_ERRORS = (OSError, ValueError, TypeError, RuntimeError, MemoryError) + (
    (_sqlite.Error,) if _sqlite is not None else ())

SCHEMA = 3
APPLICATION_ID = 0x4752494D
MAX_PAYLOAD_BYTES = 1 << 20
MAX_KEY_BYTES = 64 << 10
MAX_JSON_DEPTH = 64
MAX_JSON_ITEMS = 100_000
MAX_STAGED_ROWS = 128
MAX_STAGED_BYTES = 4 << 20
MAX_HISTORY_ROWS = 128
MAX_HISTORY_BYTES = 4 << 20
WRITE_LOCK_SECONDS = 0.010
IDLE_SECONDS = 30.0
SIZE_CAP_BYTES = 64 << 20
LOW_WATER_BYTES = 48 << 20
MAINTENANCE_INTERVAL_S = 3600
RETENTION_S = 90 * 86400
TOUCH_INTERVAL_S = 86400
VACUUM_PAGES = 256
MAX_EVICTION_CHUNKS = 64
_clock = time.monotonic
_timer = threading.Timer
_DEVICE_KEY = re.compile(r"[0-9a-f]{64}")
_CACHE_FILE = re.compile(r"([0-9a-f]{32})-v([0-9]+)\.sqlite")
_REPARSE_POINT = 0x400

ABSENT = "absent"
PERSIST_WINDOW_NS = 5_000_000_000
SOURCE_CHUNK = 128
_SOURCE_SQL = (
    "SELECT path,"
    "CASE WHEN typeof(mtime_ns)='integer' THEN mtime_ns END,"
    "CASE WHEN typeof(ctime_ns)='integer' THEN ctime_ns END,"
    "CASE WHEN typeof(size)='integer' THEN size END,"
    "CASE WHEN typeof(ino)='text' AND length(ino)<=40 THEN ino END,"
    "CASE WHEN typeof(content_hash)='text' AND length(content_hash)=64 THEN content_hash END,"
    "CASE WHEN typeof(\"check\")='text' AND length(\"check\")=64 THEN \"check\" END "
    " ,CASE WHEN typeof(last_used)='integer' THEN last_used END FROM sources WHERE path IN ("
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?'
    ")"
)


_ARTIFACT_MANY_SQL = (
    "SELECT key_digest,CASE WHEN typeof(payload)='text' "
    "AND length(CAST(payload AS BLOB))<=? THEN payload END,"
    "CASE WHEN typeof(\"check\")='text' AND length(\"check\")=64 THEN \"check\" END "
    " ,CASE WHEN typeof(last_used)='integer' THEN last_used END FROM artifacts WHERE kind=? AND key_digest IN ("
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?'
    ")"
)


_MATERIALIZED_SQL = (
    "SELECT path,CASE WHEN typeof(kind)='text' AND length(kind)<=? THEN kind END,"
    "CASE WHEN typeof(instance)='text' AND length(instance)<=? THEN instance END,"
    "CASE WHEN typeof(built_from)='text' AND length(built_from) IN (0,64) THEN built_from END,"
    "CASE WHEN typeof(last_used)='integer' AND last_used>=0 THEN last_used END,"
    "CASE WHEN typeof(\"check\")='text' AND length(\"check\")=64 THEN \"check\" END,"
    "CASE WHEN typeof(artifact_kind)='text' AND length(artifact_kind)<=? THEN artifact_kind END,"
    "CASE WHEN typeof(artifact_key)='text' AND length(artifact_key)=64 THEN artifact_key END "
    "FROM materialized WHERE path IN ("
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'
    '?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?'
    ")"
)


@dataclass(frozen=True)
class FilesystemClock:
    now_ns: int
    device: int


@dataclass(frozen=True)
class _SourceInfo:
    path: Path
    stamp: statcache.Stamp
    device: int
    relative: str | None
    plain: bool
    regular: bool = True
    metadata_trusted: bool = True


@dataclass(frozen=True)
class Kind:
    name: str
    version: int
    path_inputs: tuple[str, ...]
    params: tuple[str, ...]
    allow_persisted_sources: bool
    absent_ok_roles: tuple[str, ...] = ()
    member_filter: Callable[[str], bool] | None = None


KINDS: dict[str, Kind] = {}


@dataclass(frozen=True)
class Input:
    role: str
    path: Path


@dataclass(frozen=True)
class Collection:
    role: str
    path: Path
    recursive: bool = False


@dataclass(frozen=True)
class Materialized:
    path: Path
    kind: str
    instance: str
    built_from: str
    last_used: int


@dataclass(frozen=True)
class Uncached:
    value: object


@dataclass(frozen=True)
class WrittenInput:
    role: str
    path: Path
    data: bytes


@dataclass(frozen=True)
class ComputeInput:
    role: str
    content_hash: str
    data: bytes | None
    path_parts: tuple[str, ...]


@dataclass(frozen=True)
class ComputeCollection:
    role: str
    content_hash: str
    members: tuple[ComputeInput, ...]


@dataclass(frozen=True)
class DerivationInputs:
    files: tuple[ComputeInput, ...]
    collections: tuple[ComputeCollection, ...]


@dataclass(frozen=True)
class ValidatedInput:
    role: str
    path: Path
    content_hash: str
    stamp: statcache.Stamp | None
    data: bytes | None
    materialized: tuple[Materialized, ...] = ()


@dataclass(frozen=True)
class CollectionDigest:
    role: str
    content_hash: str
    members: tuple[ValidatedInput, ...]


class InputUnavailable(OSError):  # noqa: N818 -- Spec03 public exception contract
    """A listed input cannot supply the bytes required by this derivation."""


@dataclass(frozen=True)
class Key:
    kind: str
    digest: str


@dataclass(frozen=True)
class Lookup:
    hit: bool
    value: object = None


@dataclass(frozen=True)
class Counts:
    hits: int
    misses: int


@dataclass(frozen=True)
class BatchLookup:
    values: dict[Key | None, Lookup]
    counts: dict[str, Counts]


def _lookup_result(values: dict[Key | None, Lookup]) -> BatchLookup:
    counts: dict[str, list[int]] = {}
    for key, lookup in values.items():
        if key is not None:  # make_key's refusal: answered as a miss, counted nowhere
            counts.setdefault(key.kind, [0, 0])[0 if lookup.hit else 1] += 1
    return BatchLookup(values, {kind: Counts(*count) for kind, count in counts.items()})


@dataclass
class _Session:
    root: Path
    pid: int
    path: Path
    conn: _sqlite_available.Connection
    lock: LockType
    epoch: int
    last_used: float
    identity: tuple[int, int]
    timer: threading.Timer | None = None
    maintained: float = 0
    retained: float = 0
    trimming: bool = False
    # The control snapshot and marker of the last `_synchronize` that finished
    # every file; see `_control_snapshot`.
    synced: tuple | None = None
    synced_marker: str | None = None
    leased: float = -math.inf  # time.time() of the last lease check


@dataclass
class DeleteIntent:
    token: str
    owner: str
    descriptor: int | None
    root: Path
    finished: bool = False


@dataclass
class _State:
    pid: int = field(default_factory=os.getpid)
    lock: LockType = field(default_factory=threading.Lock)
    session: _Session | None = None
    epoch: int = 0
    devices: dict[Path, str] = field(default_factory=dict)
    owners: dict[Path, str] = field(default_factory=dict)
    locations: dict[tuple[Path, str], tuple[tuple, Path]] = field(default_factory=dict)
    intents: dict[str, DeleteIntent] = field(default_factory=dict)
    finished_intents: deque[DeleteIntent] = field(default_factory=deque)
    intent_lock: LockType = field(default_factory=threading.Lock)
    device_unavailable: bool = False
    warned: set[str] = field(default_factory=set)
    build_checked: bool = False
    build: str | None = None
    transitions: int = 0


_state = _State()
# A pid fallback must not decref an inherited SQLite handle into its C
# finalizer. Real fork hooks close it in the parent; unsupported hosts keep
# the inherited state unreachable by cache operations until process exit.
_inherited: list[_State] = []


def _process_state() -> None:
    global _state  # noqa: PLW0603 -- a new process needs a fresh lock-bearing owner
    pid = os.getpid()
    if _state.pid != pid:
        _close_inherited_intents()
        _inherited.append(_state)
        _state = _inherit_build(_State(pid=pid), _state)


def _inherit_build(child: _State, parent: _State) -> _State:
    # A forked child runs its parent's code, so it keeps the parent's BUILD
    # rather than fingerprinting sources that may have changed on disk since.
    child.build_checked, child.build = parent.build_checked, parent.build
    return child


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def prime_build() -> None:
    """Fingerprint BUILD now, from the code this process has just imported.

    The app calls this at startup. Left to the first cache use, a process
    that outlives an edit of its own sources (a `git pull` under a running
    server, a template edited, a dependency upgraded) would fingerprint the
    NEW files and store what the OLD code derived under that BUILD. Never
    raises; a disabled or SQLite-less cache computes nothing."""
    if _sqlite is not None and os.environ.get("GRIMOIRE_COMPILED_CACHE") != "0":
        _build()


def _build() -> str | None:
    _process_state()
    if not _state.build_checked:
        _state.build_checked = True
        try:
            stamp = os.environ.get("GRIMOIRE_COMPILED_BUILD")
            _state.build = compiled_build.fingerprint(
                Path(compiled_build.__file__).parent, template_paths.templates_dir(),
                stamp_path=Path(stamp) if stamp else None, require_stamp=proclock._on_android())
        except _CACHE_ERRORS:
            _state.build = None
        if _state.build is None:
            _warn_once("build")
    return _state.build


def _available() -> bool:
    _process_state()
    if _on_loop() or locks.campaign_locks_held() or os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0":
        return False
    if _sqlite is None:
        _warn_once("sqlite_unavailable")
        return False
    return _build() is not None


def _scalar(value: object, limit: int) -> bool:
    if value is None or type(value) is bool:
        return True
    if isinstance(value, str):
        return type(value) is str and len(value) <= limit
    if isinstance(value, int):
        return type(value) is int and value.bit_length() <= limit * 3
    return type(value) is float and isinstance(value, float) and math.isfinite(value)


def _json_value(value: object, limit: int) -> bool:
    # Iterator frames avoid copying a million-item list onto a work stack.
    # Depth also rejects cycles. A single encoder string chunk is bounded by
    # 12 * limit (non-BMP escapes); the complete output has the tighter limit.
    stack: list[tuple[Iterator, int]] = [(iter((value,)), 0)]
    count = 0
    while stack:
        iterator, depth = stack[-1]
        item = next(iterator, stack)
        if item is stack:
            stack.pop()
            continue
        count += 1
        if count > MAX_JSON_ITEMS:
            return False
        if type(item) not in (dict, list):
            if not _scalar(item, limit):
                return False
            continue
        if depth >= MAX_JSON_DEPTH:
            return False
        if isinstance(item, dict):
            if (len(item) > MAX_JSON_ITEMS
                    or not all(type(key) is str and len(key) <= limit for key in item)):
                return False
            stack.append((iter(item.values()), depth + 1))
        elif isinstance(item, list):
            stack.append((iter(item), depth + 1))
    return True


def _canonical(value: object, limit: int) -> str | None:
    try:
        if not _json_value(value, limit):
            return None
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False)
        chunks, size = [], 0
        for chunk in encoder.iterencode(value):
            size += len(chunk)  # ensure_ascii makes characters equal bytes
            if size > limit:
                return None
            chunks.append(chunk)
        return "".join(chunks)
    except _CACHE_ERRORS:
        return None


# Strings, then everything but brackets, are removed in the regex engine, so
# Python walks only the nesting. Visiting each string token in Python made a
# payload of many short strings cost more to check than to decode (S6 cost
# probe). json.loads still validates the complete syntax.
_JSON_STRING = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"')
_JSON_NOT_STRUCTURE = re.compile(r'[^][{}"]+')


def _json_depth(text: str) -> bool:
    structure = _JSON_NOT_STRUCTURE.sub('', _JSON_STRING.sub('', text))
    if '"' in structure:
        return False  # a string that never closes
    depth = 0
    for token in structure:
        if token in '[{':
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return False
        else:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _key_inputs(kind: Kind, inputs: Sequence[tuple[str, str]]) -> list | None:
    ordered = []
    for role, digest in inputs:
        if (not isinstance(role, str) or not isinstance(digest, str)
                or len(role) > MAX_KEY_BYTES
                or not (_DEVICE_KEY.fullmatch(digest)
                        or (digest == ABSENT and role in kind.absent_ok_roles))):
            return None
        ordered.append([role, digest])
    return ordered


def make_key(kind: Kind, inputs: Sequence[tuple[str, str]], *,
             params: Mapping[str, object], collections: Sequence[tuple[str, str]] = ()) -> Key | None:
    """Select registered derivations, including their input categories."""
    try:
        if (not isinstance(kind, Kind) or KINDS.get(kind.name) != kind
                or type(kind.version) is not int or kind.version < 1
                or set(params) != set(kind.params)
                or not set(kind.path_inputs) <= set(kind.params)
                or len(inputs) + len(collections) > MAX_KEY_BYTES // 64):
            return None
        files = _key_inputs(kind, inputs)
        groups = _key_inputs(kind, collections)
        if files is None or groups is None or not _available():
            return None
        # A file can literally contain a collection's canonical manifest.
        # Separate categories and boundaries rather than hashing a prefix that
        # could itself be supplied as file bytes (Spec03 S4 Codex review).
        payload = _canonical([kind.name, kind.version, _build(), dict(params),
                              ['files', files], ['collections', groups]], MAX_KEY_BYTES)
        return Key(kind.name, hashlib.sha256(payload.encode("ascii")).hexdigest()) if payload else None
    except _CACHE_ERRORS:
        return None


def _check(key: Key, payload: str) -> str:
    # Payload is already bounded. Quoting its canonical text at most doubles
    # its escapes; checksums never serialize an arbitrary caller object.
    body = json.dumps([key.kind, key.digest, payload], separators=(",", ":"),
                      sort_keys=True, ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(body.encode("ascii")).hexdigest()


def _decode(key: Key, row: tuple) -> Lookup:
    try:
        payload, check = row
        if (isinstance(payload, str) and isinstance(check, str)
                and _check(key, payload) == check and _json_depth(payload)):
            value = json.loads(payload)
            if _json_value(value, MAX_PAYLOAD_BYTES):
                return Lookup(True, value)
    except _CACHE_ERRORS:
        pass
    # A damaged row remains a miss, but is degradation rather than an
    # ordinary cold lookup. One fixed warning cannot expose its contents.
    _warn_once("artifact")
    return Lookup(False)


def _source_check(path: str, stamp: statcache.Stamp, digest: str) -> str | None:
    if (type(path) is not str or len(path) > MAX_KEY_BYTES or not _DEVICE_KEY.fullmatch(digest)
            or any(type(n) is not int or not -(2**63) <= n < 2**63 for n in stamp[1:4])
            or stamp[3] < 0 or type(stamp[4]) is not int or stamp[4] < 0
            or len(str(stamp[4])) > 40):
        return None
    body = _canonical([path, list(stamp[1:]), digest], MAX_KEY_BYTES)
    return hashlib.sha256(body.encode("ascii")).hexdigest() if body else None


def _decode_source(path: str, row: tuple) -> tuple[statcache.Stamp, str] | None:
    try:
        stored, mtime, ctime, size, inode, digest, check = row[:7]
        if (stored == path and isinstance(inode, str) and inode.isascii() and inode.isdecimal()
                and len(inode) <= 40 and str(int(inode)) == inode
                and isinstance(digest, str) and isinstance(check, str)):
            stamp = (path, mtime, ctime, size, int(inode))
            expected = _source_check(path, stamp, digest)
            if expected is not None and expected == check:
                return stamp, digest
    except _CACHE_ERRORS:
        pass
    _warn_once("source")
    return None


def space_digest(space: str) -> str:
    """One spelling of vector space identity, including its exact NULs."""
    return hashlib.sha256(space.encode('utf-8')).hexdigest()[:16]


def _relative(root: Path, path: Path) -> str | None:
    if '..' in path.parts:
        return None
    try:
        return Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root))).as_posix()
    except (OSError, ValueError):
        return None


def _instance(instance: object) -> str | None:
    return '' if instance is None else _canonical(instance, MAX_KEY_BYTES)


def _canonical_instance(text: str) -> bool:
    try:
        return text == '' or _canonical(json.loads(text), MAX_KEY_BYTES) == text
    except _CACHE_ERRORS:
        return False


def _materialized_check(path: str, kind: str, instance: str, built_from: str,
                        artifact_kind: str | None, artifact_key: str | None) -> str | None:
    body = _canonical([path, kind, instance, built_from, artifact_kind, artifact_key], MAX_KEY_BYTES)
    return hashlib.sha256(body.encode('ascii')).hexdigest() if body else None


def _materialized_row(path: str, kind: str, instance: str, built_from: str,
                      key: Key | None) -> tuple | None:
    if (not kind or not _canonical_instance(instance)
            or (built_from != '' and not _DEVICE_KEY.fullmatch(built_from))):
        return None
    artifact_kind, artifact_key = (key.kind, key.digest) if key else (None, None)
    check = _materialized_check(path, kind, instance, built_from, artifact_kind, artifact_key)
    return (path, kind, instance, built_from, int(time.time()), check, artifact_kind, artifact_key) if check else None


def _decode_materialized(root: Path, row: tuple) -> Materialized | None:
    try:
        path, kind, instance, built_from, used, check, artifact_kind, artifact_key = row
        if (not all(isinstance(item, str) for item in (path, kind, instance, built_from, check))
                or not kind or type(used) is not int or used < 0 or not _canonical_instance(instance)
                or (built_from != '' and not _DEVICE_KEY.fullmatch(built_from))
                or (artifact_kind is None) != (artifact_key is None)):
            raise ValueError('invalid materialized field')
        if artifact_key is not None and not _DEVICE_KEY.fullmatch(artifact_key):
            raise ValueError('invalid materialized field')
        expected = _materialized_check(path, kind, instance, built_from, artifact_kind, artifact_key)
        if expected is not None and check == expected:
            return Materialized(root / path, kind, instance, built_from, used)
    except _CACHE_ERRORS:
        pass
    _warn_once('materialized')
    return None


def _collection_hash(root: Path, members: Sequence[ValidatedInput]) -> str:
    manifest = [[_relative(root, item.path), item.content_hash] for item in members]
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(separators=(',', ':'), ensure_ascii=True).iterencode(manifest):
        digest.update(chunk.encode('ascii'))
    return digest.hexdigest()


def _linux_filesystem_type(path: Path) -> int | None:
    if not _LINUX_CLOCK or _libc is None or _ctypes is None:
        return None
    try:
        final = path.resolve()
        try:
            final.stat()
        except FileNotFoundError:
            # A new sentinel will belong to its guarded containing directory.
            # Existing leaves are classified themselves, including bind mounts.
            final = final.parent
        # Only f_type is interpreted; oversized native-word storage keeps all
        # supported statfs layouts within the allocated, correctly aligned area.
        output = (_ctypes.c_long * 128)()
        if _libc.statfs(os.fsencode(final), output) == 0:
            return int(output[0]) & 0xFFFFFFFF
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _clock_supported(path: Path) -> bool:
    if not _WINDOWS_CLOCK:
        return _linux_filesystem_type(path) in _LOCAL_FILESYSTEMS
    if _kernel32 is None or _ctypes is None:
        return False
    try:
        # Resolve the user-selected boundary only after owned components have
        # been checked. Matching st_dev alone does not prove a remote clock:
        # Windows utime(None) supplies CLIENT time (S2 plan provenance ruling).
        final = str(path.resolve())
        volume = _ctypes.create_unicode_buffer(32768)
        return bool(_kernel32.GetVolumePathNameW(final, volume, len(volume))
                    and _kernel32.GetDriveTypeW(volume.value) in (2, 3, 6)
                    and file_stats.windows_change_supported(path))
    except (OSError, ValueError, AttributeError):
        return False


def _clock_path(root: Path) -> Path | None:
    device = _device(root)
    location = _location(root, device) if device is not None else None
    return location.parent / f"{device}-clock" if location is not None else None


def filesystem_clock(root: Path) -> FilesystemClock | None:
    """Sample proven filesystem time; never fall back to process wall time."""
    if not _available():
        return None
    try:
        path = _clock_path(root)
        if path is not None and _leaf(path) and _clock_supported(path):
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            os.utime(path, None)
            info = path.stat()
            if info.st_dev > 0:
                return FilesystemClock(info.st_mtime_ns, info.st_dev)
    except _CACHE_ERRORS:
        pass
    _warn_once("clock")
    return None


def _source_info(root: Path, path: Path) -> _SourceInfo:
    # The timestamp and device must come from ONE live stat, before byte IO.
    info = path.stat()
    changed = file_stats.change_ns(path, info)
    stamp = (str(path), info.st_mtime_ns, changed if changed is not None else info.st_ctime_ns, info.st_size, info.st_ino)
    relative = None
    plain = False
    # Parent traversal can cross a symlink before '..' even when abspath
    # later appears contained. Keep such readable inputs live-only (S2 review).
    if ".." in path.parts:
        return _SourceInfo(path, stamp, info.st_dev, None, False, stat.S_ISREG(info.st_mode))
    try:
        boundary = Path(os.path.abspath(root))
        relative_path = Path(os.path.abspath(path)).relative_to(boundary)
        relative = relative_path.as_posix()
        current = boundary
        plain = True
        for component in relative_path.parts:
            current = current / component
            if _linked(current.lstat()):
                plain = False
                break
    except (OSError, ValueError):
        plain = False
    return _SourceInfo(path, stamp, info.st_dev, relative, plain, stat.S_ISREG(info.st_mode), changed is not None)


def _eligible(info: _SourceInfo, clock: FilesystemClock | None) -> bool:
    return bool(clock is not None and clock.device > 0 and info.device == clock.device
                and info.relative is not None and info.plain and info.regular and info.metadata_trusted
                and clock.now_ns - max(info.stamp[1:3]) > PERSIST_WINDOW_NS)


def _history_size(row: tuple) -> int:
    return sum(len(item.encode('utf-8')) if isinstance(item, str) else 8 for item in row)


def _history_transfer(cursor: Iterator[tuple]) -> list[tuple] | None:
    # Path count does not bound selectors per path. Reject the complete
    # optional answer before retaining an unbounded family; never truncate
    # independent selectors into an apparently complete rename history.
    rows: list[tuple] = []
    size = 0
    for row in cursor:
        size += _history_size(row)
        if len(rows) >= MAX_HISTORY_ROWS or size > MAX_HISTORY_BYTES:
            _warn_once('materialized_budget')
            return None
        rows.append(row)
    return rows


class Batch:
    """Request-owned staging; its lifecycle identity precedes caller compute."""

    def __init__(self, root: Path, *, identity: tuple[int, int, bool] | None = None) -> None:
        _process_state()
        self.root = root
        self.pid, self.epoch, fenced = identity or (os.getpid(), _state.epoch, bool(_state.transitions))
        self.disabled = fenced or self.pid != os.getpid() or self.epoch != _state.epoch
        self._rows: dict[Key, tuple[str, str, str, str, int]] = {}
        self._bytes = 0
        self._sources: dict[str, tuple] = {}
        self._source_sizes: dict[str, int] = {}
        self._materialized: dict[tuple[str, str, str], tuple] = {}
        self._materialized_sizes: dict[tuple[str, str, str], int] = {}
        self._artifact_touches: dict[Key, int] = {}
        self._artifact_used: dict[Key, int] = {}
        self._source_known: dict[str, tuple] = {}
        self._history_known: dict[tuple, tuple] = {}
        self._source_clock_checked = False
        self._source_clock: FilesystemClock | None = None
        self._storage_started = False
        self._attempt_started = False
        self._attempt_pending = False
        self._maintenance_checked = False
        self._marker: str | None = None
        self._generation: str | None = None
        self._fence_snapshot: tuple | None = None

    def _usable(self, key: Key | None = None, *, strict: bool = False) -> bool:
        self._maintenance()
        self._remember_boundary()
        lifecycle = (not self.disabled and not _state.transitions and self.pid == os.getpid()
                     and self.epoch == _state.epoch and _available()
                     and (key is None or (key.kind in KINDS and bool(_DEVICE_KEY.fullmatch(key.digest)))))
        return lifecycle and self._fence(strict=strict)

    def _maintenance(self) -> None:
        if not self._maintenance_checked:
            self._maintenance_checked = True
            if not self.disabled and self.pid == os.getpid() and self.epoch == _state.epoch:
                _pending_maintenance(self.root)

    def begin_compute(self) -> None:
        """Fence caller-supplied snapshot work before its hashing/computation.

        Source validation and store_written do this internally. Callers that
        compute optional history from already supplied bytes need the same
        durable boundary before doing their own work; no SQLite is opened here.
        """
        self._remember_boundary()

    def _remember_boundary(self) -> None:
        # SQL opt-out is temporary (for example while a campaign lock is
        # held), but caller compute has already started. Capture the durable
        # boundary before availability can short-circuit the SQLite fence.
        if not self._attempt_started:
            self._attempt_started = True
            self._marker = _read_marker(self.root)
            self._attempt_pending = _read_intents(self.root) != []

    def _fence(self, *, strict: bool = False) -> bool:
        """Whether no purge or delete intent has arrived since the boundary.

        Reads may reuse this batch's last passing answer while the control
        snapshot stands (`_control_snapshot`); a commit (`strict`) always
        reads the marker and the intents again."""
        if self._attempt_pending:
            return False
        if not strict and self._fence_snapshot is not None:
            if _control_snapshot(self.root) == self._fence_snapshot:
                return not self.disabled
            self._fence_snapshot = None
        taken = _wall_ns()
        snapshot = _control_snapshot(self.root)
        passed = self._fence_read()
        if passed and snapshot is not None and _settled(snapshot, taken):
            self._fence_snapshot = snapshot
        return passed

    def _fence_read(self) -> bool:
        marker = _read_marker(self.root)
        # A busy first access still establishes the temporal boundary. A
        # retry may recover the same generation, but cannot adopt a purge that
        # happened while caller compute was already underway (S5 review).
        if marker is None or marker != self._marker:
            return False
        if not self._storage_started:
            self._storage_started = True
            if not _state.lock.acquire(blocking=False):
                self._storage_started = False
                return False
            try:
                with _database(self.root, expected_generation=self._generation) as session:
                    if session is None:
                        self._storage_started = False
                        return False
                    self._generation = _read_generation(session.conn)
            except _CACHE_ERRORS:
                self._storage_started = False
                _warn_once('generation')
            finally:
                _state.lock.release()
        return (not self.disabled and self._marker is not None and self._generation is not None
                and _read_marker(self.root) == self._marker and _read_intents(self.root) == [])

    def _session_current(self, session: _Session, *, strict: bool = False) -> bool:
        return self._usable(strict=strict) and _read_generation(session.conn) == self._generation

    def _persist_sources(self, kind: Kind) -> bool:
        return KINDS.get(kind.name) == kind and kind.allow_persisted_sources and self._usable()

    def _sample_clock(self, kind: Kind) -> None:
        if not self._source_clock_checked and self._persist_sources(kind):
            self._source_clock_checked = True
            # Capture before ANY source stat. A slow read cannot age its own
            # input into trust, and a failed sample has no wall-time fallback.
            try:
                self._source_clock = filesystem_clock(self.root)
            except _CACHE_ERRORS:
                _warn_once("clock")

    def _abort_input(self, error: OSError) -> NoReturn:
        self.disabled = True
        self._rows.clear()
        self._sources.clear()
        self._source_sizes.clear()
        self._materialized.clear()
        self._materialized_sizes.clear()
        self._artifact_touches.clear()
        self._bytes = 0
        raise InputUnavailable("compiled input unavailable") from error

    def _input_infos(self, kind: Kind, inputs: Sequence[Input]) -> dict[Path, _SourceInfo | None]:
        found: dict[Path, _SourceInfo | None] = {}
        for item in inputs:
            if item.path not in found:
                try:
                    found[item.path] = _source_info(self.root, item.path)
                except FileNotFoundError:
                    found[item.path] = None
                except OSError as error:
                    self._abort_input(error)
            if found[item.path] is None and not (KINDS.get(kind.name) == kind
                                                and item.role in kind.absent_ok_roles):
                self._abort_input(FileNotFoundError())
        return found

    def _trusted_sources(self, kind: Kind, infos: Sequence[_SourceInfo]) -> dict[Path, str]:
        if not self._persist_sources(kind):
            return {}
        eligible = [info for info in infos if _eligible(info, self._source_clock)]
        if not eligible:
            return {}
        rows = self._source_rows([info.relative for info in eligible if info.relative is not None])
        trusted = {}
        for info in eligible:
            if info.relative is None:
                continue
            row = rows.get(info.relative)
            decoded = _decode_source(info.relative, row[:7]) if row is not None else None
            if row is not None and decoded is not None and decoded[0][1:] == info.stamp[1:]:
                trusted[info.path] = decoded[1]
                self._remember_metadata(self._source_known, info.relative, row)
                self._stage_source(info.relative, info.stamp, decoded[1])
        return trusted if self._usable() else {}

    def validate(self, kind: Kind, inputs: Sequence[Input], *,
                 with_bytes: bool = False) -> tuple[ValidatedInput, ...]:
        """Validate listed inputs; cold reads retain exact bytes for compute."""
        # Capture before source bytes even for False-policy derivations.
        # Persistence policy changes trust, never the delete-time boundary.
        if not inputs:
            return ()
        self._usable()
        self._sample_clock(kind)
        infos = self._input_infos(kind, inputs)
        return self._validate_infos(kind, inputs, infos, with_bytes=with_bytes)

    def _validate_infos(self, kind: Kind, inputs: Sequence[Input],
                        infos: Mapping[Path, _SourceInfo | None], *,
                        with_bytes: bool) -> tuple[ValidatedInput, ...]:
        # File and collection forms share one stat-to-bytes trust rule. A
        # collection already captured its member stats while enumerating;
        # repeating them here could associate bytes with a different stamp.
        trusted = {} if with_bytes else self._trusted_sources(
            kind, [info for info in infos.values() if info is not None])
        values: dict[Path, tuple[str, statcache.Stamp | None, bytes | None]] = {}
        for path, info in infos.items():
            if info is None:
                values[path] = (ABSENT, None, None)
                continue
            data = None
            digest = trusted.get(path)
            if digest is None:
                try:
                    data = path.read_bytes()
                except OSError as error:
                    self._abort_input(error)
                digest = hashlib.sha256(data).hexdigest()
                if (info.relative is not None and self._persist_sources(kind)
                        and _eligible(info, self._source_clock)):
                    self._stage_source(info.relative, info.stamp, digest)
            values[path] = (digest, info.stamp, data)
        history = self.materialized(list(infos))
        return tuple(ValidatedInput(item.role, item.path, *values[item.path], history.get(item.path, ()))
                     for item in inputs)

    def _collection_infos(self, kind: Kind, collection: Collection) -> dict[Path, _SourceInfo]:
        infos: dict[Path, _SourceInfo] = {}
        try:
            pending = [collection.path]
            while pending:
                parent = pending.pop()
                with os.scandir(parent) as listing:
                    entries = list(listing)
                for entry in entries:
                    path = Path(entry.path)
                    # Reparse directories/junctions must not become recursive
                    # traversal edges. Linked regular files still supply live
                    # bytes, and _source_info keeps them out of source trust.
                    if entry.is_dir(follow_symlinks=False):
                        if collection.recursive and not _linked(entry.stat(follow_symlinks=False)):
                            pending.append(path)
                        continue
                    relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(self.root))).as_posix()
                    if kind.member_filter is not None and not kind.member_filter(relative):
                        continue
                    info = _source_info(self.root, path)
                    if info.relative is None:
                        raise OSError('collection member has no contained path')
                    if info.regular:
                        infos[path] = info
        except (OSError, ValueError) as error:
            self._abort_input(OSError(str(error)))
        return infos

    def collection(self, kind: Kind, collection: Collection, *,
                   with_bytes: bool = False) -> CollectionDigest:
        """List membership live; hash sorted relative paths and exact bytes."""
        self._usable()
        self._sample_clock(kind)
        try:
            directory = _source_info(self.root, collection.path)
            if directory.relative is None or (collection.recursive and not directory.plain):
                raise OSError('collection has no plain contained directory')
        except (OSError, ValueError) as error:
            self._abort_input(OSError(str(error)))
        infos = self._collection_infos(kind, collection)
        ordered = sorted(infos, key=lambda path: infos[path].relative or "")  # all members have an info
        members = self._validate_infos(kind, [Input(collection.role, path) for path in ordered],
                                       infos, with_bytes=with_bytes)
        # Its digest fits a composite key even when the manifest does not.
        return CollectionDigest(collection.role, _collection_hash(self.root, members), members)

    def validate_one(self, kind: Kind, input: Input, *,  # noqa: A002 -- Spec03 keyword contract
                     with_bytes: bool = False) -> ValidatedInput:
        return self.validate(kind, [input], with_bytes=with_bytes)[0]

    def known_hashes(self, kind: Kind, paths: Sequence[Path]) -> dict[Path, str | None]:
        """Stat every supplied path; never read input bytes or record a source."""
        if not paths:
            return {}
        self._sample_clock(kind)
        infos = []
        for path in paths:
            with contextlib.suppress(OSError):
                infos.append(_source_info(self.root, path))
        trusted = self._trusted_sources(kind, infos)
        return {path: trusted.get(path) for path in paths}

    def lookup(self, key: Key | None) -> Lookup:
        if key is None or not self._usable(key) or not _state.lock.acquire(blocking=False):
            return Lookup(False)
        session = None
        try:
            if not self._usable(key):
                return Lookup(False)
            with _database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return Lookup(False)
                # Preserve the existence of a rejected row without loading its
                # payload into Python, so damage is reported like decode failures.
                row = session.conn.execute(
                    "SELECT CASE WHEN typeof(payload)='text' "
                    "AND length(CAST(payload AS BLOB))<=? THEN payload END,"
                    '"check",CASE WHEN typeof(last_used)=\'integer\' THEN last_used END '
                    'FROM artifacts WHERE kind=? AND key_digest=?',
                    (MAX_PAYLOAD_BYTES, key.kind, key.digest)).fetchone()
                session.last_used = _clock()
        except _CACHE_ERRORS as error:
            if session is not None:
                _recover(self.root, session.path, error)
            _warn_once("lookup")
            return Lookup(False)
        finally:
            _state.lock.release()
        # JSON construction and checksum work do not hold the SQLite lock.
        answer = _decode(key, row[:2]) if row is not None else Lookup(False)
        self._remember_artifact(key, row, answer)
        return answer if self._usable(key) else Lookup(False)


    def lookup_many(self, keys: Sequence[Key | None]) -> BatchLookup:
        """Decode only caller-supplied unique keys, with per-kind counts.

        A None key (`make_key` refusing) is a miss, as it is for `lookup`."""
        misses = dict.fromkeys(keys, Lookup(False))
        values = misses.copy()
        groups: dict[str, list[Key]] = {}
        for key in values:
            if key is not None and self._usable(key):
                groups.setdefault(key.kind, []).append(key)
        for kind, supplied in groups.items():
            for offset in range(0, len(supplied), SOURCE_CHUNK):
                chunk = supplied[offset:offset + SOURCE_CHUNK]
                rows = self._artifact_rows(kind, chunk)
                if rows is None:
                    return _lookup_result(misses)
                # Each chunk's transfer is bounded. Decode outside SQLite's
                # lock before selecting the next chunk, rather than retaining
                # every requested encoded payload alongside decoded values.
                for key in chunk:
                    row = rows.get(key.digest)
                    if row is not None:
                        values[key] = _decode(key, row[:2])
                        self._remember_artifact(key, row, values[key])
        if not self._usable():
            return _lookup_result(misses)
        return _lookup_result(values if self._usable() else misses)

    def _remember_artifact(self, key: Key, row: tuple | None, answer: Lookup) -> None:
        try:
            if row is not None and type(row[2]) is int:
                self._artifact_used[key] = row[2]
            if answer.hit and self._usable(key):
                self._touch_artifact(key)
        except _CACHE_ERRORS:
            # A decoded answer is already valid. A failed bookkeeping insert
            # can follow byte-budget accounting, so discard all partial writes.
            self._discard_staging()
            _warn_once('lookup_bookkeeping')

    def _touch_artifact(self, key: Key) -> None:
        if key in self._rows or int(time.time()) - self._artifact_used.get(key, 0) < TOUCH_INTERVAL_S:
            return
        size = len(key.kind) * 6 + 256
        count = len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)
        if key not in self._artifact_touches:
            if count >= MAX_STAGED_ROWS or self._bytes + size > MAX_STAGED_BYTES:
                return
            self._bytes += size
        self._artifact_touches[key] = int(time.time())

    def _artifact_rows(self, kind: str, keys: Sequence[Key]) -> dict[str, tuple] | None:
        if not self._usable() or not _state.lock.acquire(blocking=False):
            return None
        session = None
        try:
            if not self._usable():
                return None
            with _database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return None
                supplied = [key.digest for key in keys]
                parameters: list[object] = [MAX_PAYLOAD_BYTES, kind, *supplied]
                parameters.extend([None] * (SOURCE_CHUNK - len(supplied)))
                rows = {row[0]: row[1:] for row in session.conn.execute(_ARTIFACT_MANY_SQL, parameters)}
                session.last_used = _clock()
                return rows
        except _CACHE_ERRORS as error:
            if session is not None:
                _recover(self.root, session.path, error)
            _warn_once('lookup')
            return None
        finally:
            _state.lock.release()

    def _source_rows(self, paths: Sequence[str]) -> dict[str, tuple]:
        if not paths or not self._usable() or not _state.lock.acquire(blocking=False):
            return {}
        session = None
        try:
            if not self._usable():
                return {}
            with _database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return {}
                found = {}
                for offset in range(0, len(paths), SOURCE_CHUNK):
                    chunk: list[str | None] = list(paths[offset:offset + SOURCE_CHUNK])
                    chunk.extend([None] * (SOURCE_CHUNK - len(chunk)))
                    for row in session.conn.execute(_SOURCE_SQL, chunk):
                        found[row[0]] = row
                session.last_used = _clock()
                return found
        except _CACHE_ERRORS as error:
            if session is not None:
                _recover(self.root, session.path, error)
            _warn_once("source")
            return {}
        finally:
            _state.lock.release()

    def _materialized_rows(self, paths: Sequence[str]) -> list[tuple] | None:
        if not paths or not self._usable() or not _state.lock.acquire(blocking=False):
            return []
        session = None
        try:
            if not self._usable():
                return []
            with _database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session):
                    return []
                supplied: list[object] = [MAX_KEY_BYTES, MAX_KEY_BYTES, MAX_KEY_BYTES, *paths]
                supplied.extend([None] * (SOURCE_CHUNK - len(paths)))
                rows = _history_transfer(session.conn.execute(_MATERIALIZED_SQL, supplied))
                session.last_used = _clock()
                return rows
        except _CACHE_ERRORS as error:
            if session is not None:
                _recover(self.root, session.path, error)
            _warn_once('materialized')
            return []
        finally:
            _state.lock.release()

    def materialized(self, paths: Sequence[Path]) -> dict[Path, tuple[Materialized, ...]]:
        """Advisory work history for explicit paths, including vanished ones."""
        requested: dict[Path, tuple[Materialized, ...]] = dict.fromkeys(paths, ())
        relatives = {path: relative for path in requested if (relative := _relative(self.root, path)) is not None}
        supplied = list(dict.fromkeys(relatives.values()))
        decoded: dict[str, list[Materialized]] = {}
        count, size = 0, 0
        for offset in range(0, len(supplied), SOURCE_CHUNK):
            rows = self._materialized_rows(supplied[offset:offset + SOURCE_CHUNK])
            if rows is None:
                return requested
            count += len(rows)
            size += sum(_history_size(row) for row in rows)
            if count > MAX_HISTORY_ROWS or size > MAX_HISTORY_BYTES:
                _warn_once('materialized_budget')
                return requested
            for row in rows:
                value = _decode_materialized(self.root, row)
                if value is not None:
                    decoded.setdefault(row[0], []).append(value)
                    self._remember_metadata(self._history_known, row[:3], row)
        if not self._usable():
            return requested
        return {path: tuple(sorted(decoded.get(relatives.get(path, ''), ()),
                                   key=lambda item: (item.kind, item.instance))) for path in requested}

    def _remember_metadata(self, known: dict, key: object, row: tuple) -> None:
        try:
            known[key] = row
        except _CACHE_ERRORS:
            # The source/hash or history value was already verified. This memo
            # only avoids redundant touches; losing it cannot lose that answer.
            self._discard_staging()
            _warn_once('metadata_bookkeeping')

    def _stage_materialized(self, row: tuple) -> None:
        key = row[:3]
        previous = self._history_known.get(key)
        if (previous is not None and previous[:4] + previous[5:] == row[:4] + row[5:]
                and row[4] - previous[4] < TOUCH_INTERVAL_S):
            return
        size = len(_canonical(list(row), MAX_KEY_BYTES * 2) or '')
        old = self._materialized_sizes.get(key, 0)
        count = len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)
        if (not self._usable() or (key not in self._materialized and count >= MAX_STAGED_ROWS)
                or self._bytes - old + size > MAX_STAGED_BYTES):
            return
        self._materialized[key] = row
        self._materialized_sizes[key] = size
        self._bytes += size - old

    def mark_materialized(self, path: Path, kind: str, *, instance: object = None,
                          built_from: str = '') -> None:
        try:
            relative, canonical = _relative(self.root, path), _instance(instance)
            if relative is not None and canonical is not None:
                self.materialized([path])
                row = _materialized_row(relative, kind, canonical, built_from, None)
                if row is not None:
                    self._stage_materialized(row)
        except _CACHE_ERRORS:
            self._discard_staging()
            _warn_once('materialized_admission')

    def copy_materialized(self, old: Path, new: Path, *,
                          instance_map: Callable[[str, str], object] | None = None) -> None:
        relative = _relative(self.root, new)
        if relative is None:
            return
        prepared = {}
        for item in self.materialized([old])[old]:
            # A mapper receives canonical text. Its canonical-string result
            # must not be encoded again; structured JSON results are also
            # canonicalized for the plan's object-valued hook interface.
            mapped = item.instance if instance_map is None else instance_map(item.kind, item.instance)
            canonical = mapped if isinstance(mapped, str) else _instance(mapped)
            if canonical is None or not _canonical_instance(canonical):
                _warn_once('instance')
                return
            row = _materialized_row(relative, item.kind, canonical, '', None)
            if row is None or row[:3] in prepared:
                _warn_once('instance')
                return
            prepared[row[:3]] = row
        sizes = {identity: len(_canonical(list(row), MAX_KEY_BYTES * 2) or '')
                 for identity, row in prepared.items()}
        delta = sum(size - self._materialized_sizes.get(identity, 0) for identity, size in sizes.items())
        count = (len(self._rows) + len(self._sources) + len(self._artifact_touches)
                 + len(self._materialized.keys() | prepared.keys()))
        if not self._usable() or count > MAX_STAGED_ROWS or self._bytes + delta > MAX_STAGED_BYTES:
            return
        # Admit the complete selector set, rather than silently collapse or
        # truncate units. Oversize history remains an optional cold warm-up.
        self._materialized.update(prepared)
        self._materialized_sizes.update(sizes)
        self._bytes += delta

    def _family_rows(self, key: Key, inputs: Sequence[ValidatedInput], instance: object) -> dict | None:
        canonical = _instance(instance)
        if canonical is None:
            return None
        hashes: dict[str, str] = {}
        for item in inputs:
            relative = _relative(self.root, item.path)
            if relative is None:
                return None
            digest = '' if item.content_hash == ABSENT else item.content_hash
            # Two byte snapshots of one path cannot establish one current
            # built_from. Retain history with an explicitly unknown hash.
            hashes[relative] = digest if relative not in hashes else (
                digest if hashes[relative] == digest else '')
        rows = {}
        for path, digest in hashes.items():
            row = _materialized_row(path, key.kind, canonical, digest, key)
            if row is None:
                return None
            rows[row[:3]] = row
        return rows

    def _discard_staging(self) -> None:
        self._rows.clear()
        self._sources.clear()
        self._source_sizes.clear()
        self._materialized.clear()
        self._materialized_sizes.clear()
        self._artifact_touches.clear()
        self._bytes = 0

    def _store_family(self, key: Key | None, value: object, inputs: Sequence[ValidatedInput],
                      instance: object) -> bool:
        try:
            return self._admit_family(key, value, inputs, instance)
        except _CACHE_ERRORS:
            # The caller's answer already exists. Allocation can fail after
            # one map changed, so none of this batch's partial staging may land.
            self._discard_staging()
            _warn_once('family_admission')
            return False

    def _admit_family(self, key: Key | None, value: object, inputs: Sequence[ValidatedInput],
                      instance: object) -> bool:
        if key is None or not self._usable(key):
            return False
        payload = _canonical(value, MAX_PAYLOAD_BYTES)
        metadata = self._family_rows(key, inputs, instance)
        if payload is None or metadata is None:
            return False
        sizes = {identity: len(_canonical(list(row), MAX_KEY_BYTES * 2) or '')
                 for identity, row in metadata.items()}
        old_payload = len(self._rows[key][2]) if key in self._rows else 0
        touch_size = len(key.kind) * 6 + 256 if key in self._artifact_touches else 0
        delta = len(payload) - old_payload - touch_size + sum(
            size - self._materialized_sizes.get(identity, 0) for identity, size in sizes.items())
        count = (len(self._rows.keys() | {key}) + len(self._materialized.keys() | metadata.keys())
                 + len(self._artifact_touches.keys() - {key}))
        if count > MAX_STAGED_ROWS or self._bytes - sum(self._source_sizes.values()) + delta > MAX_STAGED_BYTES:
            return False
        if count + len(self._sources) > MAX_STAGED_ROWS or self._bytes + delta > MAX_STAGED_BYTES:
            self._bytes -= sum(self._source_sizes.values())
            self._sources.clear()
            self._source_sizes.clear()
        # Prepare the whole family before mutating any staging map. Subsequent
        # flush publishes all rows in one transaction, or none on failure.
        self._artifact_touches.pop(key, None)
        self._rows[key] = (key.kind, key.digest, payload, _check(key, payload), int(time.time()))
        self._materialized.update(metadata)
        self._materialized_sizes.update(sizes)
        self._bytes += delta
        return True

    def _refresh_family(self, key: Key, inputs: Sequence[ValidatedInput], instance: object) -> None:
        try:
            self._refresh_family_rows(key, inputs, instance)
        except _CACHE_ERRORS:
            self._discard_staging()
            _warn_once('family_refresh')

    def _refresh_family_rows(self, key: Key, inputs: Sequence[ValidatedInput], instance: object) -> None:
        rows = self._family_rows(key, inputs, instance)
        if rows is None:
            return
        additions = {identity: row for identity, row in rows.items()
                     if identity not in self._history_known
                     or self._history_known[identity][:4] + self._history_known[identity][5:] != row[:4] + row[5:]
                     or row[4] - self._history_known[identity][4] >= TOUCH_INTERVAL_S}
        size = sum(len(_canonical(list(row), MAX_KEY_BYTES * 2) or '') for row in additions.values())
        count = len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches)
        if count + len(additions) > MAX_STAGED_ROWS or self._bytes + size > MAX_STAGED_BYTES:
            return
        for row in additions.values():
            self._stage_materialized(row)

    def _bytes_for(self, kind: Kind, values: tuple[ValidatedInput, ...], *,
                   required: bool = False) -> tuple[ValidatedInput, ...]:
        needed = [Input(item.role, item.path) for item in values if item.data is None and item.content_hash != ABSENT]
        fetched = iter(self.validate(kind, needed, with_bytes=True))
        result = tuple(next(fetched) if item.data is None and item.content_hash != ABSENT else item for item in values)
        # Collection membership was established by the live listing. A member
        # disappearing is a failed required snapshot even when ordinary file
        # inputs with this role deliberately permit ABSENT (S4 fidelity gate).
        if required and any(item.content_hash == ABSENT for item in result):
            self._abort_input(FileNotFoundError('collection member disappeared'))
        return result

    def _compute_view(self, files: tuple[ValidatedInput, ...],
                      collections: tuple[CollectionDigest, ...]) -> DerivationInputs:
        return DerivationInputs(
            tuple(ComputeInput(item.role, item.content_hash, item.data, ()) for item in files),
            tuple(ComputeCollection(collection.role, collection.content_hash, tuple(
                ComputeInput(item.role, item.content_hash, item.data,
                             tuple(Path(_relative(self.root, item.path) or '').parts)) for item in collection.members))
                  for collection in collections))

    def derive(self, kind: Kind, inputs: Sequence[Input], *, params: Mapping[str, object],
               compute: Callable[[DerivationInputs, Mapping[str, object]], object],
               collections: Sequence[Collection] = (), instance: object = None) -> object:
        self.begin_compute()  # Empty-input keys still represent caller work.
        files = self.validate(kind, inputs)
        groups = tuple(self.collection(kind, collection) for collection in collections)
        pairs = [(item.role, item.content_hash) for item in files]
        key = make_key(kind, pairs, params=params, collections=[(group.role, group.content_hash) for group in groups])
        hit = self.lookup(key)
        if hit.hit and key is not None:
            self._refresh_family(key, (*files, *(item for group in groups for item in group.members)), instance)
            return hit.value
        files = self._bytes_for(kind, files)
        groups = tuple(replace(group, members=self._bytes_for(kind, group.members, required=True)) for group in groups)
        groups = tuple(replace(group, content_hash=_collection_hash(self.root, group.members)) for group in groups)
        key = make_key(kind, [(item.role, item.content_hash) for item in files], params=params,
                       collections=[(group.role, group.content_hash) for group in groups])
        value = compute(self._compute_view(files, groups), params)
        if isinstance(value, Uncached):
            return value.value
        self._store_family(key, value, (*files, *(item for group in groups for item in group.members)), instance)
        return value

    def store_written(self, kind: Kind, inputs: Sequence[WrittenInput], value: object, *,
                      params: Mapping[str, object], instance: object = None) -> Key | None:
        self._usable()  # capture before hashing the writer's supplied snapshot
        # Known bytes are the writer's snapshot. Never stat/reopen them or
        # infer an immediately trustworthy source stamp from a completed write.
        files = tuple(ValidatedInput(item.role, item.path, hashlib.sha256(item.data).hexdigest(), None, item.data)
                      for item in inputs)
        key = make_key(kind, [(item.role, item.content_hash) for item in files], params=params)
        return key if self._store_family(key, value, files, instance) else None

    def _stage_source(self, path: str, stamp: statcache.Stamp, digest: str) -> None:
        try:
            self._admit_source(path, stamp, digest)
        except _CACHE_ERRORS:
            # Bytes and SHA have already succeeded. Neither insert may leave
            # a partial source family or prevent the caller using its snapshot.
            self._discard_staging()
            _warn_once('source_admission')

    def _admit_source(self, path: str, stamp: statcache.Stamp, digest: str) -> None:
        if not self._usable():
            return
        check = _source_check(path, stamp, digest)
        row = (path, *stamp[1:4], str(stamp[4]), digest, check, int(time.time()))
        previous = self._source_known.get(path)
        if (previous is not None and type(previous[7]) is int and previous[:7] == row[:7]
                and row[7] - previous[7] < TOUCH_INTERVAL_S):
            return
        if check is None:
            return
        size = len(path) * 6 + 256
        old_size = self._source_sizes.get(path, 0)
        if ((path not in self._sources and len(self._sources) + len(self._rows) + len(self._materialized) + len(self._artifact_touches) >= MAX_STAGED_ROWS)
                or self._bytes - old_size + size > MAX_STAGED_BYTES):
            return
        self._sources[path] = row
        self._source_sizes[path] = size
        self._bytes += size - old_size

    def put(self, key: Key | None, value: object) -> None:
        if key is None or not self._usable(key):
            return
        payload = _canonical(value, MAX_PAYLOAD_BYTES)
        if payload is None:
            _warn_once("payload")
            return
        old = self._rows.get(key)
        old_size = len(old[2]) if old is not None else 0
        # Bound request memory and its single transaction. Excess values
        # remain available to the caller without occupying optional storage.
        if ((key not in self._rows
             and len(self._rows) + len(self._sources) + len(self._materialized) + len(self._artifact_touches) >= MAX_STAGED_ROWS)
                or self._bytes - old_size + len(payload) > MAX_STAGED_BYTES):
            return
        try:
            check = _check(key, payload)
            self._rows[key] = (key.kind, key.digest, payload, check, int(time.time()))
            self._bytes += len(payload) - old_size
        except _CACHE_ERRORS:
            _warn_once("payload")

    def flush(self) -> None:
        rows, self._rows = self._rows, {}
        sources, self._sources = self._sources, {}
        metadata, self._materialized = self._materialized, {}
        touches, self._artifact_touches = self._artifact_touches, {}
        self._source_sizes.clear()
        self._materialized_sizes.clear()
        self._bytes = 0
        if (not rows and not sources and not metadata and not touches) or not self._usable(strict=True) or not _state.lock.acquire(timeout=WRITE_LOCK_SECONDS):
            return
        session = None
        try:
            if not self._usable(strict=True):
                return
            with _database(self.root, expected_generation=self._generation) as session:
                if session is None or not self._session_current(session, strict=True):
                    return
                session.conn.execute("BEGIN IMMEDIATE")
                if not self._session_current(session, strict=True):
                    session.conn.rollback()
                    return
                session.conn.executemany(
                    'INSERT INTO artifacts(kind,key_digest,payload,"check",last_used) VALUES (?,?,?,?,?) '
                    'ON CONFLICT(kind,key_digest) DO UPDATE SET payload=excluded.payload,'
                    '"check"=excluded."check",last_used=excluded.last_used', rows.values())
                session.conn.executemany(
                    'INSERT INTO sources(path,mtime_ns,ctime_ns,size,ino,content_hash,"check",last_used) '
                    'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET '
                    'mtime_ns=excluded.mtime_ns,ctime_ns=excluded.ctime_ns,size=excluded.size,'
                    'ino=excluded.ino,content_hash=excluded.content_hash,"check"=excluded."check",'
                    'last_used=excluded.last_used', sources.values())
                # Associations describe an already bounded artifact family.
                # Touches never enumerate paths or change a reader's answer.
                session.conn.executemany(
                    'UPDATE artifacts SET last_used=? WHERE kind=? AND key_digest=? AND last_used<?',
                    ((used, key.kind, key.digest, used - TOUCH_INTERVAL_S + 1) for key, used in touches.items()))
                session.conn.executemany(
                    'UPDATE materialized SET last_used=? WHERE artifact_kind=? AND artifact_key=? AND last_used<?',
                    ((used, key.kind, key.digest, used - TOUCH_INTERVAL_S + 1) for key, used in touches.items()))
                session.conn.executemany(
                    'INSERT INTO materialized(path,kind,instance,built_from,last_used,"check",artifact_kind,artifact_key) '
                    'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path,kind,instance) DO UPDATE SET '
                    'built_from=excluded.built_from,last_used=excluded.last_used,"check"=excluded."check",'
                    'artifact_kind=excluded.artifact_kind,artifact_key=excluded.artifact_key', metadata.values())
                if not self._session_current(session, strict=True):
                    session.conn.rollback()
                    return
                session.conn.commit()
                # Publication cannot wait for this SQL exclusion. If it lands
                # after our last pre-commit check, erase the just-committed
                # snapshot before releasing that exclusion (S5 Codex gate).
                if not self._session_current(session, strict=True):
                    marker = _read_marker(self.root)
                    _purge_connection(session.conn, marker if marker is not None else self._generation or '')
                    return
                _maintain(session)
                session.last_used = _clock()
        except _CACHE_ERRORS as error:
            if session is not None:
                _recover(self.root, session.path, error)
            _warn_once("flush")
        finally:
            _state.lock.release()


@contextlib.contextmanager
def batch(root: Path | None = None) -> Iterator[Batch]:
    _process_state()
    # Resolve the authoritative root inside the lifecycle identity, not before
    # it. A slow pointer read crossing a switch cannot adopt the new epoch for
    # the old root (independent brutal review, Spec03 S1).
    identity = (os.getpid(), _state.epoch, bool(_state.transitions))
    request = Batch(Path(root) if root is not None else paths.home(), identity=identity)
    request._maintenance()
    try:
        yield request
    except BaseException:
        request._discard_staging()
        raise
    else:
        request.flush()


def _warn_once(failure_kind: str) -> None:
    _process_state()
    if failure_kind in _state.warned:
        return
    _state.warned.add(failure_kind)
    # Degradation reporting cannot turn a disposable memo into a failed
    # caller. Exception text and paths deliberately never reach this sink.
    with contextlib.suppress(*_CACHE_ERRORS):
        logs.record("warning", "store.compiled", "Compiled cache unavailable",
                    failure_kind=failure_kind)


def _linked(info: os.stat_result) -> bool:
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT))


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


def _location(root: Path, device: str) -> Path | None:
    # Every path _location proves lives in .cache/compiled or above it, so
    # nothing it checks can change without moving the control snapshot: a
    # sidecar or a link arriving moves the directory, a marker or intent its
    # own entry. A proved location stands while the snapshot does.
    taken = _wall_ns()
    snapshot = _control_snapshot(root)
    memo = _state.locations.get((root, device))
    if snapshot is not None and memo is not None and memo[0] == snapshot:
        return memo[1]
    _state.locations.pop((root, device), None)
    path = _prove_location(root, device)
    if path is not None and snapshot is not None and _settled(snapshot, taken):
        _state.locations[(root, device)] = (snapshot, path)
    return path


def _prove_location(root: Path, device: str) -> Path | None:
    try:
        parent = root / ".cache"
        if not _directory(parent) or not _directory(parent / "compiled"):
            _warn_once("unsafe_path")
            return None
        path = parent / "compiled" / f"{device}-v{SCHEMA}.sqlite"
        # SQLite can inspect a hot sidecar while opening an existing file.
        # Reject every recognized sidecar before even the ownership read.
        candidates = [path, *(Path(str(path) + suffix) for suffix in
                              ("-journal", "-wal", "-shm", ".corrupt"))]
        if not _controls_safe(parent / "compiled") or not _leaf(path.with_suffix(".alive")) or not all(_leaf(candidate) for candidate in candidates):
            _warn_once("unsafe_path")
            return None
        return path
    except OSError:
        _warn_once("storage")
        return None


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


LEASE_CHECK_S = 60.0


def _renew_lease(session: _Session) -> None:
    # The lease needs a touch a day; a check per operation re-proved every
    # sidecar on the hot path. Skipping one only ever skips a write.
    now = time.time()
    if 0 <= now - session.leased < LEASE_CHECK_S:
        return
    session.leased = now
    lease = session.path.with_suffix('.alive')
    try:
        if not _cache_file_safe(session.path):
            return
        if not lease.exists():
            atomic.write_text(lease, '')
            os.utime(lease, (now, now))
        elif now - lease.stat().st_mtime >= TOUCH_INTERVAL_S:
            os.utime(lease, (now, now))
        if now - session.retained >= MAINTENANCE_INTERVAL_S:
            session.retained = now
            _sweep_retention(session)
    except _CACHE_ERRORS:
        _warn_once('lease')


def _cache_filename(path: Path) -> str:
    # Owned names are ASCII hex/schema spellings. Canonicalize on every OS:
    # POSIX normcase is identity even on insensitive APFS volumes. Sharing
    # exclusion for distinct case aliases on sensitive volumes is conservative.
    return path.name.lower()


def _probe_recognized(path: Path) -> bool:
    match = _CACHE_FILE.fullmatch(_cache_filename(path).removesuffix('.corrupt'))
    if _sqlite is None or match is None:
        return False
    conn = _sqlite.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True,
                           timeout=0.005, isolation_level=None)
    try:
        conn.setlimit(_sqlite.SQLITE_LIMIT_LENGTH, 2 * MAX_PAYLOAD_BYTES)
        schema = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',)).fetchone()
        generation = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_generation',)).fetchone()
        return (conn.execute('PRAGMA application_id').fetchone()[0] == APPLICATION_ID
                and schema == (match[2],) and generation is not None
                and isinstance(generation[0], str) and (generation[0] == '' or bool(_DEVICE_KEY.fullmatch(generation[0]))))
    finally:
        conn.close()


def _sweep_retention(session: _Session) -> None:
    # Only recognized, owned disposable files are eligible. The current
    # local schema is exempt even when an old mtime or clock skew suggests
    # dormancy; obsolete schemas keep their own independent lease.
    for path in session.path.parent.iterdir():
        original = path.with_name(_cache_filename(path).removesuffix('.corrupt'))
        if original == session.path or not _CACHE_FILE.fullmatch(original.name):
            continue
        fd = None
        try:
            if not _cache_file_safe(original) or not _cache_file_safe(path):
                continue
            # Recovery keeps the canonical schema lease even when replacement
            # failed and only its aside remains. Dormancy must age that copy too.
            lease = original.with_suffix('.alive')
            used = _retention_used(lease, path)
            if time.time() - used <= RETENTION_S:
                continue
            fd = _recovery_lock(session.root, original)
            if fd is None or not _cache_file_safe(original) or not _cache_file_safe(path) or not _probe_recognized(path):
                continue
            used = _retention_used(lease, path)
            if time.time() - used <= RETENTION_S:
                continue  # Another process renewed the lease before exclusion.
            _retire_owned_files(path, original, lease)
        except _CACHE_ERRORS:
            _warn_once('retention')
        finally:
            if fd is not None:
                with contextlib.suppress(*_CACHE_ERRORS):
                    proclock.release(fd)


def _retention_used(lease: Path, path: Path) -> float:
    return (lease if lease.exists() else path).stat().st_mtime


def _retire_owned_files(path: Path, original: Path, lease: Path) -> None:
    if path != original:
        _retire_owned_aside(path, original, lease)
    else:
        for candidate in [path, lease, *(Path(str(path) + suffix)
                          for suffix in ('-journal', '-wal', '-shm'))]:
            candidate.unlink(missing_ok=True)
        _erase_recognized_aside(Path(str(path) + '.corrupt'))


def _retire_owned_aside(path: Path, original: Path, lease: Path) -> None:
    # Called only after canonical exclusion, current ownership and lease proof.
    if _erase_recognized_aside(path) and not original.exists():
        lease.unlink(missing_ok=True)


def _erase_recognized_aside(path: Path) -> bool:
    # A healthy owned neighbour never proves this separate file's ownership.
    # All callers already hold the canonical operation/recovery exclusion.
    try:
        if (_cache_file_safe(path) and path.exists()
                and not Path(str(path) + '-journal').exists() and _probe_recognized(path)):
            path.unlink()
            return True
    except _CACHE_ERRORS:
        _warn_once('aside')
    return False


def _cache_parent(root: Path) -> Path | None:
    parent = root / '.cache'
    if not _directory(parent) or not _directory(parent / 'compiled'):
        return None
    parent /= 'compiled'
    return parent if _controls_safe(parent) else None


def _read_marker(root: Path) -> str | None:
    try:
        for parent in (root / '.cache', root / '.cache' / 'compiled'):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                return ''
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                return None
        path = root / '.cache' / 'compiled' / 'purge'
        if not _leaf(path):
            return None
        try:
            with path.open('r', encoding='ascii') as stream:
                value = stream.read(66).strip()
        except FileNotFoundError:
            return ''
        return value if _DEVICE_KEY.fullmatch(value) else None
    except _CACHE_ERRORS:
        _warn_once('marker')
        return None


def _read_generation(conn: _sqlite_available.Connection) -> str | None:
    row = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_generation',)).fetchone()
    if row is not None and isinstance(row[0], str) and (row[0] == '' or _DEVICE_KEY.fullmatch(row[0])):
        return row[0]
    return None


def _publish_marker(root: Path, token: str | None = None) -> str | None:
    try:
        parent = _cache_parent(root)
        if parent is None:
            raise OSError('unsafe cache controls')
        token = token or secrets.token_hex(32)
        atomic.write_text(parent / 'purge', token + '\n')
        return token
    except _CACHE_ERRORS:
        _warn_once('marker_publish')
        return None


def _read_intents(root: Path, *, token: str | None = None) -> list[tuple[str, str]] | None:
    parent = root / '.cache' / 'compiled' / 'delete-intents'
    try:
        info = parent.lstat()
    except FileNotFoundError:
        return []
    except _CACHE_ERRORS:
        return None
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
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
    except _CACHE_ERRORS:
        _warn_once('intent_read')
        return None


def _read_intent(parent: Path, token: str) -> list[tuple[str, str]] | None:
    path = parent / token
    if not re.fullmatch(r'[0-9a-f]{32}', token) or not _leaf(path):
        return None
    if not path.exists():
        return []
    with path.open('r', encoding='ascii') as stream:
        value = json.loads(stream.read(256))
    if (not isinstance(value, dict) or set(value) != {'token', 'owner'}
            or value['token'] != token or not isinstance(value['owner'], str)
            or not _DEVICE_KEY.fullmatch(value['owner'])):
        return None
    return [(token, value['owner'])]


def _intent_lock(root: Path, token: str) -> int | None:
    return proclock.acquire(proclock.lock_path(root, 'compiled-delete', token), proclock.NO_WAIT)


def prepare_delete(root: Path) -> DeleteIntent | None:
    """Durably fence optional storage before an authorized private delete."""
    _process_state()
    # Fork must not inherit the acquisition-to-registration descriptor gap.
    # This mutex protects intent resources only; preparation performs no SQL.
    with _state.intent_lock:
        return _prepare_delete(root)


def _prepare_delete(root: Path) -> DeleteIntent | None:
    fd = None
    try:
        _release_finished_intents_locked()
        root = Path(root)
        parent = _cache_parent(root)
        device = _device(root)
        if parent is None or device is None:
            return None
        directory = parent / 'delete-intents'
        if not _directory(directory):
            return None
        token = secrets.token_hex(16)
        if not _leaf(directory / token):
            return None
        fd = _intent_lock(root, token)
        if fd is None:
            return None
        owner = _state.owners[root]
        atomic.write_text(directory / token, json.dumps({'token': token, 'owner': owner}, separators=(',', ':')))
        intent = DeleteIntent(token, owner, fd, root)
        _state.intents[token] = intent
        fd = None
        return intent
    except _CACHE_ERRORS:
        _warn_once('intent_publish')
        return None
    finally:
        if fd is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                proclock.release(fd)


def _close_inherited_intents() -> None:
    # Child descriptors share their parent's open-file lock. Close the child
    # reference without issuing an unlock that could abandon the live parent.
    for intent in tuple(_state.intents.values()):
        if intent.descriptor is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                os.close(intent.descriptor)
            intent.descriptor = None


def _release_intents() -> None:
    with _state.intent_lock:
        _release_intents_locked()


def _release_intents_locked() -> None:
    for intent in tuple(_state.intents.values()):
        if intent.descriptor is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                proclock.release(intent.descriptor)
            intent.descriptor = None
    _state.intents.clear()
    _state.finished_intents.clear()


def _release_finished_intents_locked() -> None:
    # A completion cannot wait behind another preparation's filesystem I/O.
    # Keep its descriptor registered for fork safety until this mutex is
    # available; releasing it makes the durable intent recoverable normally.
    # Completed resources have their own queue: 128 live preparations ahead
    # of one finished delete cannot permanently strand its descriptor.
    for _ in range(128):
        if not _state.finished_intents:
            break
        intent = _state.finished_intents[0]
        _release_finished_intent(intent)
        _state.finished_intents.popleft()
    # Allocation failure while queueing still leaves the flag on its already
    # registered resource. A bounded fallback can release those owners too.
    for intent in list(itertools.islice(_state.intents.values(), 128)):
        if intent.finished:
            _release_finished_intent(intent)


def _release_finished_intent(intent: DeleteIntent) -> None:
    if intent.descriptor is not None:
        proclock.release(intent.descriptor)
        intent.descriptor = None
    _state.intents.pop(intent.token, None)


def _release_finished_intents() -> None:
    if not _state.intent_lock.acquire(blocking=False):
        return
    try:
        _release_finished_intents_locked()
    except _CACHE_ERRORS:
        _warn_once('intent_release')
    finally:
        _state.intent_lock.release()


def _retire_intent(intent: DeleteIntent, *, no_wait: bool = False) -> bool:
    if not _state.intent_lock.acquire(blocking=not no_wait):
        return False
    try:
        _retire_intent_locked(intent)
        return True
    finally:
        _state.intent_lock.release()


def _retire_intent_locked(intent: DeleteIntent) -> None:
    try:
        if (intent.token, intent.owner) in (_read_intents(intent.root, token=intent.token) or []):
            (intent.root / '.cache' / 'compiled' / 'delete-intents' / intent.token).unlink()
    finally:
        _state.intents.pop(intent.token, None)
        if intent.descriptor is not None:
            proclock.release(intent.descriptor)
            intent.descriptor = None


def _owned_tables(conn: _sqlite_available.Connection, after: int | None) -> Iterator[tuple[int, str]]:
    # compiled-maintenance-sql: at most129 bounded owned schema probes; close before DROP
    for _ in range(SOURCE_CHUNK + 1):
        cursor = conn.execute("SELECT rowid,name FROM sqlite_master WHERE type='table' AND (? IS NULL OR rowid>?) ORDER BY rowid LIMIT 1", (after, after))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return
        identity, name = row
        if type(identity) is not int or not isinstance(name, str) or '\x00' in name:
            raise ValueError('invalid owned schema name')
        yield identity, name
        after = identity


def _quote_table(name: str) -> str:
    if not isinstance(name, str) or len(name) > 2 * MAX_PAYLOAD_BYTES or '\x00' in name:
        raise ValueError('invalid owned table name')
    return '"' + name.replace('"', '""') + '"'


def _purge_connection(conn: _sqlite_available.Connection, token: str) -> bool:
    # compiled-maintenance-sql: quote only bounded verified owned schema names
    try:
        conn.execute('PRAGMA secure_delete=ON')
        conn.execute('BEGIN IMMEDIATE')
        complete = (_purge_trigger_page(conn) and _purge_view_page(conn) and _purge_virtual_page(conn)
                    and _purge_table_page(conn, token))
        conn.commit()
        if not complete:
            return False
        # A failed vacuum leaves the old generation, so the next batch retries
        # physical erasure rather than treating a partial purge as complete.
        conn.execute('VACUUM')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE cache_meta SET value=? WHERE name=?', (token, 'purge_generation'))
        conn.commit()
        return True
    except _CACHE_ERRORS as error:
        with contextlib.suppress(*_CACHE_ERRORS):
            conn.rollback()
        # Corruption needs owned-file recovery, even with derivations disabled.
        # Returning False strands the old generation and prevents normal reads
        # from ever reaching their corruption recovery path after a delete.
        if _corruption(error):
            raise
        _warn_once('purge_busy')
        return False


def _purge_trigger_page(conn: _sqlite_available.Connection) -> bool:
    # compiled-maintenance-sql: stream at most129 triggers from an already verified owned file
    # Old disposable schemas can contain DELETE triggers that repopulate an
    # earlier erased table. Remove every trigger before erasing any payload;
    # bounded pages commit under the same durable, still-pending generation.
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY rowid LIMIT 129")
    for offset, (name,) in enumerate(rows):
        if offset == SOURCE_CHUNK:
            return False
        conn.execute(f'DROP TRIGGER {_quote_table(name)}')
    return True


def _purge_virtual_page(conn: _sqlite_available.Connection) -> bool:
    # compiled-maintenance-sql: at most128 quoted virtual-table drops from a verified owned file
    # Contentless FTS cannot DELETE rows. Its disposable index and shadows
    # must be discarded before ordinary erasure. rootpage=0/NULL identifies
    # virtual tables without transferring or parsing arbitrary schema SQL:
    # https://www.sqlite.org/schematab.html
    for _ in range(SOURCE_CHUNK):
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name)<>'cache_meta' AND (rootpage=0 OR rootpage IS NULL) ORDER BY rowid LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return True
        name = row[0]
        # A live sqlite_master cursor prevents DROP TABLE. LIMIT1 and close
        # transfer one bounded name and release it before dropping its shadows.
        conn.execute(f'DROP TABLE {_quote_table(name)}')
    return False


def _purge_view_page(conn: _sqlite_available.Connection) -> bool:
    # compiled-maintenance-sql: at most128 quoted view drops from a verified owned file
    # Views can hold private literals even with no rows. Their definitions
    # must disappear before claiming physical erasure, like virtual indexes.
    for _ in range(SOURCE_CHUNK):
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='view' ORDER BY rowid LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return True
        name = row[0]
        conn.execute(f'DROP VIEW {_quote_table(name)}')
    return False


def _purge_cursor(conn: _sqlite_available.Connection, token: str) -> int | None:
    # compiled-maintenance-sql: fixed256-byte progress value in an already verified owned file
    row = conn.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=256 THEN value END FROM cache_meta WHERE name=?", ('purge_progress',)).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        value = json.loads(row[0])
        if (isinstance(value, dict) and set(value) == {'token', 'cursor', 'check'}
                and value['token'] == token and type(value['cursor']) is int
                and -(2**63) <= value['cursor'] < 2**63
                and value['check'] == _purge_progress_check(token, value['cursor'])):
            return value['cursor']
    except _CACHE_ERRORS:
        pass
    return None


def _purge_progress_check(token: str, cursor: int | None) -> str:
    # Like artifact/source row checks, bind all progress fields: a valid-JSON
    # damaged integer must restart erasure instead of skipping owned tables.
    return hashlib.sha256(f'{token}\0{cursor}'.encode('ascii')).hexdigest()


def _purge_table_page(conn: _sqlite_available.Connection, token: str) -> bool:
    # compiled-maintenance-sql: quote only streamed names from a verified owned file,128 deletions per pass
    after = _purge_cursor(conn, token)
    for offset, (identity, name) in enumerate(_owned_tables(conn, after)):
        if offset == SOURCE_CHUNK:
            # Same transaction as the deletes: crashes never advance this
            # bounded integer cursor past work that did not commit. Rowid
            # includes legal empty names and avoids retaining long text cursors.
            conn.execute("INSERT INTO cache_meta(name,value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                         ('purge_progress', json.dumps({'token': token, 'cursor': after,
                                                       'check': _purge_progress_check(token, after)})))
            return False
        if name.lower() != 'cache_meta':
            if name.lower().startswith('sqlite_'):
                # SQLite owns the DDL of planner/sequence tables, which cannot
                # be dropped. Their orphaned statistics can still hold private
                # values, so erase their rows before the physical vacuum.
                conn.execute(f'DELETE FROM {_quote_table(name)}')
            else:
                conn.execute(f'DROP TABLE {_quote_table(name)}')
        after = identity
    # DEFAULT/CHECK expressions, column names and index definitions can also
    # hold private text. Keep ownership valid at every committed boundary,
    # then rebuild canonical public DDL in this final transaction, including
    # cache_meta itself. Unknown old layouts are disposable after a purge.
    schema = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',)).fetchone()
    generation = _read_generation(conn)
    if schema is None or not isinstance(schema[0], str) or generation is None:
        raise ValueError('invalid owned metadata')
    conn.execute('DROP TABLE cache_meta')
    _create_schema(conn, schema[0], generation)
    return True


def _purge_if_needed(conn: _sqlite_available.Connection, token: str) -> None:
    if _read_generation(conn) != token:
        _purge_connection(conn, token)


def _purge_file(root: Path, path: Path, token: str, current: _Session | None) -> bool:
    """Erase one owned file up to `token`. True only when this pass leaves
    nothing for a later one: the file is at `token`, or is not ours to touch."""
    if _sqlite is None:
        return False
    fd, conn = None, None
    recognized = False
    try:
        if not _cache_file_safe(path):
            return False
        if not _probe_recognized(path):
            return True  # foreign or unreadable: no pass ever erases it
        recognized = True
        if current is not None and current.path == path:
            _purge_if_needed(current.conn, token)
            return True
        fd = _recovery_lock(root, path)
        if fd is None or not _cache_file_safe(path):
            return False
        conn = _sqlite.connect(path, timeout=0.005, isolation_level=None)
        conn.setlimit(_sqlite.SQLITE_LIMIT_LENGTH, 2 * MAX_PAYLOAD_BYTES)
        conn.execute('PRAGMA busy_timeout=5')
        _purge_if_needed(conn, token)
        return True
    except _CACHE_ERRORS as error:
        if recognized and _corruption(error) and (fd is not None or (current is not None and current.path == path)):
            _erase_owned_corrupt(path, conn)
            conn = None
        else:
            _warn_once('purge')
        return False
    finally:
        if conn is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                conn.close()
        if fd is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                proclock.release(fd)


def _erase_owned_corrupt(path: Path, conn: _sqlite_available.Connection | None) -> None:
    if conn is not None:
        with contextlib.suppress(*_CACHE_ERRORS):
            conn.close()
    if _state.session is not None and _state.session.path == path:
        _close_locked(invalidate=False)
    # The caller holds the same NO_WAIT exclusion as ordinary recovery.
    # Erase a still-owned, still-corrupt disposable file rather than
    # trying SQL on its damaged pages. Do not create a replacement in
    # cache0 mode; the next enabled batch can initialize an empty one.
    # A hot journal has unproved before-images and remains untouched.
    if (_cache_file_safe(path) and _probe_recognized(path)
            and not Path(str(path) + '-journal').exists() and _still_corrupt(path)):
        path.unlink()


def _purge_corrupt_aside(root: Path, path: Path) -> bool:
    # Failed ordinary recovery can leave the only payload copy in an aside.
    # Durable delete maintenance must erase it even with BUILD/cache0 off.
    # Re-prove ownership under the original file's operation exclusion;
    # foreign/unreadable/linked asides and unproved journals remain untouched.
    original = path.with_name(_cache_filename(path).removesuffix('.corrupt'))
    fd = _recovery_lock(root, original)
    if fd is None:
        return False
    try:
        # An aside that survives this pass is retried by the next one.
        return _cache_file_safe(original) and _erase_recognized_aside(path)
    except _CACHE_ERRORS:
        # One unproved neighbour must not strand owned copies after every delete.
        _warn_once('purge_aside')
        return False
    finally:
        with contextlib.suppress(*_CACHE_ERRORS):
            proclock.release(fd)


def _purge_local_files(root: Path, token: str, current: _Session | None = None) -> bool:
    """Erase this device's files up to `token`; True when none is left behind."""
    device = _device(root)
    parent = _cache_parent(root)
    if _sqlite is None:
        _warn_once('purge_sqlite_unavailable')
        return False
    if device is None or parent is None:
        return False
    done = True
    for path in parent.iterdir():
        match = _CACHE_FILE.fullmatch(_cache_filename(path).removesuffix('.corrupt'))
        if match is not None and match[1] == device:
            if _cache_filename(path).endswith('.corrupt'):
                done = _purge_corrupt_aside(root, path) and done
            else:
                done = _purge_file(root, path, token, current) and done
    return done


def purge_for_delete(root: Path, *, intent: DeleteIntent | None = None) -> None:
    """Publish durable erasure work, then attempt owned in-place purges."""
    _process_state()
    try:
        root = Path(root)
        token = _publish_marker(root)
        if token is None:
            return
        if _state.lock.acquire(blocking=False):
            try:
                _purge_local_files(root, token)
            finally:
                _state.lock.release()
        if intent is not None and intent.root == root:
            _retire_intent(intent, no_wait=True)
    except _CACHE_ERRORS:
        _warn_once('purge')
    finally:
        # Failed publication keeps the durable request recoverable. Release
        # only this call's process resource; never another delete's fence.
        if intent is not None:
            intent.finished = True
            if intent.descriptor is not None:
                with contextlib.suppress(*_CACHE_ERRORS):
                    _state.finished_intents.append(intent)
            _release_finished_intents()


def _recover_pending(root: Path, current: _Session | None = None) -> bool:
    _release_finished_intents()
    intents = _read_intents(root)
    if intents is None:
        return False
    _device(root)
    owner = _state.owners.get(root)
    # A bounded page is work, not a malformed backlog. Erase owned payloads
    # even if active/foreign entries fill the page; those fences stay durable.
    foreign = [(token, device) for token, device in intents if device != owner]
    active = bool(foreign)
    generation = None
    for token, device in intents:
        if device != owner:
            continue
        fd = _intent_lock(root, token)
        if fd is None:
            active = True
            continue
        recovered = DeleteIntent(token, device, fd, root)
        try:
            if generation is None:
                generation = _publish_marker(root)
                if generation is None:
                    active = True
                    continue
                _purge_local_files(root, generation, current)
            # Preparation holds this mutex across durable I/O to close the
            # fork-registration gap. Optional readers must defer, never wait.
            if not _retire_intent(recovered, no_wait=True):
                active = True
        finally:
            if recovered.descriptor is not None:
                proclock.release(recovered.descriptor)
                recovered.descriptor = None
    if active:
        _purge_fenced_page(root, intents, current)
    return not active and _read_intents(root) == []


def _purge_fenced_page(root: Path, intents: list[tuple[str, str]], current: _Session | None) -> None:
    # Reuse an unchanged page's completed erasure generation rather than
    # repeatedly vacuuming for foreign/active owners we may never retire.
    token = hashlib.sha256(json.dumps(sorted(intents), separators=(',', ':')).encode('ascii')).hexdigest()
    # Intent removal can sync before its producer's marker. Persist this
    # observed boundary locally so disappearance cannot restore an older
    # generation and let a pre-delete batch republish erased text.
    if _read_marker(root) != token:
        _publish_marker(root, token)
    _purge_local_files(root, token, current)


def _pending_maintenance(root: Path) -> None:
    # Disabled derivations still finish already durable erasure work. This
    # never creates a database, requires no BUILD and never waits for SQLite
    # or a process lock. Async/campaign-held callers defer to the next batch.
    if _on_loop() or locks.campaign_locks_held():
        return
    marker, intents = _read_marker(root), _read_intents(root)
    if marker is None or (marker == '' and intents == []):
        return
    if not _state.lock.acquire(blocking=False):
        return
    try:
        if not _recover_pending(root):
            return
        marker = _read_marker(root)
        if marker:
            _purge_local_files(root, marker)
    except _CACHE_ERRORS:
        _warn_once('pending_purge')
    finally:
        _state.lock.release()


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
    cache = root / '.cache'
    compiled = cache / 'compiled'
    parts: list[tuple | None] = []
    for path in (cache, compiled, compiled / 'purge', compiled / 'delete-intents'):
        try:
            info = path.lstat()
            parts.append((info.st_dev, info.st_ino, info.st_mode, info.st_size,
                          info.st_mtime_ns, info.st_ctime_ns))
        except FileNotFoundError:
            parts.append(None)
        except (*_CACHE_ERRORS, AttributeError):
            return None  # no snapshot: every caller takes its full path
    return tuple(parts)


def _settled(snapshot: tuple, taken_ns: int) -> bool:
    # statcache's racy-clean rule: a timestamp within one coarse tick of when
    # the snapshot was taken could hide a second change landing in that tick.
    newest = max((max(part[4], part[5]) for part in snapshot if part is not None), default=0)
    return newest < taken_ns - statcache.RACY_WINDOW_NS


_wall_ns = time.time_ns  # injectable for the settle rule's tests


def _synchronize(session: _Session) -> bool:
    # A pass that finished every file, over control paths that have not moved
    # since and had settled before it began, has nothing new to find.
    snapshot = _control_snapshot(session.root)
    if snapshot is not None and snapshot == session.synced:
        try:
            if _read_generation(session.conn) == session.synced_marker:
                return True
        except _CACHE_ERRORS:
            pass  # a damaged handle is the full pass's to close and recover
    session.synced = session.synced_marker = None
    taken = _wall_ns()
    snapshot = _control_snapshot(session.root)
    if not _recover_pending(session.root, session):
        # A foreign fence records its own completed erasure generation.
        # Normalizing it to the local marker would force another VACUUM on
        # every unchanged batch. Incomplete local versions are still retried
        # by _recover_pending before it reports that the fence remains live.
        return False
    marker = _read_marker(session.root)
    if marker is None:
        return False
    # Retry obsolete versions too: a busy old file remains durable work even
    # when the current schema has already recorded this marker.
    done = _purge_local_files(session.root, marker, session)
    current = _read_generation(session.conn) == marker
    if current and done and snapshot is not None and _settled(snapshot, taken):
        session.synced, session.synced_marker = snapshot, marker
    return current


def _device(root: Path) -> str | None:
    if _state.device_unavailable:
        return None
    if root not in _state.devices:
        try:
            key = maintenance_reports.device_key(root)
            if not isinstance(key, str) or not _DEVICE_KEY.fullmatch(key):
                raise ValueError("device identity unavailable")
            _state.owners[root] = key
            _state.devices[root] = key[:32]
        except _CACHE_ERRORS:
            _state.device_unavailable = True
            _warn_once("device")
            return None
    return _state.devices[root]


def _close_locked(*, invalidate: bool = True) -> None:
    session, _state.session = _state.session, None
    if invalidate:
        _state.epoch += 1
    if session is not None:
        if session.timer is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                session.timer.cancel()
        try:
            session.conn.close()
        except _CACHE_ERRORS:
            _warn_once("close")


def close() -> None:
    """Close owned resources and invalidate prepared work; safe to repeat."""
    _process_state()
    with _state.lock:
        _close_locked()


@contextlib.contextmanager
def root_change() -> Iterator[None]:
    """Fence optional work throughout authoritative root publication."""
    _process_state()
    with _state.lock:
        _state.transitions += 1
        _close_locked()
    try:
        yield
    finally:
        with _state.lock:
            _close_locked()
            _state.transitions -= 1


def _schedule_idle(session: _Session, delay: float = IDLE_SECONDS) -> None:
    timer = _timer(delay, lambda: _idle(session))
    timer.daemon = True
    session.timer = timer
    timer.start()


def _idle(session: _Session) -> None:
    if os.getpid() != session.pid:
        return
    with _state.lock:
        if _state.session is not session:
            return
        remaining = IDLE_SECONDS - (_clock() - session.last_used)
        if remaining > 0:
            try:
                _schedule_idle(session, remaining)
            except _CACHE_ERRORS:
                _close_locked(invalidate=False)
                _warn_once('idle_timer')
        else:
            _close_locked(invalidate=False)


def _before_fork() -> None:
    _state.lock.acquire()
    _state.intent_lock.acquire()
    _close_locked()


def _after_fork_parent() -> None:
    _state.intent_lock.release()
    _state.lock.release()


def _after_fork_child() -> None:
    global _state  # noqa: PLW0603 -- replace inherited locks without acquiring them
    _close_inherited_intents()
    _state = _inherit_build(_State(), _state)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=_before_fork, after_in_parent=_after_fork_parent,
                        after_in_child=_after_fork_child)


def _live_bytes(conn: _sqlite_available.Connection) -> int:
    pages = conn.execute('PRAGMA page_count').fetchone()[0]
    free = conn.execute('PRAGMA freelist_count').fetchone()[0]
    return (pages - free) * conn.execute('PRAGMA page_size').fetchone()[0]


def _lru_victims(conn: _sqlite_available.Connection) -> list[tuple]:
    # compiled-maintenance-sql: bounded private LRU metadata, never membership
    artifacts = conn.execute("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used FROM artifacts ORDER BY used,rowid LIMIT 128").fetchall()
    sources = conn.execute("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used FROM sources ORDER BY used,rowid LIMIT 128").fetchall()
    materialized = conn.execute("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used FROM materialized ORDER BY used,rowid LIMIT 128").fetchall()
    return sorted([*(('artifacts', *row) for row in artifacts), *(('sources', *row) for row in sources),
                   *(('materialized', *row) for row in materialized)],
                  key=lambda row: row[2] if type(row[2]) is int else -1)[:128]


def _evict(conn: _sqlite_available.Connection, table: str, rowid: int) -> None:
    # compiled-maintenance-sql: remove only associations to an explicit victim rowid
    if table == 'artifacts':
        # Remove only the evicted association, so a newer projection at the
        # same path/instance survives replacement of its earlier artifact.
        conn.execute('DELETE FROM materialized WHERE (artifact_kind,artifact_key) IN '
                     '(SELECT kind,key_digest FROM artifacts WHERE rowid=?)', (rowid,))
        conn.execute('DELETE FROM artifacts WHERE rowid=?', (rowid,))
    elif table == 'sources':
        conn.execute('DELETE FROM sources WHERE rowid=?', (rowid,))
    else:
        conn.execute('DELETE FROM materialized WHERE rowid=?', (rowid,))


def _maintain(session: _Session) -> None:
    pending = session.conn.execute('SELECT value FROM cache_meta WHERE name=?', ('trimming',)).fetchone()
    session.trimming = session.trimming or pending == ('1',)
    now = time.time()
    if not session.trimming and now - session.maintained < MAINTENANCE_INTERVAL_S:
        return
    conn = session.conn
    if not session.trimming and _live_bytes(conn) <= SIZE_CAP_BYTES:
        session.maintained = now
        return
    session.trimming = True
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO cache_meta(name,value) VALUES ('trimming','1') "
                 "ON CONFLICT(name) DO UPDATE SET value='1'")
    for _ in range(MAX_EVICTION_CHUNKS):
        victims = _lru_victims(conn)
        if not victims:
            break
        for table, rowid, _used in victims:
            _evict(conn, table, rowid)
            if _live_bytes(conn) <= LOW_WATER_BYTES:
                break
        if _live_bytes(conn) <= LOW_WATER_BYTES:
            break
    conn.commit()
    _incremental_vacuum(conn)
    if _live_bytes(conn) <= LOW_WATER_BYTES:
        conn.execute("DELETE FROM cache_meta WHERE name='trimming'")
        session.trimming = False
        session.maintained = now
    # A bounded partial pass stays eligible at the next actual write, even
    # after it falls below the cap but has not reached low water (S5 review).


def _incremental_vacuum(conn: _sqlite_available.Connection) -> None:
    # Return at most VACUUM_PAGES free pages to the filesystem. The pragma has
    # no result columns, and Python 3.11's sqlite3 resets such a statement
    # after its first step, so there one call frees ONE page however many it
    # asks for; 3.12+ runs every step. Repeat until the budget is spent or
    # the freelist stops shrinking, which bounds both.
    freed = 0
    while freed < VACUUM_PAGES:
        before = conn.execute('PRAGMA freelist_count').fetchone()[0]
        if not before:
            return
        conn.execute('PRAGMA incremental_vacuum(256)').fetchall()
        step = before - conn.execute('PRAGMA freelist_count').fetchone()[0]
        if step <= 0:
            return
        freed += step


def _initialise(conn: _sqlite_available.Connection) -> None:
    # auto_vacuum must precede tables. Bootstrapping metadata and the
    # application id in one transaction exposes no partially owned database
    # to an artifact writer.
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("PRAGMA application_id=1196575053")
    _create_schema(conn, str(SCHEMA), '')
    conn.commit()


def _create_schema(conn: _sqlite_available.Connection, schema: str, generation: str) -> None:
    # Both first ownership and final purge rebuild use the same public DDL.
    # The caller owns the transaction; obsolete filenames retain their proved
    # version marker while unsupported old layouts can rebuild or miss safely.
    conn.execute("CREATE TABLE cache_meta(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
    conn.executemany("INSERT INTO cache_meta(name,value) VALUES (?,?)",
                     (("schema", schema), ("purge_generation", generation)))
    conn.execute('CREATE TABLE artifacts(kind TEXT NOT NULL,key_digest TEXT NOT NULL,'
                 'payload TEXT NOT NULL,"check" TEXT NOT NULL,last_used INTEGER NOT NULL,'
                 'PRIMARY KEY(kind,key_digest))')
    conn.execute('CREATE TABLE sources(path TEXT PRIMARY KEY,mtime_ns INTEGER NOT NULL,'
                 'ctime_ns INTEGER NOT NULL,size INTEGER NOT NULL,ino TEXT NOT NULL,'
                 'content_hash TEXT NOT NULL,"check" TEXT NOT NULL,last_used INTEGER NOT NULL)')
    conn.execute('CREATE TABLE materialized(path TEXT NOT NULL,kind TEXT NOT NULL,instance TEXT NOT NULL,'
                 'built_from TEXT NOT NULL,last_used INTEGER NOT NULL,"check" TEXT NOT NULL,'
                 'artifact_kind TEXT,artifact_key TEXT,PRIMARY KEY(path,kind,instance))')
    conn.execute('CREATE INDEX materialized_artifact ON materialized(artifact_kind,artifact_key)')


def _owned(conn: _sqlite_available.Connection, existed: bool) -> bool:
    application_id = conn.execute("PRAGMA application_id").fetchone()[0]
    if application_id == APPLICATION_ID:
        schema = conn.execute("SELECT value FROM cache_meta WHERE name=?",
                              ("schema",)).fetchone()
        generation = conn.execute("SELECT value FROM cache_meta WHERE name=?",
                                  ("purge_generation",)).fetchone()
        if schema == (str(SCHEMA),) and generation is not None:
            return True
        _warn_once("schema")
        return False
    if (application_id == 0 and not existed
            and conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
            .fetchone() is None):
        _initialise(conn)
        return True
    _warn_once("ownership")
    return False


def _recovery_lock(root: Path, path: Path) -> int | None:
    return proclock.acquire(proclock.lock_path(root, "compiled-recovery", _cache_filename(path)), proclock.NO_WAIT)


def _discard_aside(root: Path, path: Path) -> None:
    aside = Path(str(path) + ".corrupt")
    if not aside.exists():
        return
    # Caller holds the operation/recovery exclusion throughout this unlink.
    if _location(root, path.name.split("-v", 1)[0]) == path:
        _erase_recognized_aside(aside)


def _corruption(error: BaseException) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    return (_sqlite is not None and type(code) is int
            and code & 0xFF in (_sqlite.SQLITE_CORRUPT, _sqlite.SQLITE_NOTADB))


def _still_corrupt(path: Path) -> bool:
    # An earlier error can outlive another backend's repair. Probe again
    # while holding the recovery lock, before renaming any current inode.
    if _sqlite is None:
        return False
    conn = None
    try:
        conn = _sqlite.connect(path, timeout=0.005, check_same_thread=False, isolation_level=None)
        conn.setlimit(_sqlite.SQLITE_LIMIT_LENGTH, 2 * MAX_PAYLOAD_BYTES)
        return conn.execute("PRAGMA quick_check(1)").fetchone() != ("ok",)
    except _CACHE_ERRORS as error:
        return _corruption(error)
    finally:
        if conn is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                conn.close()


def _replace_corrupt_locked(root: Path, path: Path) -> _Session | None:
    _close_locked(invalidate=False)
    if (_location(root, path.name.split("-v", 1)[0]) != path
            or not _probe_recognized(path)):
        return None
    # Corruption classification never establishes ownership. An unreadable
    # metadata/header file stays a miss, even at our expected cache filename.
    # A journal can belong to a foreign before-image. An unreadable
    # ownership probe never licenses rolling it back or discarding it.
    if Path(str(path) + "-journal").exists():
        return None
    if not _still_corrupt(path):
        return _open_locked(root, recover=False, clean_aside=False, excluded=True)
    aside = Path(str(path) + ".corrupt")
    if aside.exists():
        return None
    path.replace(aside)
    session = _open_locked(root, recover=False, clean_aside=False, excluded=True)
    if session is not None:
        aside.unlink(missing_ok=True)
    return session


def _recover(root: Path, path: Path, error: BaseException, *, excluded: bool = False) -> _Session | None:
    if not _corruption(error):
        return None
    fd = None
    try:
        if not excluded:
            fd = _recovery_lock(root, path)
            if fd is None:
                return None
        return _replace_corrupt_locked(root, path)
    except _CACHE_ERRORS:
        _warn_once("recovery")
        return None
    finally:
        if fd is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                proclock.release(fd)


def _probe_owned(path: Path) -> bool:
    if _sqlite is None:
        return False
    # immutable bypasses rollback/WAL recovery entirely. mode=ro alone does
    # not: SQLite can still try to recover a foreign hot rollback journal.
    conn = _sqlite.connect(path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True,
                           timeout=0.005, check_same_thread=False, isolation_level=None)
    try:
        conn.setlimit(_sqlite.SQLITE_LIMIT_LENGTH, 2 * MAX_PAYLOAD_BYTES)
        return _owned(conn, True)
    finally:
        conn.close()


def _unborn(path: Path) -> bool:
    return path.stat().st_size == 0 and not Path(str(path) + "-journal").exists()


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _connect_owned(root: Path, path: Path, *, recover: bool, clean_aside: bool) -> _Session | None:
    if _sqlite is None:
        return None
    conn: _sqlite_available.Connection | None = None
    try:
        # A 0-byte file at the owned name with no hot journal beside it is a
        # first initialisation that never committed (a kill, a full disk):
        # it holds nothing to preserve, so it initialises like a missing one
        # rather than reading as foreign forever.
        existed = path.exists() and not _unborn(path)
        if existed and not _probe_owned(path):
            return None
        conn = _sqlite.connect(path, timeout=0.005, check_same_thread=False,
                               isolation_level=None)
        # SQLite enforces this inside its engine, before returning a hostile
        # value to Python. A SQL length predicate alone cannot bound its own
        # internal allocation. Metadata/record overhead gets one extra MiB.
        conn.setlimit(_sqlite.SQLITE_LIMIT_LENGTH, 2 * MAX_PAYLOAD_BYTES)
        if not _owned(conn, existed):
            conn.close()
            return None
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5")
        session = _Session(root, os.getpid(), path, conn, _state.lock,
                           _state.epoch, _clock(), _identity(path))
        _state.session = session
        _renew_lease(session)
        _schedule_idle(session)
        if clean_aside:
            with contextlib.suppress(*_CACHE_ERRORS):
                _discard_aside(root, path)
        return session
    except _CACHE_ERRORS as error:
        if _state.session is not None and _state.session.conn is conn:
            _close_locked(invalidate=False)
        elif conn is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                conn.close()
        _warn_once("database")
        return _recover(root, path, error, excluded=True) if recover else None


@contextlib.contextmanager
def _database(root: Path, *, recover: bool = True, clean_aside: bool = True,
              expected_generation: str | None = None) -> Iterator[_Session | None]:
    # Ordinary SQL and replacement share one NO_WAIT machine-local lock.
    # Idle connections may coexist; before each operation their inode is
    # checked under that lock so a replacement cannot strand active users.
    fd, session = None, None
    try:
        if not (_state.transitions or _on_loop() or locks.campaign_locks_held() or _sqlite is None
                or os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0"):
            device = _device(root)
            path = _location(root, device) if device is not None else None
            if path is not None:
                fd = _recovery_lock(root, path)
                if fd is not None:
                    session = _open_locked(root, recover=recover, clean_aside=clean_aside, excluded=True)
                    if (session is not None and expected_generation is not None
                            and _read_generation(session.conn) != expected_generation):
                        session = None
                    if session is not None and not _synchronize(session):
                        session = None
    except _CACHE_ERRORS:
        # Assignment can precede a failed synchronization: never expose that
        # still-unpurged connection as a usable optional cache session.
        session = None
        _warn_once("exclusion")
    try:
        yield session
    except _CACHE_ERRORS as error:
        if session is not None:
            _failed_sql(session, error)
        raise
    finally:
        if fd is not None:
            with contextlib.suppress(*_CACHE_ERRORS):
                proclock.release(fd)


def _failed_sql(session: _Session, error: BaseException) -> None:
    # Finish failed transactions before releasing the replacement exclusion.
    # Otherwise a recoverer could rename in the exception-to-rollback gap.
    if _corruption(error):
        _close_locked(invalidate=False)
        return
    try:
        session.conn.rollback()
    except _CACHE_ERRORS:
        _close_locked(invalidate=False)


def _open_locked(root: Path, *, recover: bool = True, clean_aside: bool = True,
                 excluded: bool = False) -> _Session | None:
    """Private seam: the connection lock must already be held by the caller."""
    if _state.transitions or _on_loop() or locks.campaign_locks_held() or os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0":
        return None
    if _sqlite is None:
        _warn_once("sqlite_unavailable")
        return None
    if not excluded:
        with _database(root, recover=recover, clean_aside=clean_aside) as session:
            return session
    root = Path(root)
    device = _device(root)
    if device is None:
        return None
    path = _location(root, device)
    if path is None:
        _close_locked()
        return None
    if _state.session is not None:
        same_root = _state.session.root == root and _state.session.path == path
        if same_root and _state.session.identity == _identity(path):
            _renew_lease(_state.session)
            return _state.session
        _close_locked(invalidate=not same_root)
    return _connect_owned(root, path, recover=recover, clean_aside=clean_aside)
