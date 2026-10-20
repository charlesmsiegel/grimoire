"""Attempt stamps, bounded local counts, and guarded outcome observation."""

from __future__ import annotations

import asyncio
import functools
import queue
import threading
from concurrent.futures import Executor, Future

from .. import llm_capture, llm_reasoning, llm_usage, tool_calls, wire
from ..llm_errors import LLMError
from . import _routes
from ._routes import _Route as _Route

# The bound on counting a reply locally when its provider reported no counts
# (spec 9.1; `_estimate`). A warm count is far below it. A cold count is an
# encoder download, and the end of the reply must not wait on that. A count
# that times out still finishes on its worker thread, and the loader memoizes
# the encoder it fetched, so the next call's count is warm.
COUNT_TIMEOUT_S = 5.0

class _DaemonExecutor(Executor):
    """One daemon thread running what is submitted, in order.

    A `ThreadPoolExecutor` cannot be this: its workers are joined at
    interpreter exit, so a count parked behind an encoder download with no
    timeout of its own would hold the process open. A count is worth nothing
    once the process is going, so its thread is a daemon. A future cancelled
    before its turn (`asyncio.wait_for` cancels it at `COUNT_TIMEOUT_S`) is
    skipped, never run."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        self._queue.put((future, fn, args, kwargs))
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self._name,
                                                daemon=True)
                self._thread.start()
        return future

    def _run(self) -> None:
        while True:
            future, fn, args, kwargs = self._queue.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - handed to the waiter
                future.set_exception(exc)
            else:
                future.set_result(result)


@functools.cache
def _count_executor() -> Executor:
    """The one thread local counts run on, made on first use.

    Its own, never asyncio's default executor: the encoder loader holds its
    lock across a download that has no timeout of its own, and a count parked
    behind it must not occupy a default-executor worker that `_parts_lowered`'s
    picture loads and httpx's DNS lookups need. One worker, so a hung download
    parks one thread; the counts queued behind it are cancelled unstarted when
    their `COUNT_TIMEOUT_S` runs out. A daemon (`_DaemonExecutor`), so that
    thread never holds up process exit."""
    return _DaemonExecutor("grimoire-count")


class _Stall:
    """Whether counting is failing, so a stall logs one warning rather than one
    per call: while an encoder load is stuck or broken, every reply whose
    provider reported no counts fails its count the same way. A count that
    works ends the stall, and the next failure is news again."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stalled = False

    def failed(self) -> bool:
        """Record a failure; whether it is the first of its stall."""
        with self._lock:
            first, self._stalled = not self._stalled, True
            return first

    def worked(self) -> None:
        with self._lock:
            self._stalled = False


_count_stall = _Stall()


