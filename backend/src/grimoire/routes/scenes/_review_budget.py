"""One monotonic budget bounds concurrent review phases without conflating refusal and failure."""

from __future__ import annotations

import asyncio
import time

from ... import store
from ...llm_errors import LLMError
from ._router import router as router

# Indirection so tests can drive budget arithmetic off a fake clock instead of
# real waiting. Deliberately NOT time.time(): a wall-clock jump (NTP, DST,
# sleep/wake) must not expire or extend a running absorb.
_clock = time.monotonic
BUDGET_EXHAUSTED = "absorb time budget exhausted"


class Abandoned(Exception):
    """Nobody is waiting for this work any more, so it stopped.

    Not an LLMError: no call failed and no clock ran out. The phases that catch
    it turn it into their own "the review was closed" status, and both scoped
    retries hand that back to a client that has already gone -- the value is in
    stopping, not in what is returned.
    """

    #: `store.usage.NOT_A_FAILURE`: a reviewer walking away is not a provider
    #: failing them, so this must not reach the error store (#156).
    llm_call_failed = False


ABANDON_POLL = 0.5

# What a review says about itself once the reviewer has dismissed it. The
# vestigial `_ABANDONED_DOSSIERS`/`_ABANDONED_AUDIT` bodies that used to sit
# here went with the socket: a scoped retry no longer answers down a connection
# nobody is holding, so there is no body to invent -- the run is marked
# `cancelled` and this is what its error says.
_ABANDONED_REASON = "the review this was for was closed before it finished"


async def _watched(coro, abandoned, poll: float = ABANDON_POLL):
    """Await `coro`, giving up on it if the caller goes away while it runs.

    A client disconnect does NOT cancel a plain endpoint -- uvicorn runs it to
    completion -- so without this nothing notices. Checking only *between* calls
    is not enough either, which is the gap this closes: a single wedged call
    (the audit is one call; the first NPC of a dossier run is another) holds the
    request for as long as the provider keeps dribbling inside the idle bound,
    and `absorb_budget = 0` means no deadline ever ends it. Cancel is exactly
    what the panel offers as the way out of that, so the call is raced against
    the check rather than merely bracketed by it.

    The abandoned call is cancelled and detached, not awaited: `llm._settle`'s
    reason -- waiting for the cancellation you asked for hands the unwinding the
    very control you were taking back.
    """
    if abandoned is None:
        return await coro
    task = asyncio.ensure_future(coro)

    def detach():
        task.cancel()
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll)
            if done:
                return task.result()
            if await abandoned():
                detach()
                raise Abandoned
    except asyncio.CancelledError:
        # The REQUEST was cancelled -- a graceful-shutdown deadline, or a server
        # that does cancel handlers on disconnect. `asyncio.wait` re-raises that
        # straight through here, and the child is a separately scheduled task
        # that nothing else holds: without this it keeps generating, outliving
        # the request whose cost this helper exists to bound. Same condition
        # `_bounded_call` covers, same treatment.
        detach()
        raise


def _phase_or_raise(result):
    """One phase's `(edits, block)` pair, or its exception re-raised.

    `_stage_dossiers`, `_stage_voice_drift` and `_run_audit` each document that
    they never raise for absorb -- every failure comes back as a status the
    inspector renders. `gather(return_exceptions=True)` would quietly turn a
    breach of that into a tuple-unpacking error somewhere else, so it is
    surfaced here instead, where the traceback still points at the phase.
    """
    if isinstance(result, BaseException):
        raise result
    return result


