"""Process-wide memo for pure derivations of file content, keyed by stat.

Sync sweeps re-hash every entity and actor card on every request; the bytes
almost never change between requests. A (path, mtime_ns, size, inode)
signature is enough to reuse the last result — any write (including one from
another process syncing the store folder) moves mtime, and the inode detects
rename-replace operations that preserve mtime. Absent-ok signatures accept
missing companion files as a cacheable state, while other callers fall back
when any file is missing.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

MAX_ENTRIES = 4096
# Filesystem timestamps tick coarsely (up to ~15ms on Windows), so a same-size
# rewrite moments after the cached read can leave the signature unchanged. Like
# git's racy-clean handling: never cache a file whose mtime is this recent.
RACY_WINDOW_NS = 1_000_000_000
_cache: dict[tuple, object] = {}


def signature(*paths: Path, absent_ok: bool = False) -> tuple | None:
    """Stat signature covering every path; None if any is missing.

    `st_ino` rides along because a rename-replace cannot preserve it: a sync
    client or restore landing a same-size file with the origin's old mtime
    would otherwise produce an identical signature, and nothing would ever
    invalidate. The residual is an in-place same-size rewrite with a restored
    mtime — the racy-clean residual git also accepts.

    `absent_ok` turns a missing path into the cacheable sentinel
    `(path, "absent")` instead of voiding the signature. It exists for
    companion files whose absence is a valid state (an appearances record a
    campaign has not written yet): the sentinel differs from every real stat
    tuple, so the file's creation invalidates naturally. Callers whose missing
    file means "not found" keep the default.
    """
    sig: list[tuple[str, int, int, int] | tuple[str, str]] = []
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            if absent_ok:
                sig.append((str(p), "absent"))
                continue
            return None
        sig.append((str(p), st.st_mtime_ns, st.st_size, st.st_ino))
    return tuple(sig)


def memo(kind: str, sig: tuple | None, compute: Callable[[], T],
         *, pool: dict | None = None, max_entries: int | None = None) -> T:
    """Return the cached value for (kind, sig), computing on miss. A None
    signature (file vanished between the caller's check and here) is never
    cached. Eviction is FIFO; races under the threadpool at worst recompute.

    `pool` puts the entry in a caller-owned dict with its own MAX_ENTRIES
    budget instead of the shared one. It exists for a caller that touches
    *every* file in the store on a single request: the shared cache is one
    FIFO of 4096, so one such sweep evicts every entity and card hash in it
    and hands the next sync sweep a cold cache — a caller making its own reads
    cheap at the cost of everyone else's. It also keeps the memory that sweep
    holds (whole flattened transcripts, for `store/search.py`) inside a budget
    that can be reasoned about on its own.

    `max_entries` overrides `MAX_ENTRIES` for caller pools.
    """
    if sig is None:
        return compute()
    cache = _cache if pool is None else pool
    now = time.time_ns()
    if any(len(entry) == 4 and now - entry[1] < RACY_WINDOW_NS for entry in sig):
        return compute()  # too fresh to trust the signature; compute, don't cache
    key = (kind, sig)
    try:
        return cache[key]  # type: ignore[return-value]
    except KeyError:
        pass
    val = compute()
    _put(cache, key, val, MAX_ENTRIES if max_entries is None else max_entries)
    return val


def _put(cache: dict, key: object, val: object, budget: int) -> None:
    """FIFO insert under `budget`. Races under the threadpool at worst evict
    one entry too many, which costs a recompute and nothing else."""
    while len(cache) >= budget:
        try:
            del cache[next(iter(cache))]
        except (StopIteration, KeyError, RuntimeError):
            break
    cache[key] = val


#: One `stamp()`: (path, st_mtime_ns, st_ctime_ns, st_size, st_ino).
Stamp = tuple[str, int, int, int, int]


def stamp(path: str | os.PathLike[str]) -> Stamp | None:
    """One path's stamp for `memo_stamped`, or None when it cannot be statted.

    A directory stamps like a file, and that is what makes it useful: every
    way an entry can arrive in or leave a directory -- a create, an unlink, a
    rename, and every atomic write this app makes, which renames a temp over
    its target -- moves the directory's mtime (`migrations._identity_marks_path`
    relies on the same rule). So a directory's stamp vouches for its LISTING,
    and a file's for its bytes.

    Wider than `signature`'s tuple by `st_ctime_ns`. A file rewritten in place
    and then handed its old mtime back -- a sync client or `touch -r` restoring
    the origin's timestamp -- keeps mtime, size and inode, but not ctime, which
    no user tool can set. (On Windows `st_ctime` is the creation time, which
    buys nothing there and costs nothing either.)
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.fspath(path), st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def _unchanged(stamps: tuple[Stamp, ...]) -> bool:
    for s in stamps:
        try:
            st = os.stat(s[0])
        except OSError:
            return False
        if (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino) != s[1:]:
            return False
    return True


def memo_stamped(key: object, compute: Callable[[], tuple[T, tuple[Stamp, ...] | None]],
                 *, pool: dict, max_entries: int) -> T:
    """`memo` for a value whose inputs are only known once it is computed.

    `memo` is handed its signature up front, which needs the caller to know
    which files the value reads before reading any of them. A listing row does
    not: which card files there are is a directory listing, and which image
    folder feeds it is named inside a file. So here `compute` returns the value
    together with the stamps (`stamp()`) of everything it read, and a later call
    re-stats exactly those paths and reuses the value if not one has moved.

    `compute` owes two things for that to be sound, and they are the whole
    contract:

    - Each stamp is taken BEFORE what it vouches for is read -- the directory
      before its listing, the file before its bytes. A write landing between
      the two then shows up as a stamp that no longer matches, and the next
      call recomputes; the other order would store the new stamp beside the
      old content, and nothing would ever invalidate it.
    - A path that was ABSENT is vouched for by its parent directory's stamp,
      since creating it moves that mtime. So an absent file costs no stat here
      at all, which is the point: the stamps are the fewest stats that still
      notice every input changing.

    `compute` returns None for the stamps when something it read cannot be
    vouched for (a file vanished between the listing and the stat, or the
    value came from a state the next read is supposed to repair). The value is
    returned and nothing is stored.

    Nothing touched inside `RACY_WINDOW_NS` before the computation started is
    stored, for `memo`'s reason: a same-size rewrite within one timestamp tick
    of the stat would leave the stamp unchanged. Measured against the start,
    not the end, so a stamp taken late in a long computation is held to the
    same bar as the first one.

    Values are shared between callers: hand out copies of anything mutable.
    """
    entry = pool.get(key)
    if entry is not None and _unchanged(entry[0]):
        return entry[1]
    started = time.time_ns()
    value, stamps = compute()
    if stamps is None or any(s[1] >= started - RACY_WINDOW_NS for s in stamps):
        pool.pop(key, None)
        return value
    _put(pool, key, (stamps, value), max_entries)
    return value
