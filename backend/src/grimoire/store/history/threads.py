"""History's own worker threads, apart from the shared pool (09-S3).

A `def` turn route reaches the history phase through the portal and blocks
its worker -- holding one of the shared pool's tokens -- until the phase
returns. Were the phase's own file stages to need tokens from that same pool,
enough concurrent turns would each hold one while waiting for another, and
none would ever be granted. So every thread hop the phase makes runs under
this limiter instead: one per event loop (a `RunVar`, since a test runs a loop
per app), small, because a phase's stages run one after another and a turn
waits on at most one at a time.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

import anyio
from anyio.lowlevel import RunVar

#: Concurrent history file stages across every turn on one loop. The stages
#: are disk reads and arithmetic, so a few threads serve every turn in flight;
#: tuned later against the long suite's latency column if it ever queues.
HISTORY_THREADS = 4

_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("history_threads")
T = TypeVar("T")


def limiter() -> anyio.CapacityLimiter:
    try:
        return _LIMITER.get()
    except LookupError:
        made = anyio.CapacityLimiter(HISTORY_THREADS)
        _LIMITER.set(made)
        return made


async def run(fn: Callable[..., T], *args: Any, abandon_on_cancel: bool = False) -> T:
    """`fn(*args)` on a history worker thread."""
    return await anyio.to_thread.run_sync(partial(fn, *args), abandon_on_cancel=abandon_on_cancel,
                                          limiter=limiter())