def _stamp(usage: dict | None, route: _Route | wire.Target, attempts: int) -> None:
    """Start one attempt's accounting: which route is about to run, and how many
    have been tried (#152).

    **Cleared first**, which is the whole reason this is a function. An attempt
    that reached the provider's usage frame and then died -- a connection
    dropped after generation, which is billed work nobody received -- leaves
    numbers in the holder, and merging those into the attempt that eventually
    answers would report one call as the sum of two. So each attempt starts
    from nothing and the row describes the call that served, with `attempts`
    saying how many it took.

    The limit that follows, stated rather than hidden: a failed attempt's
    provider-side charge is NOT in the ledger. There is no honest place to put
    it -- the tokens belong to no delivered reply -- and `attempts > 1` is the
    marker that some went uncounted. The same trade `_resilient` already
    documents about retries costing money.

    `model` is what the request will really run on (the target's, which is
    `effective_model`'s), and a provider that reports its own overwrites it: an alias
    resolves to a dated snapshot, and a ledger that says `opus` where the bill
    says `opus-2026-08` cannot be reconciled against an invoice. So the model
    the call ASKED for is kept beside it (`requested_model`): a model's own
    rates are stated under that name, never under a snapshot's.

    What served the attempt is filed from THIS attempt's target
    (`llm_usage.account`): its provider id, the sampler preset it was sent,
    and its account. So a fallback, a degrade sibling or a retry each
    describes itself, and a row that fell back names the fallback.

    Two keys survive the clear: the display reasoning buffer (reset), and the
    run id a `store.usage.Meter` seeded (`llm_capture.RUN_KEY`), which every
    attempt's incoming-response capture names.
    """
    if usage is None:
        return
    if not isinstance(route, _routes._Route):
        # One attempt handed alone, as its target.
        route = _routes._Route(route, 0)
    target = route.target
    reasoning = usage.get(llm_reasoning.KEY)
    run = llm_capture.run_id(usage)
    calls = usage.get(tool_calls.KEY)
    usage.clear()
    if isinstance(reasoning, llm_reasoning.Buffer):
        reasoning.begin()
        usage[llm_reasoning.KEY] = reasoning
    if run:
        # The caller's run (01g-C3) is the call's, not the attempt's: every
        # attempt's capture names it.
        usage[llm_capture.RUN_KEY] = run
    # A caller's tool-call collector (01g) rides every attempt like the
    # reasoning buffer, emptied so a retry starts with no calls of the
    # attempt that failed. Only one the caller installed: none is added.
    if isinstance(calls, tool_calls.Collector):
        # Told which attempt it is, so the opaque state it keeps is echoed
        # back only to the provider and model that wrote it.
        calls.begin(kind=target.kind, provider_id=target.provider_id, model=target.model)
        usage[tool_calls.KEY] = calls
    usage.update({"model": target.model, "connection": _routes._label(target),
                  "provider": target.kind, "attempts": attempts,
                  # Which attempt is live, for the route that may have to
                  # report an outcome this facade never sees. See `ATTEMPTED`.
                  _routes.ATTEMPTED: target})
    usage["requested_model"] = target.requested_model or target.model
    llm_usage.account(usage, target)


async def _estimate(usage: dict | None, conn: wire.Target, counter) -> None:
    """Mark the attempt as ended on its own, and count what its provider did
    not (spec 9.1, ruling 14).

    Reached only after an attempt's stream ran out by itself -- never on an
    early break, a close or a failure, which nobody can say what was billed
    for. Counted on the counting thread (`_count_executor`), because a counter
    can start an encoder download, and bounded by `COUNT_TIMEOUT_S` so the end
    of the reply never waits on one. Only the half nobody reported is counted.
    A counter that raises or overruns costs the estimate and never fails the
    reply, which has already been delivered; it logs one warning per stall
    (`_count_stall`), not one per call."""
    if usage is None:
        return
    usage[llm_usage.ENDED_KEY] = True
    if counter is None:
        return
    try:
        estimate = usage.get(llm_usage.ESTIMATE_KEY)
        if not isinstance(estimate, llm_usage.Estimate):
            return
        need_prompt = llm_usage.tokens(usage.get("prompt_tokens")) is None
        need_completion = (usage.get("operation") != "embed"
                           and llm_usage.tokens(usage.get("completion_tokens")) is None)
        if not (need_prompt or need_completion):
            return
        work = functools.partial(estimate.count, counter, prompt=need_prompt,
                                 completion=need_completion)
        prompt, completion = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(_count_executor(), work),
            COUNT_TIMEOUT_S)
        llm_usage.fill(usage, prompt, completion)
        _count_stall.worked()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        if _count_stall.failed():
            # The type only: a counter's message could quote the text it counted.
            _routes.log.warning("could not count the tokens %r did not report: %s "
                        "(said once until a count works again)",
                        _routes._label(conn), type(exc).__name__)


def _observe(observer, conn: wire.Target, error: LLMError | None) -> None:
    """Report one attempt's outcome, without letting the report break the call.

    The observer is how a provider's own verdict reaches the health registry
    without anything polling for it (#146): every real turn is already a live
    test of the connection it ran on, and the failures worth showing in the
    status bar are exactly the ones a scene just hit.

    Guarded because it is bookkeeping on the generation path. An observer that
    raises would turn a *successful* turn into an error — the one outcome a
    status feature must never be able to produce — and, on the failure path,
    would replace the provider's error with its own.
    """
    if observer is None:
        return
    try:
        observer(conn, error)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        _routes.log.warning("could not record connection health for %r: %s", _routes._label(conn), exc)