async def _gather_phases(*coros, limit: int) -> list:
    """Run absorb's phase coroutines concurrently, at most `limit` in flight,
    returning their results POSITIONALLY.

    Positional, so the staged edits are assembled in a fixed order however the
    calls happen to finish -- the review a reviewer reads must not be shuffled
    by which provider replied first, and `test_frozen_campaign` would notice.

    `return_exceptions=True` is not a convenience. A bare `gather` propagates
    the first exception and leaves its siblings RUNNING: orphaned provider
    calls nobody will read and nothing is bounding. `Abandoned` and
    `BudgetRefused` both fly through this code, so that is the ordinary path
    here rather than the exotic one, and the caller unpacks each result and
    decides. Only the extraction's failure is fatal; the other three never
    raise for absorb (each has its own failure boundary) and report a status.

    Each phase still carries its own share of the budget: `_Budget.run` reads
    the remaining time when it is called, and every phase calls it at once, so
    each gets the whole window rather than a slice. Parallel phases do not
    consume one another's wall-clock.
    """
    sem = asyncio.Semaphore(limit)

    async def guarded(coro):
        async with sem:
            return await coro

    tasks = [asyncio.ensure_future(guarded(c)) for c in coros]
    try:
        return await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # The REQUEST was cancelled -- shutdown, or a server that cancels
        # handlers on disconnect. Detach rather than await, for `_watched`'s
        # reason: waiting for the cancellation you asked for hands the
        # unwinding the very control you were taking back.
        for t in tasks:
            t.cancel()
            t.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        raise


class BudgetRefused(LLMError):
    """The budget was already gone, so the call was never issued.

    An LLMError subclass, so every existing `except LLMError` still covers it —
    but a phase that cares can tell "never sent" from "sent, then cancelled".
    Only the first means the step was never attempted, and only the first is a
    step to report as skipped rather than failed."""

    #: `store.usage.NOT_A_FAILURE`. This carries `kind="timeout"` like every
    #: other LLMError, which is exactly why it has to say so out loud: nothing
    #: downstream could otherwise tell a step the clock refused to start from a
    #: provider that really did time out, and the error store would report the
    #: second when it was the first.
    llm_call_failed = False


class _Budget:
    """A wall-clock ceiling on one absorb's whole LLM sequence (#243).

    Absorb awaits an extraction call, then one dossier call per present NPC,
    then one voice-drift call per present NPC that has a voice anchor, then an
    audit call, all inside a single HTTP request — the per-call idle
    timeout in `llm` bounds each *stall*, but nothing bounds the total. This
    does, and it is deliberately absorb's policy rather than the LLM facade's:
    only the caller knows which of its steps are droppable.

    `seconds <= 0` means no ceiling at all (config's escape hatch), in which
    case every method degrades to a plain await.
    """

    def __init__(self, seconds: float):
        self._deadline = None if seconds <= 0 else _clock() + seconds

    def remaining(self) -> float | None:
        return None if self._deadline is None else self._deadline - _clock()

    def spent(self) -> bool:
        left = self.remaining()
        return left is not None and left <= 0

    async def run(self, coro, on_start=None, on_timeout=None):
        """Await `coro` under the remaining budget, reporting an overrun as the
        same LLMError kind an upstream stall raises — so every caller's existing
        LLM failure handling covers it with no new branch.

        `on_start` fires only if the call actually goes out, and `BudgetRefused`
        says it did not. Both exist because this is the only place that can
        decide it: a caller's own `spent()` check is already stale by the time
        the deadline is read below, however few statements sit in between.

        `on_timeout` fires for a call that went out and was then cut off here,
        so the connection it was running on can be told (`common._noting`).

        The timeout waits for the cancellation it requests to complete, so
        the real ceiling is the budget plus however long the call takes to
        unwind; that unwinding is itself hard-bounded in `llm`
        (grace-then-abandon), which keeps this a bound rather than a hope.
        """
        left = self.remaining()
        if left is None:
            if on_start:
                on_start()
            return await coro
        if left <= 0:
            # Handled here rather than left to wait_for, which cancels a task
            # before its first step and so reports a timeout for a request that
            # never left. `close()` retires the coroutine we are not going to
            # await, which would otherwise warn at collection.
            coro.close()
            raise BudgetRefused("timeout", BUDGET_EXHAUSTED)
        # A positive timeout can cancel only after we yield to the call, so
        # the call is issued even if its answer never arrives.
        if on_start:
            on_start()
        timer = asyncio.timeout(left)
        try:
            async with timer:
                return await coro
        except TimeoutError as exc:
            # #73's Windows gate exposed the clock-tick gap: our timer can
            # cancel before _clock() advances past the deadline. Its own
            # expiration proves ownership; an upstream TimeoutError still
            # keeps its reason while the budget has time left.
            expired = timer.expired() or self.spent()
            detail = BUDGET_EXHAUSTED if expired else (str(exc) or "the call timed out")
            overrun = LLMError("timeout", detail)
            # This ceiling has the same blind spot `routes.common._bounded_call`
            # does, and for the same reason (#146): `wait_for` cancels the call
            # from outside the facade, so `_resilient` unwinds through
            # `GeneratorExit` rather than its `except LLMError` and the attempt
            # nobody got an answer from is the one the health registry never
            # hears about. An absorb phase stopped this way would leave the
            # connection reading green.
            #
            # Only here, not on the `BudgetRefused` path above: a step that was
            # never issued is a statement about this absorb's clock, not about
            # the provider.
            if on_timeout is not None:
                on_timeout(overrun)
            raise overrun from exc


