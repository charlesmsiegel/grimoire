"""Process-wide memo for pure derivations of file content, keyed by stat.

Sync sweeps re-hash every entity and actor card on every request; the bytes
almost never change between requests. A (path, mtime_ns, size) signature is
enough to reuse the last result — any write (including one from another
process syncing the store folder) moves mtime, and a missing file yields no
signature at all, so callers fall back to their not-found path.
"""

from __future__ import annotations

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


def signature(*paths: Path) -> tuple | None:
    """Stat signature covering every path; None if any is missing."""
    sig = []
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            return None
        sig.append((str(p), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def memo(kind: str, sig: tuple | None, compute: Callable[[], T],
         *, pool: dict | None = None) -> T:
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
    """
    if sig is None:
        return compute()
    cache = _cache if pool is None else pool
    now = time.time_ns()
    if any(now - mtime_ns < RACY_WINDOW_NS for _, mtime_ns, _ in sig):
        return compute()  # too fresh to trust the signature; compute, don't cache
    key = (kind, sig)
    try:
        return cache[key]  # type: ignore[return-value]
    except KeyError:
        pass
    val = compute()
    while len(cache) >= MAX_ENTRIES:
        try:
            del cache[next(iter(cache))]
        except (StopIteration, KeyError, RuntimeError):
            break
    cache[key] = val
    return val
