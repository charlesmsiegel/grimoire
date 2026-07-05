"""Process-wide memo for pure derivations of file content, keyed by stat.

Sync sweeps re-hash every entity and actor card on every request; the bytes
almost never change between requests. A (path, mtime_ns, size) signature is
enough to reuse the last result — any write (including one from another
process syncing the store folder) moves mtime, and a missing file yields no
signature at all, so callers fall back to their not-found path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

MAX_ENTRIES = 4096
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


def memo(kind: str, sig: tuple | None, compute: Callable[[], T]) -> T:
    """Return the cached value for (kind, sig), computing on miss. A None
    signature (file vanished between the caller's check and here) is never
    cached. Eviction is FIFO; races under the threadpool at worst recompute."""
    if sig is None:
        return compute()
    key = (kind, sig)
    try:
        return _cache[key]  # type: ignore[return-value]
    except KeyError:
        pass
    val = compute()
    while len(_cache) >= MAX_ENTRIES:
        try:
            del _cache[next(iter(_cache))]
        except (StopIteration, KeyError, RuntimeError):
            break
    _cache[key] = val
    return val
