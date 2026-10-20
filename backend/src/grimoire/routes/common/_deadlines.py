"""common: deadlines responsibilities."""

from __future__ import annotations

import logging
import time

from ... import deadline, llm, store
from ... import inference as operations
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store.inference.resolved import ResolvedInference

log = logging.getLogger('grimoire.routes.common')


async def _bounded_call(coro, ceiling: float | None = None, on_timeout=None):
    """Await one non-streaming generation under a total-duration ceiling (#272).

    The facade's own bound is an *idle* one -- the gap between deltas -- which is
    the right shape for streamed prose (cutting a healthy long generation off
    mid-sentence is worse than letting it finish) but leaves an upstream that
    emits a frame every `llm_timeout - 1` seconds holding its request forever.
    The one-shot generation routes have no partial output to protect: nothing is
    visible until the call returns, and a truncated one costs only a retry. So
    they get a stopwatch, and `stream` deliberately does not.

    Absorb is not routed through here. It carries `_Budget`, which bounds a whole
    *sequence* and knows which of its steps are droppable -- and whose `0` means
    "no ceiling at all, however long the calls take". Folding this ceiling into
    the facade would silently narrow that escape hatch for every absorb step, so
    the ceiling stays where the policy is: the routes that opt into it.

    An overrun is raised as the same `LLMError("timeout", ...)` an upstream stall
    already raises, so every caller's existing `except LLMError` covers it with
    no new branch -- and it reaches the client as the 504 that kind maps to
    (#213), which is what it is. `llm_call_budget <= 0` disables the ceiling.

    `ceiling` overrides that setting for a caller whose wait is not a
    generation and must not inherit the "no ceiling at all" escape hatch --
    the health check (#146), which is a button somebody is watching rather
    than a model they are waiting on.

    `on_timeout` is called with the overrun error before it is raised, for the
    caller that knows which connection was being held. `_noting` builds one.

    `asyncio.wait_for` is deliberately NOT used, for the reason `llm._settle`
    spells out: it cancels the call and then waits for that cancellation to
    finish, so the ceiling is only as hard as the unwinding underneath it. Here
    that unwinding is `_guard`'s `finally`, which grants the pull `_CLOSE_TIMEOUT`
    to settle and the provider another to close -- so a stalled upstream can
    hold the request ~10s past a ceiling that promised to give up at `seconds`,
    and a client that swallows cancellation holds it for good. Waiting is
    therefore capped here and the cancelled call is left to unwind on its own.
    A detached task is a leak we can live with; a wedged request is not.
    """
    seconds = store.config.llm_call_budget() if ceiling is None else ceiling
    if seconds <= 0:
        return await coro

    def overrun(limit: float) -> LLMError:
        error = LLMError(
            "timeout", f"the reply did not finish within {limit:g}s — giving up")
        # The one failure the facade cannot see for itself: cancelling the call
        # unwinds `_resilient` through `GeneratorExit`, not through its
        # `except LLMError`, so without this the connection that just held a
        # request past its ceiling keeps whatever verdict it had — a green dot
        # over a 504 (#146). Only the holder of the ceiling can tell this from
        # a caller who simply walked away, which is why the facade does not try.
        if on_timeout is not None:
            on_timeout(error)
        return error

    try:
        return await deadline.bounded(coro, seconds, overrun)
    except TimeoutError as exc:
        # asyncio.TimeoutError IS the builtin TimeoutError from 3.11 on, so an
        # upstream that gives up on its own lands in the same handler as an
        # expired ceiling. It keeps its own message: blaming a setting that had
        # nothing to do with it would send the user to tune the wrong knob.
        raise LLMError("timeout", str(exc) or "the call timed out") from exc


#: Seconds one pre-generation play decision (roadmap 02 §3.4: a turn intent,
#: a turn plan, a tool decision) may hold the contribution it shapes, across
#: every call it makes -- the primary, each prompt-only re-send of an attempt
#: that refused the schema, and a fallback stage behind a native one. Argued
#: structurally: the player is waiting on a stream that has not started, and
#: an optional decision must cost less waiting than the reply it shapes. Well
#: under `llm_call_budget` (default 300 s), which bounds one whole
#: non-streaming generation. To be tuned against 01a's latency reports, never
#: against a real library.
PLAN_CEILING_S = 20.0

