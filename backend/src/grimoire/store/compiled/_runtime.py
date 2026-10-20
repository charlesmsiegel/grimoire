"""The single process owner: session, build, close, idle and fork lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import threading
import time
from _thread import LockType
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ... import compiled_build, template_paths
from .. import locks, logs, maintenance_reports, proclock
from . import _contracts, _sqlite_support

_clock = time.monotonic
_timer = threading.Timer


@dataclass
class _Session:
    root: Path
    pid: int
    path: Path
    conn: _sqlite_support._sqlite_available.Connection
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
    controls: dict[Path, tuple[tuple, str, bool]] = field(default_factory=dict)
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
    if _sqlite_support._sqlite is not None and os.environ.get("GRIMOIRE_COMPILED_CACHE") != "0":
        # Silently: a warning is a log row, and a log row would create the
        # store at startup. A missing BUILD is reported at the first use.
        _fingerprint()


def _fingerprint() -> None:
    _process_state()
    if not _state.build_checked:
        _state.build_checked = True
        try:
            stamp = os.environ.get("GRIMOIRE_COMPILED_BUILD")
            _state.build = compiled_build.fingerprint(
                Path(compiled_build.__file__).parent, template_paths.templates_dir(),
                stamp_path=Path(stamp) if stamp else None, require_stamp=proclock.on_android())
        except _sqlite_support._CACHE_ERRORS:
            _state.build = None


def build_fingerprint() -> str | None:
    """BUILD, for a consumer whose own content key must move with it -- a
    derived artifact another spec keys statistics by (`store/searchdocs`'s
    document key, which 09 keys term statistics by). None when it cannot be
    computed. Silent, unlike a cache use: a key is not a cache use."""
    _fingerprint()
    return _state.build


def _build() -> str | None:
    _fingerprint()
    if _state.build is None:
        _warn_once("build")
    return _state.build


def enabled() -> bool:
    """Whether this process may use the compiled layer at all: SQLite is
    importable and the kill switch (`GRIMOIRE_COMPILED_CACHE=0`) is not set.
    A True answer still lets any single request miss (busy, locked, corrupt,
    on the loop, under a campaign lock); this only says the layer is not off.
    Reads no file and never raises."""
    return _sqlite_support._sqlite is not None and os.environ.get("GRIMOIRE_COMPILED_CACHE") != "0"


def available() -> bool:
    """Whether a batch opened now could use the layer at all: `enabled`, not
    on the event loop, not under a campaign lock, and BUILD known. A True
    answer still lets one lookup miss (busy, locked, corrupt). Never raises."""
    try:
        return _available()
    except _sqlite_support._CACHE_ERRORS:
        return False


def published(root: Path) -> bool:
    """Whether `root` has a compiled store at all. A library without
    `.cache/compiled` has no history and no artifacts, and a reader that
    must not create one (spec 05's dry run) asks this before opening a
    batch, which would."""
    return os.path.isdir(os.path.join(root, '.cache', 'compiled'))


def _available() -> bool:
    _process_state()
    if _on_loop() or locks.campaign_locks_held() or os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0":
        return False
    if _sqlite_support._sqlite is None:
        _warn_once("sqlite_unavailable")
        return False
    return _build() is not None


def unavailable_reason() -> str | None:
    """Why the cache is off for this caller, or None when it is on.

    For a report that must say so (spec 05 section 9, `cache: off` with this
    module's reason) rather than a reader, which simply misses. The same tests
    as `_available`, in the same order, each named. Never raises."""
    try:
        _process_state()
        if os.environ.get("GRIMOIRE_COMPILED_CACHE") == "0":
            return "disabled"
        if _sqlite_support._sqlite is None:
            return "sqlite_unavailable"
        if _on_loop():
            return "event_loop"
        if locks.campaign_locks_held():
            return "campaign_lock_held"
        if _build() is None:
            return "build_unavailable"
    except _sqlite_support._CACHE_ERRORS:
        return "unavailable"
    return None


def _warn_once(failure_kind: str) -> None:
    _process_state()
    if failure_kind in _state.warned:
        return
    _state.warned.add(failure_kind)
    # Degradation reporting cannot turn a disposable memo into a failed
    # caller. Exception text and paths deliberately never reach this sink.
    with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
        logs.record("warning", "store.compiled", "Compiled cache unavailable",
                    failure_kind=failure_kind)


def _close_inherited_intents() -> None:
    # Child descriptors share their parent's open-file lock. Close the child
    # reference without issuing an unlock that could abandon the live parent.
    for intent in tuple(_state.intents.values()):
        if intent.descriptor is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                os.close(intent.descriptor)
            intent.descriptor = None


def _device(root: Path) -> str | None:
    if _state.device_unavailable:
        return None
    if root not in _state.devices:
        try:
            key = maintenance_reports.device_key(root)
            if not isinstance(key, str) or not _contracts._DEVICE_KEY.fullmatch(key):
                raise ValueError("device identity unavailable")
            _state.owners[root] = key
            _state.devices[root] = key[:32]
        except _sqlite_support._CACHE_ERRORS:
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
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                session.timer.cancel()
        try:
            session.conn.close()
        except _sqlite_support._CACHE_ERRORS:
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


def _schedule_idle(session: _Session, delay: float = _contracts.IDLE_SECONDS) -> None:
    timer = _timer(delay, lambda: _idle(session))
    timer.daemon = True
    session.timer = timer
    timer.start()


def _idle(session: _Session) -> None:
    if os.getpid() != session.pid:
        return
    # A session the current state does not hold is not this timer's to touch,
    # and neither is that state's exclusion: taking it even briefly refuses
    # the non-blocking fence read a batch starts with, and drops its put.
    # Sessions are only ever built new, so one that is not current never will
    # be again; the unlocked read turns away only what the locked check would.
    if _state.session is not session:
        return
    with _state.lock:
        if _state.session is not session:
            return
        remaining = _contracts.IDLE_SECONDS - (_clock() - session.last_used)
        if remaining > 0:
            try:
                _schedule_idle(session, remaining)
            except _sqlite_support._CACHE_ERRORS:
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
