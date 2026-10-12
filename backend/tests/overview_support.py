"""Shared scaffolding for spec 04's overview tests.

`audited()` records the files opened and the directories listed while it is
active, through one process-wide `sys.addaudithook` (a hook cannot be removed,
so it is installed once and switched by a flag). `stat` has no audit event, so
stats are not counted -- they are cheap, and the counters stand in for them.

`clock_forward(monkeypatch)` moves statcache's view of "now" far enough ahead
that a file just written is outside `RACY_WINDOW_NS`, so `memo_stamped` stores
what a test computes. POSIX ctime cannot be backdated, so this is the only way
an in-process memo can be exercised without sleeping (spec 04 section 9.1).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace

from grimoire.store import statcache


@dataclass
class Audit:
    opened: list[str] = field(default_factory=list)
    listed: list[str] = field(default_factory=list)

    def opened_under(self, root: os.PathLike | str) -> list[str]:
        prefix = os.fspath(root)
        return [p for p in self.opened if p.startswith(prefix)]

    def listed_under(self, root: os.PathLike | str) -> list[str]:
        prefix = os.fspath(root)
        return [p for p in self.listed if p.startswith(prefix)]


_lock = threading.Lock()
_active: list[Audit] = []
_installed = False


def _hook(event: str, args: tuple) -> None:
    if not _active or event not in ("open", "os.listdir", "os.scandir"):
        return
    try:
        target = args[0]
        if isinstance(target, int) or target is None:
            return
        path = os.fsdecode(os.fspath(target))
    except Exception:  # noqa: BLE001 -- an audit hook must never raise into the read
        return
    with _lock:
        for audit in _active:
            (audit.opened if event == "open" else audit.listed).append(path)


@contextmanager
def audited() -> Iterator[Audit]:
    global _installed  # noqa: PLW0603 -- one process-wide hook, installed once
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True
    audit = Audit()
    with _lock:
        _active.append(audit)
    try:
        yield audit
    finally:
        with _lock:
            _active.remove(audit)


def clock_forward(monkeypatch, seconds: float = 30.0) -> None:
    """Make statcache believe every stamp is older than its racy window."""
    ahead = int(seconds * 1e9)
    monkeypatch.setattr(statcache, "time", SimpleNamespace(time_ns=lambda: time.time_ns() + ahead))


def clear_memos() -> None:
    """Forget every in-process memo the overview reads go through, as a
    process restart would."""
    from grimoire.store import characters
    from grimoire.store.scenes import read as scenes_read
    statcache._cache.clear()
    scenes_read._SCENE_POOL.clear()
    characters._IDENTITY_POOL.clear()
    try:
        from grimoire.store.overview import read as overview_read
    except ImportError:
        return
    overview_read._OVERVIEW_POOL.clear()


def register_overview_kinds(monkeypatch, *names: str) -> None:
    """Register overview candidate kinds in the compiled registry for a test,
    whatever the measured registration in `compiled.KINDS` says, so the
    persisted layer is exercised either way. No names: all four."""
    from grimoire.store import compiled
    chosen = names or tuple(compiled.OVERVIEW_CANDIDATES)
    kinds = dict(compiled.KINDS)
    for name in chosen:
        kinds[name] = compiled.OVERVIEW_CANDIDATES[name]
    monkeypatch.setattr(compiled, "KINDS", kinds)


def unregister_overview_kinds(monkeypatch) -> None:
    from grimoire.store import compiled
    monkeypatch.setattr(compiled, "KINDS", {k: v for k, v in compiled.KINDS.items()
                                            if k not in compiled.OVERVIEW_CANDIDATES})


def cache_clock_forward(monkeypatch, seconds: float = 30.0) -> None:
    """Move the compiled layer's filesystem clock past `PERSIST_WINDOW_NS`,
    so a source just written is old enough to be recorded (spec 03 section 5;
    POSIX ctime cannot be backdated, the `migrations._clock` precedent)."""
    from grimoire.store import compiled
    real = compiled.filesystem_clock

    def ahead(root):
        clock = real(root)
        if clock is None:
            return None
        return compiled.FilesystemClock(clock.now_ns + int(seconds * 1e9), clock.device)
    monkeypatch.setattr(compiled, "filesystem_clock", ahead)


def restart() -> None:
    """What a process restart forgets: the compiled layer's handles and every
    in-process memo. The cache file itself is kept."""
    from grimoire.store import compiled
    compiled.close()
    clear_memos()


@contextmanager
def database_locked(home: os.PathLike | str) -> Iterator[None]:
    """Hold every compiled cache file of `home` under an exclusive SQLite
    transaction from another connection: lookups miss, flushes store nothing."""
    import sqlite3
    from pathlib import Path
    conns = []
    for path in sorted(Path(home, ".cache", "compiled").glob("*.sqlite")):
        conn = sqlite3.connect(path, timeout=0, isolation_level=None)
        conn.execute("BEGIN EXCLUSIVE")
        conns.append(conn)
    try:
        yield
    finally:
        for conn in conns:
            conn.rollback()
            conn.close()
