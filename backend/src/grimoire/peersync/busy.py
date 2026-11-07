"""A library held for a peer: a serving device's bounded wait for its own library (028).

A resolve, and a transfer step that deletes, keep this library locked while they wait for the
peer's answer, so the peer's own server could in turn be waiting for its library held the same
way: two devices doing so against each other would each wait on the other until the connection
timed out. While one is in flight, a served commit waits for this library only ``BUSY_WAIT``.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator

from grimoire.store import LibraryStore

from .errors import PeerRefused

#: How long a serving device waits for its own library before refusing a step as ``busy``, once one
#: of its own resolves (or deleting transfer steps, ``outgoing``) is in flight. That holds the library
#: while it waits for its peer's answer (019 commits the receiver's data with both libraries held),
#: so two devices doing so against each other would each wait on the other until the connection
#: timed out, freezing every local write meanwhile.
BUSY_WAIT = 1.0

_OUTGOING: dict[str, int] = {}
_OUTGOING_LOCK = threading.Lock()


def _key(store: LibraryStore) -> str:
    return os.path.realpath(store.root)


@contextlib.contextmanager
def outgoing(store: LibraryStore) -> Iterator[None]:
    """Mark ``store`` as held for a peer for the block: a resolve against it, or a transfer step
    replayed on it while this library stays locked (this process only: a library is open for
    writing in one process at a time)."""
    key = _key(store)
    with _OUTGOING_LOCK:
        _OUTGOING[key] = _OUTGOING.get(key, 0) + 1
    try:
        yield
    finally:
        with _OUTGOING_LOCK:
            _OUTGOING[key] -= 1
            if not _OUTGOING[key]:
                del _OUTGOING[key]


def resolving_out(store: LibraryStore) -> bool:
    """Whether this process holds ``store`` for a peer right now (``outgoing``): resolving a conflict
    against it, or replaying a deleting transfer step on it."""
    with _OUTGOING_LOCK:
        return _key(store) in _OUTGOING


@contextlib.contextmanager
def committing(store: LibraryStore) -> Iterator[None]:
    """Hold ``store`` for one served commit, or refuse ``busy`` with nothing written.

    Without a resolve or a deleting transfer step of this library in flight the wait is unbounded,
    as before: whoever holds the library is a local commit that will finish. With one, the holder
    may be that resolve or step, waiting on its peer, whose server may in turn be waiting here: after ``BUSY_WAIT`` the step
    is refused, so the cycle ends at once and no local write waits for a connection to time out.
    """
    started = time.monotonic()
    while not store.lock.acquire(timeout=0.05):
        if resolving_out(store) and time.monotonic() - started >= BUSY_WAIT:
            raise PeerRefused("busy", "this library is busy with a peer of its own; try again")
    try:
        yield
    finally:
        store.lock.release()
