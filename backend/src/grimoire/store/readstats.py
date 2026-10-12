"""Request-scoped read counters (roadmap 04-C3a).

A leaf: it imports nothing from `store`, so `statcache`, `frontmatter` and the
transcript parser can bump it and stay leaves themselves. Nothing here is
persisted or logged. The counters exist for tests and the overview benchmark
harness (`scripts/bench_overview.py`), which assert on their names; the one
production consumer, the overview routes' Debug `overview_read` row, records
none of them (most of them scale with the library, and the log is a file users
share -- spec 04 section 8).

`bump` with no collector open costs a `ContextVar.get` and a `None` test, which
is why it may sit on every parse.

Names are stable because tests assert on them:

- `statcache.hit.<kind>` / `statcache.miss.<kind>` -- `memo`, `memo_stamped`
- `frontmatter.parse`, `frontmatter.parse_head`
- `transcript.parse` -- `scenes.serialize._parse_messages`
- `compiled.hit.<kind>`, `compiled.miss.<kind>`, `compiled.store.<kind>`,
  `compiled.bypass`, `compiled.sources_hit`, `compiled.sources_miss`,
  `compiled.bytes_hashed`, `compiled.collection_digest`
- `overview.computed.<kind>`, `overview.live_path.<kind>`,
  `overview.unreadable_scene`
- `todo.scope.library`, `todo.scope.campaign`, `todo.chore.<id>`
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar


class Counters:
    """One collector's counts. Thread-safe: a request's threadpool workers and
    the threads they hand work to share the set their context carries."""

    __slots__ = ("_counts", "_lock")

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def add(self, name: str, n: int) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + n

    def merge(self, other: Counters) -> None:
        for name, n in other.snapshot().items():
            self.add(name, n)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def get(self, name: str) -> int:
        with self._lock:
            return self._counts.get(name, 0)

    def total(self, prefix: str) -> int:
        """The sum of every counter whose name starts with `prefix`."""
        with self._lock:
            return sum(n for name, n in self._counts.items() if name.startswith(prefix))


_current: ContextVar[Counters | None] = ContextVar("grimoire_readstats", default=None)


@contextmanager
def collect() -> Iterator[Counters]:
    """Open a counter set for this context. A nested `collect` counts into its
    own set and, on exit, adds what it counted to the enclosing one, so an
    outer collector always holds everything its context did."""
    counters = Counters()
    parent = _current.get()
    token = _current.set(counters)
    try:
        yield counters
    finally:
        _current.reset(token)
        if parent is not None:
            parent.merge(counters)


def bump(name: str, n: int = 1) -> None:
    """Count `n` against `name` in the open set; a no-op when none is open.
    Never raises."""
    counters = _current.get()
    if counters is not None:
        with suppress(Exception):  # instrumentation never fails a read
            counters.add(name, n)


def active() -> bool:
    """Whether a collector is open in this context."""
    return _current.get() is not None


def snapshot() -> dict[str, int]:
    """The open set's counts, or `{}` when none is open."""
    counters = _current.get()
    return counters.snapshot() if counters is not None else {}