def _budget_overrun(exc: BaseException) -> bool:
    """Whether `exc` is this absorb's own clock running out rather than an
    upstream stall.

    `_Budget.run` deliberately reports both as the same LLMError kind so callers
    need no extra branch to *handle* them -- but a phase needs to *say* which
    happened, because only one of them is fixed by a larger budget. The detail
    is the sentinel `_Budget.run` sets, not a substring guess.

    A call whose routes failed composed (`LLMError.words`) is the clock's when
    the clock stopped any one of them. That happens only to `decide`'s
    prompt-only re-sends, each run under this budget on its own after the
    routes refused the structured field: the composed sentence names both
    routes, so it never equals the sentinel, yet a re-send the clock cut off
    or never let go out is a route a larger budget would have let answer."""
    if not isinstance(exc, LLMError):
        return False
    return exc.detail == BUDGET_EXHAUSTED or any(_budget_overrun(w) for w in exc.words)


def _phase_report(dossiers: dict, voice: dict, mechanics: dict,
                  identity_block: dict) -> list[dict]:
    """One row per LLM-backed step of this absorb, in run order -- extraction,
    identity, dossiers, voice, audit: was it attempted, how did it end, and was
    the shared time budget what stopped it (#243/#236 follow-up).

    Without it, a slow-but-healthy extraction that eats the whole budget returns
    an absorb with fewer proposed edits and no way to tell that apart from a
    model that simply had nothing to suggest.

    Each row is *projected* from the block that already reports that step -- the
    `audit` row from `mechanics`, which is that step's block -- so `phases` is a
    uniform view rather than a second source of truth that can drift from what
    the review panel renders beside it.

    Extraction gets no block because it has no partial outcome: `post_absorb`
    raises when it fails, so reaching this call already proves it succeeded.
    The identity row sits right after it because it is chained onto it (see
    `_extract_and_identify`), and it has no retry route: a duplicate check
    that failed leaves its rows staged as new with hints, which the reviewer
    can act on without asking again.

    ("Phase" here means a step of one absorb run; the `Phase 2:`/`Phase 5:`
    comments elsewhere in this file are roadmap milestones, unrelated.)"""
    keys = store.pending_reviews.PHASE_KEYS
    return [{"name": "extraction", "status": "ok", "reason": None,
             "attempted": True, "budget_exhausted": False}] + \
           [{"name": name, **{k: block[k] for k in keys}}
            for name, block in (("identity", identity_block), ("dossiers", dossiers),
                                ("voice", voice), ("audit", mechanics))]
