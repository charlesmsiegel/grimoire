"""A hard wait: await something for at most so long, then stop waiting.

A gateway leaf, standard library only, shared by `routes.common._bounded_call`
(a one-shot generation's ceiling, #272) and the tool loop's per-turn and
per-tool bounds (`inference.run_tools`, spec 01g 3.9), so the two cannot
drift apart.

`asyncio.wait_for` is deliberately NOT used: it cancels the awaited work and
then waits for that cancellation to finish, so a bound is only as hard as the
unwinding underneath it (`llm._settle` spells it out). Here the waiting is
capped and the cancelled work is left to unwind on its own -- a detached task
is a leak a caller can live with; a wedged request is not. A thread a task was
waiting on is not stopped either: it finishes in its own time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


def abandon(task: asyncio.Future) -> None:
    """Ask `task` to stop, then stop waiting on it. Retrieving its exception
    in a callback keeps asyncio from logging it as never retrieved."""
    task.cancel()
    task.add_done_callback(lambda t: None if t.cancelled() else t.exception())


async def bounded(work: Awaitable[T], seconds: float | None,
                  overrun: Callable[[float], BaseException]) -> T:
    """`work`'s result, or -- once `seconds` have passed -- `overrun(seconds)`
    raised, with the work abandoned (`abandon`). `seconds` None is no bound;
    `<= 0` is a wait already spent, so the work is abandoned unstarted and
    `overrun` raised at once. Every caller reads a ceiling of `<= 0` from
    configuration as "off" itself (`_bounded_call` never calls here with
    one; the tool loop's per-turn wait is None then), so a non-positive
    number reaching here is always a remainder computed from a clock that
    has run out -- which must never read as "wait forever". A caller that is
    cancelled while waiting abandons the work too, and the cancellation
    propagates."""
    if seconds is None:
        return await work
    task = asyncio.ensure_future(work)
    if seconds <= 0:
        abandon(task)
        raise overrun(seconds)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
    except asyncio.CancelledError:
        abandon(task)
        raise
    if not done:
        abandon(task)
        raise overrun(seconds)
    return task.result()
