"""One Debug row per overview read (roadmap 04-C3a).

The four overview routes -- `GET /worlds`, `GET /campaigns`, `GET /api/shell`
and `GET /todo` -- each open a `readstats` collector for the request and, when
it ends, write one `overview_read` row through `logs.record`, the app's only
log writer: the endpoint, the scope kind (`library` or `campaign`), the wall
milliseconds and the compiled cache's state (`on`, `off`, or `degraded` when at
least one persisted overview kind fell back to its live path).

What it deliberately does not carry: any counter, id, name, path or payload.
Most counters scale with the library (campaign scopes with the campaign count,
bytes hashed with transcript sizes), and the log is a file a user may hand to
someone else -- library-size figures would be a new category of content in it
(spec 04 section 8). The counters stay in-process, for tests and the overview
benchmark harness.

Debug because it fires on every navigation; the level is the user's choice and
the monthly cap still applies. It never raises and never changes an answer:
`logs.record` cannot raise, and the timing and state are read inside a guard.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from ..store import compiled, logs, readstats


def cache_state(counters: readstats.Counters) -> str:
    """`off` when the compiled layer is switched off or unavailable, `degraded`
    when a persisted overview kind took its live path in this request, else
    `on`."""
    if not compiled.enabled():
        return "off"
    return "degraded" if counters.total("overview.live_path.") else "on"


@contextmanager
def overview_read(endpoint: str, scope: str) -> Iterator[readstats.Counters]:
    """Collect this request's read counters and log its one `overview_read`
    row when it ends, whether it answered or raised."""
    started = time.perf_counter()
    with readstats.collect() as counters:
        try:
            yield counters
        finally:
            ms: float | None = None
            state = "unknown"
            try:
                ms = round((time.perf_counter() - started) * 1000, 1)
                state = cache_state(counters)
            except Exception:  # noqa: BLE001 -- instrumentation never fails a read
                pass
            logs.record("debug", "overview", "overview read", kind="overview_read",
                        endpoint=endpoint, scope=scope, ms=ms, cache=state)