#: The clock a play decision's deadline reads: a seam for tests, which move it
#: to prove a second call is refused unsent once the deadline has passed.
_plan_clock = time.monotonic


class DeadlineRefused(LLMError):  # noqa: N818 - the spec's name, beside absorb's BudgetRefused
    """A play decision's deadline was already gone, so the call was never
    issued (roadmap 02 §3.4).

    `store.usage.NOT_A_FAILURE` is False, as absorb's `BudgetRefused` declares
    it, so `inference._refused_unsent` ends the decide chain here rather than
    trying a later stage, and nothing reaches the error store or the
    connection's health: the clock is the app's own, not the provider's."""

    llm_call_failed = False


def plan_ceiling() -> float:
    """`PLAN_CEILING_S`, or the user's `llm_call_budget` when that is lower
    (`<= 0` is "no ceiling" there, which never lifts this one)."""
    budget = store.config.llm_call_budget()
    return PLAN_CEILING_S if budget <= 0 else min(PLAN_CEILING_S, budget)


def plan_deadline(ceiling: float | None = None) -> operations.Around:
    """One monotonic deadline for ONE play decision, as `decide`'s `around`.

    Each facade call is bounded by what is left of it (`_bounded_call` with
    that remainder as its ceiling); a call that would start with nothing left
    is refused unsent with `DeadlineRefused`. An overrun of a call that did go
    out is the call's own `timeout`, filed by its meter -- but never against
    the connection (`_noting` is deliberately absent): this deadline is the
    app's constant, not the user's `llm_call_budget`, so a slow and healthy
    reasoning model must not read as failing on the Models page because of
    it. Built per decision: the clock starts here. `ceiling` is
    `plan_ceiling()` read by a caller off the event loop (it reads the
    store); None reads it here, for a caller already off the loop."""
    end = _plan_clock() + (plan_ceiling() if ceiling is None else ceiling)

    async def bound(call, _holder):
        left = end - _plan_clock()
        if left <= 0:
            close = getattr(call, "close", None)
            if close is not None:
                close()   # a coroutine never awaited would warn at collection
            raise DeadlineRefused(
                "timeout", "the play decision's deadline passed before this call")
        return await _bounded_call(call, ceiling=left)
    return bound


def _noting(client: LLMClient, resolved: ResolvedInference, usage: dict | None = None):
    """`on_timeout` for a bounded generation: file the overrun against the
    connection that was actually running when the ceiling fired (#146).

    A function rather than a lambda at each call site, because the thing worth
    reading at those sites is the generation, not the bookkeeping.

    `resolved` is the route's resolution and `usage` is the holder the facade
    stamps per attempt, which is the more accurate of the two: a generation
    that failed over is being served by the *fallback* by the time it overruns,
    and blaming the primary would both overwrite its real failure with a
    timeout it did not cause and leave the connection that did cause one
    looking healthy. The resolution's primary attempt
    (`inference.note_outcome`) is the fallback for a caller that threads no
    holder, or whose holder no attempt has stamped yet.
    """
    def note(exc: LLMError) -> None:
        # Guarded for the same reason `llm._observe` is, and it took a broken
        # test suite to prove the point: this runs on the failure path, and an
        # exception here does not merely lose a status update — it REPLACES the
        # error the caller is about to raise. A gateway double with no
        # `note_outcome` turned "the budget stopped this phase" into an
        # AttributeError, and the phase stopped reporting why it died.
        try:
            attempted = (usage or {}).get(llm.ATTEMPTED)
            if attempted:
                client.note_outcome(attempted, exc)
            else:
                operations.note_outcome(client, resolved, exc)
        except Exception as exc2:  # noqa: BLE001 - see above
            log.warning("could not record a ceiling timeout: %s", exc2)

    return note
