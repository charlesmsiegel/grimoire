"""Which record paths were published inside a collection scope (spec 05-C4).

`store.atomic`'s four publishing functions each call `note(path)` once their
replace or append has succeeded, and that is the whole of the coupling: this
module imports nothing from `grimoire`, reads nothing, stats nothing and knows
nothing about the compiled cache. It reports a fact -- this path was published
-- to whoever asked, and with nobody asking it costs one context-variable read.

Who asks is a *boundary* whose writes should be warmed afterwards: every HTTP
request (`main._WriteSetCollector`), every detached run (`runner._guarded` and
`runner._guarded_thread`) and a script (`cache_sync.collecting()`). Each opens
a `Scope`, and at its end CLOSES it, which freezes what it collected into a
snapshot nobody can change afterwards.

The context variable holds a tuple of scopes, reached by reference: a worker
thread started through `anyio.to_thread.run_sync` or Starlette's
`run_in_threadpool` runs in a copy of the caller's context, so its writes land
in the same scope objects. A raw `threading.Thread` does not copy the context,
so its writes are not collected; that is a stated gap, not an accident.

Closing is what makes a run that outlives its request safe. A detached run
starts holding a copy of the request's context (the run portal schedules it
with `call_soon_threadsafe`, which copies the calling thread's context), so
without isolation every write the run made would land in the request's scope
after that scope was handed off -- from other threads, while a consumer
iterated it. A run opens its scope with `isolated=True`, which REPLACES the
inherited tuple, and a late `note` into a scope already closed adds nothing.

What a path is: `os.fspath(path)` exactly as `atomic` received it. No
`abspath`, which would depend on the process's working directory; every store
path is built from `paths.home()` and is absolute already. The consumer
relativises each entry against the root it recorded, and drops one that is
relative or outside it.
"""

from __future__ import annotations

import contextvars
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager


class Scope:
    """One collection, owned by one boundary.

    A set behind its own small lock, and a `closed` flag. `close` stops it
    accepting and hands back a frozen copy, under the same lock, so the
    snapshot can never change afterwards and can never be iterated while
    another thread adds to it.
    """

    __slots__ = ("_closed", "_lock", "_paths")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: set[str] = set()
        self._closed = False

    def _add(self, path: str) -> None:
        with self._lock:
            if not self._closed:
                self._paths.add(path)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> frozenset[str]:
        """Stop accepting, and return what was collected. Idempotent."""
        with self._lock:
            self._closed = True
            return frozenset(self._paths)


_SCOPES: contextvars.ContextVar[tuple[Scope, ...]] = contextvars.ContextVar("grimoire_writeset", default=())


@contextmanager
def collecting(*, isolated: bool = False) -> Iterator[Scope]:
    """Open a scope for the length of the block.

    `isolated=False` adds it beside every scope already open, for a nested
    scope that really is part of its parent. `isolated=True` replaces the
    inherited ones, for a boundary whose lifetime is not its parent's (a
    detached run, a script). The scope is NOT closed on exit: the boundary
    closes it when it hands the snapshot over, which may be later.
    """
    scope = Scope()
    token = _SCOPES.set((scope,) if isolated else (*_SCOPES.get(), scope))
    try:
        yield scope
    finally:
        _SCOPES.reset(token)


def note(path: str | os.PathLike[str]) -> None:
    """Record that `path` was published, into every open scope.

    A no-op when none is open. Never raises for a path it was handed: it is
    called after a successful write, and must not turn one into a failure.
    """
    scopes = _SCOPES.get()
    if not scopes:
        return
    try:
        text = os.fspath(path)
    except TypeError:
        return
    if not isinstance(text, str):
        return
    for scope in scopes:
        scope._add(text)
