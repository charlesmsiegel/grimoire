"""Provider-agnostic LLM surface: the dispatch facade over the providers.

The shared error type lives in `llm_errors.py`, not here — see its docstring.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import logging
import queue
import random
import threading
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import Executor, Future
from typing import NamedTuple

from . import (
    adapters,
    content_parts,
    decisions,
    llm_capture,
    llm_errors,
    llm_reasoning,
    llm_sampling,
    llm_usage,
    model_guidance,
    wire,
)
from .anthropic import AnthropicClient
from .claude_agent import ClaudeAgentClient
from .llm_errors import LLMError
from .openai_compatible import OpenAICompatibleClient
from .openrouter import OpenRouterClient

log = logging.getLogger(__name__)

# Fallback for a client constructed with no timeout of its own. The real value
# comes from config.md via the resolver routes injects (#243); this only covers
# callers that inject nothing.
DEFAULT_TIMEOUT = 120.0
# How long the facade waits for the next provider frame before telling the
# caller it is still alive (#95). Chosen well under the idle timeouts proxies
# and load balancers apply to a quiet connection (commonly 30-60s), because the
# gap this covers -- connect plus time-to-first-token, or a model reasoning
# silently -- otherwise looks exactly like a hung stream from outside.
# ``tick <= 0`` disables it.
HEARTBEAT_INTERVAL = 15.0
# Grace period for a provider to unwind. aclose() throws GeneratorExit into it,
# which unwinds httpx's stream context manager — normally instant, but the
# connection being closed is by definition a sick one, so cleanup gets its own
# bound.
_CLOSE_TIMEOUT = 5.0
# What the Claude path runs when its connection names no model. The SDK takes
# an alias, so an unconfigured Claude connection generates perfectly happily --
# unlike the other two kinds, whose empty model reaches the provider as an
# empty model. `wire`'s, which reads it into every target's `model`.
CLAUDE_DEFAULT_MODEL = wire.CLAUDE_DEFAULT_MODEL
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
    behind it must not occupy a default-executor worker that `_lowered`'s
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

# --- retry with backoff, and the fallback route (#144) ---
#: Failure kinds a second attempt could plausibly fix. `rate_limit` and
#: `network` are transient by definition, and both fail *fast* -- a refused
#: connection, a TLS error, a 429 -- which is what makes re-attempting them
#: nearly free.
#:
#: Everything else is deliberately absent, for three different reasons.
#: `auth`/`missing_key`/`missing_dependency` are configuration: retrying is a
#: slower way to show the same error. `bad_response` conflates a 500 from an
#: overloaded provider (worth retrying) with a well-formed 200 whose body the
#: parser could not use (never worth retrying), and the taxonomy cannot yet
#: tell them apart -- retrying the pair would hammer a provider over a reply
#: that will never parse. Splitting the kind is still open: #213 gave every kind
#: an HTTP status and did not need it, because both halves are a 502 either way,
#: so the split has to be argued on retry behaviour alone.
#:
#: `timeout` is the deliberate one. It is transient, and it is still excluded:
#: unlike the others it costs the *whole* `llm_timeout` to detect, so retrying
#: it would silently multiply the one bound the user set explicitly -- a 120s
#: no-reply timeout becoming a six-minute stare at an empty scene, with the
#: setting still reading 120. "Giving up after N seconds" has to keep meaning
#: N seconds. A dead upstream is reported once, promptly; if a fallback route
#: is configured it still gets its turn.
RETRYABLE_KINDS = frozenset({"rate_limit", "network"})
#: Retries *after* the first attempt, for a client constructed with no resolver
#: of its own. The real value comes from config.md via routes, same as the
#: timeout. Two is enough to ride out a blip and few enough that a genuinely
#: down provider still reports quickly.
DEFAULT_RETRIES = 2
#: Backoff schedule, in seconds: attempt *n* waits somewhere in the upper half
#: of `min(RETRY_CAP, RETRY_BASE * 2**n)`. Module constants rather than
#: defaults baked into a signature, because they are the knob tests turn --
#: setting `RETRY_BASE` to 0 makes a retry test instant.
RETRY_BASE = 0.5
RETRY_CAP = 8.0
#: The longest provider-named `Retry-After` worth waiting out, in seconds.
#:
#: A `Retry-After` is not advice, it is the provider saying when it will serve
#: this request — so the backoff schedule above yields to it whenever it is
#: longer, and retrying sooner is just a request guaranteed to be rejected.
#: But a window that runs into minutes is the provider saying it will *not*
#: serve this soon, and sitting on it holds a scene hostage on a promise
#: nothing enforces. Past this line, retrying stops: the fallback route gets
#: its turn immediately, and failing that the user is told about the rate
#: limit now rather than after a wait they did not choose.
RETRY_AFTER_CAP = 30.0

#: HTTP statuses that, answering a request carrying sampler parameters, are read
#: as the PRESET being refused rather than the connection failing. 400 is what
#: OpenRouter and the OpenAI API send for a parameter they reject; 422 is what
#: FastAPI-based servers (vLLM among them) send for a field failing validation.
#: Such an attempt is not handed to the fallback -- see `_resilient`.
PRESET_REFUSAL_STATUSES = frozenset({400, 422})

#: Where a sampler preset came from, for the scopes that are about the ROUTE
#: rather than the connection. A route-level answer follows the route onto
#: whichever connection serves it, the fallback included (`LLMClient._routes`).
ROUTE_SCOPES = frozenset({"campaign", "global"})

#: The key under which a call's connection dict carries the fallback that call
#: may fail over to (spec 5.5): the resolver's own fallback attempt, lowered,
#: attached to the primary's dict by `store.inference.resolve` -- absent when
#: there is none, or when it is known unable to do the job (spec 5.3).
#:
#: The facade never reads it: `LLMClient` is sent a `wire.Chain` (or a lone
#: `wire.Target`) and refuses a dict, so the fallback rides on
#: `wire.Chain.fallback`. The resolver's lowered dicts still carry it, which
#: is where `ResolvedInference.chain` reads whether the fallback rides, and
#: `wire.from_lowered` reads the same key. Deleted with the lowering (Task 10).
FALLBACK_KEY = "_fallback"

#: The key under which a call's connection dict says THIS attempt may be asked
#: for structured output (spec 7.2): `True` on the lowered dict of each attempt
#: of a decide resolution whose `structured_output` is `yes`, set by
#: `store.inference.resolve` and absent everywhere else -- so a generate
#: resolution's dicts are what they were before slice F.
#:
#: The facade reads it per attempt as `wire.Target.structured` (what
#: `wire.from_lowered` and the resolver's targets read from this key), so a
#: fallback without the mode is sent the same prompt (the schema rides in it)
#: and no structured envelope, without the facade importing the store.
STRUCTURED_KEY = "_structured"


def _backoff_delay(attempt: int) -> float:
    """Seconds to wait before retry `attempt` (0-based).

    Exponential, capped, and jittered. This is the schedule for when the
    provider did *not* say when to come back; `_resilient` prefers a
    `Retry-After` over it whenever there is one.

    The jitter covers the case where several callers hit one account's limit
    together and an unjittered schedule has all of them retry at the same
    instant, re-creating the burst that got them rejected. Note what that is
    and is not here: absorb's calls are strictly sequential (extraction, then
    each dossier, each awaited in turn), so they cannot collide with each
    other. The real colliders are two browser tabs generating at once and two
    grimoire installs sharing one API key — thinner than a server's fan-out,
    which is why the jitter is "equal jitter" (half the delay fixed, half
    random) rather than full: enough spread to break a tie, never a wait that
    collapses to nearly nothing.
    """
    ceiling = min(RETRY_CAP, RETRY_BASE * (2 ** attempt))
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def _label(attempt: wire.Target) -> str:
    """How an attempt's connection is named in a log line and the ledger's
    `connection`: what the user called it, or whatever identifies it at all
    for one that has no name."""
    return attempt.provider_name or attempt.provider_id or attempt.kind or "?"


#: Connection kinds whose client cannot carry OpenAI-style content PARTS: the
#: Claude SDK path joins a message's content into one string, so a multimodal
#: message raises deep inside it. The registry's, derived from each adapter's
#: `carries_images`. `store.image_drafts.SUPPORTED_KINDS` states the same rule
#: positively; the ROUTE layer refuses such a connection as the PRIMARY with a
#: message the user can act on, through the `claude` provider preset's `never`
#: (`store.inference.providers`) at the inference seam;
#: `test_image_description_draft.py` pins the two halves to agree.
TEXT_ONLY_KINDS = adapters.TEXT_ONLY_KINDS

#: Connection kinds whose provider can be asked for a model catalog (#149):
#: the registry's, derived from each adapter's `lists_models`.
#:
#: `claude` is absent, and not as an oversight: that path's models are aliases
#: the SDK resolves at request time, with no endpoint to enumerate them, which
#: is why a Claude provider's model is typed (or picked from a fixed list)
#: rather than read off a live catalog. The route reads this to refuse a
#: catalog request the provider cannot serve *before* making one, so the reader gets "this kind has no catalog" instead
#: of a transport error from a URL that was never going to exist.
LISTABLE_KINDS = adapters.LISTABLE_KINDS

#: Key under which `_stamp` records the attempt the *current* one is running
#: on, in the usage holder the caller already threads down: its `Target`.
#:
#: For the one caller that has to file an outcome this facade cannot see — a
#: route whose ceiling cancels the call (`routes.common._noting`). Without it
#: that route can only name the connection it *started* with, and a generation
#: that failed over to the fallback and then overran would be recorded against
#: the primary, overwriting the primary's real failure with a timeout it did
#: not cause and leaving the fallback's health untouched.
#:
#: Safe to put here because `store.usage.Meter` copies named keys out of the
#: holder rather than dumping it, so this never reaches a ledger row.
ATTEMPTED = "_attempted_conn"


def prefill_capable(conn: wire.Target | dict) -> bool:
    """Whether a prompt ending in a partial assistant turn may be sent on `conn`
    as a prefill for the model to continue ("Keep writing", play controls IV).

    The connection's own opt-in and nothing else -- no kind check. Whether a
    trailing assistant message is continued depends on the model behind the
    route, not on the adapter: current Claude models refuse one, and most chat
    templates read it as history and write a fresh reply. So the default is
    the instruction, and the user says where prefill works."""
    if isinstance(conn, wire.Target):
        return conn.prefill
    return conn.get("prefill") is True


def _carries_parts(messages: list[dict]) -> bool:
    """Does any message hold content parts this facade cannot LOWER to text?

    Image references (#377, `content_parts`) are lowered per route -- a route
    that cannot read images is sent their text -- so they are no reason to
    keep a text-only fallback away. An image-description draft's own
    `image_url` parts are."""
    return any(not isinstance(m.get("content", ""), str)
               and not content_parts.lowerable(m["content"]) for m in messages)


#: The route marker for a DEGRADE sibling (#377) on a lowered dict: the same
#: connection, sent the text lowering of a prompt whose images it just
#: refused. The facade marks one with `wire.Target.degrade`, and only
#: `wire.from_lowered` reads this spelling (a test holds the two equal).
DEGRADE = "_degrade"

#: The HTTP statuses that mean "not this request" -- a provider refusing an
#: image for its format, size or content. `bad_response` alone cannot say this:
#: a 500 maps to it too, and re-sending as text on a server error would drop
#: the pictures from a turn a plain retry would have served. 404 because
#: OpenRouter answers "No endpoints found that support image input" with one
#: when no endpoint it may route to takes images; a 404 for a genuinely missing
#: model costs one extra text attempt that fails the same way.
REJECTED_STATUSES = frozenset({400, 404, 413, 415, 422})

#: The statuses a native decisions request answers when the ENDPOINT refused
#: that model, key or request (spec 7.4, ruling 11): `REJECTED_STATUSES` plus
#: 403. OpenRouter's reference documents 403 as "authenticated but insufficient
#: permissions", which is what a key without access to an alpha endpoint gets
#: while it serves every chat call -- so the connection answered, and is not
#: marked failing. A revoked key answers 401 and stays observed (M6).
NATIVE_REJECTED_STATUSES = REJECTED_STATUSES | {403}


class _Route(NamedTuple):
    """One attempt a generation may make, as the pair a route always was:
    the attempt's target and its retry budget (the primary's; 0 for a
    fallback or a degrade sibling). The target is what every callback, the
    `ATTEMPTED` stamp and a schema refusal are handed."""

    target: wire.Target
    retries: int

    def sent(self) -> wire.Target:
        """`target`, less the degrade marker: what an image budget, a tail
        chooser and a schema refusal are handed."""
        return dataclasses.replace(self.target, degrade=False)


def _degraded(route: _Route) -> _Route:
    """`route`'s degrade sibling (#377): the same attempt, sent its images as
    text, with no retries of its own."""
    return _Route(dataclasses.replace(route.target, degrade=True), 0)


def _with_degrades(routes: list[_Route]) -> list[_Route]:
    """Each route followed by its degrade sibling."""
    return [r for route in routes for r in (route, _degraded(route))]


def _chain_of(attempt: wire.Chain | wire.Target) -> wire.Chain:
    """What a generation is sent, as a chain: a `Chain` as it is, and a
    `Target` as a chain of one. Anything else -- a lowered connection dict
    included -- is a `TypeError`: the facade sends typed targets only."""
    if isinstance(attempt, wire.Chain):
        return attempt
    return wire.Chain(_target_of(attempt))


def _target_of(attempt: wire.Target) -> wire.Target:
    """`attempt`, when it is one attempt's `Target`; a `TypeError` for
    anything else, a lowered connection dict included."""
    if isinstance(attempt, wire.Target):
        return attempt
    raise TypeError(f"the facade sends a wire.Chain or wire.Target, not {type(attempt).__name__}")


def _may_send_refs(messages: list[dict]) -> bool:
    """Whether any route could be sent an image reference -- asked of every
    variant a prepared prompt holds, not only the primary's: a fallback packed
    for a larger window can keep a picture the primary's packing gave up, and
    without a sibling its refusal of that picture would end the turn."""
    if isinstance(messages, model_guidance.PreparedMessages):
        return messages.any_variant(content_parts.has_refs)
    return content_parts.has_refs(messages)


def _same_route(a: wire.Target, b: wire.Target) -> bool:
    """Whether two attempts would send the same request to the same place.

    Falling back to the connection that just failed is not a fallback: it is a
    third attempt wearing a different name, and it doubles the time a user
    waits to be told the provider is down. Identity first, then the provider
    id (a `Target` holds dicts, so it is never a key): an empty id never
    matches.
    """
    if a is b:
        return True
    return bool(a.provider_id) and a.provider_id == b.provider_id


def effective_model(conn: wire.Target | dict) -> str:
    """The model a generation on `conn` will actually run on.

    Only the Claude path substitutes anything, so this differs from
    ``conn["model"]`` for exactly one kind -- but it is the difference between
    telling the reader "no model" and naming the one about to answer them.
    Both the dispatcher and the config route read the answer from here so the
    status bar cannot drift from what generation does. A target's `model` is
    already the one it runs (`wire.from_lowered` reads a dict by this rule).
    """
    if isinstance(conn, wire.Target):
        return conn.model
    if conn.get("kind") == "claude":
        return conn.get("model") or CLAUDE_DEFAULT_MODEL
    return conn.get("model", "")


def _swallow(task: asyncio.Task) -> None:
    """Retrieve a finished cleanup's exception so asyncio doesn't log it as
    never-retrieved. Cleanup failures are deliberately not re-raised: by the
    time we are closing, the caller either has its reply or is already being
    told about a timeout, and neither should turn into a different error."""
    if not task.cancelled():
        task.exception()


def _close_when_settled(task: asyncio.Task, agen) -> None:
    """Close an abandoned pull's iterator as soon as that pull finally lets go.

    Without this, skipping the close (which is mandatory while __anext__ runs)
    would mean never closing at all, and each wedged call would strand its
    connection for the life of the process.
    """
    _swallow(task)
    try:
        asyncio.ensure_future(_aclose(agen)).add_done_callback(_swallow)
    except RuntimeError:
        pass  # no running loop (shutdown): nothing left to close into


async def _settle(task: asyncio.Task | None, agen) -> bool:
    """Make sure a pull is over, and report whether its iterator may be closed.

    ``asyncio.wait_for`` is not usable here, and neither is a plain
    ``await task`` after cancelling: both wait for the cancellation they
    request to *finish*, so an upstream that ignores CancelledError turns
    right back into the indefinitely-held request this module exists to
    prevent. So the cancel gets a grace period and is then abandoned — but an
    abandoned pull is still running inside the iterator, and closing an
    iterator mid-``__anext__`` raises, so the caller is told not to close it.
    A detached task is a leak we can live with; a wedged request is not.
    """
    if task is None:
        return True
    if not task.done():
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=_CLOSE_TIMEOUT)
        if not done:
            task.add_done_callback(lambda t: _close_when_settled(t, agen))
            return False
    _swallow(task)
    return True


async def _aclose(agen) -> None:
    """Close the provider under the same grace-then-abandon rule as _settle:
    cleanup that will not unwind must not be allowed to hold the caller."""
    task = asyncio.ensure_future(agen.aclose())
    task.add_done_callback(_swallow)
    done, _ = await asyncio.wait({task}, timeout=_CLOSE_TIMEOUT)
    if not done:
        task.cancel()  # asked to stop; deliberately not awaited


async def _guard(agen, timeout: float, tick: float | None = None, pending=None) -> AsyncIterator[str]:
    """Bound the gap between deltas, whatever the provider underneath.

    The wait covers connect + time-to-first-token on the first pull and
    mid-stream stalls on every later one, so a wedged upstream surfaces as an
    LLMError instead of holding an SSE connection open forever (#243). It is
    deliberately an *idle* bound, not a total one: a slow-but-progressing
    generation is healthy and must not be cut off mid-prose. Callers that need
    a ceiling on total duration impose it themselves (see routes' absorb
    budget). ``timeout <= 0`` disables the bound entirely.

    The bound counts provider *activity*, not visible text: a provider yields
    an empty string for a frame that carries no content (a keep-alive, or the
    reasoning a model can stream for minutes before its first word), which
    resets the wait and is dropped here rather than reaching the caller. Timing
    only yielded text would cancel healthy long-reasoning generations that the
    providers' old HTTP read timeout let run.

    Deltas already received are yielded before the timeout raises, so a partial
    reply stays recoverable by the fence watcher's on_error path.

    Every `tick` seconds without caller-visible text, an empty string is yielded:
    the facade's own liveness signal (#95), for callers that have to keep a
    connection of their own visibly alive. A provider's empty frame does NOT
    satisfy it and is still not forwarded — the two carry different information,
    and only this one is on a schedule the caller picked. So `""` reaching a
    caller means "no text yet, still connected", and never anything else;
    `complete()` joins it away for callers that only want the finished string.

    The two clocks are deliberately different, and conflating them was a bug
    caught in review. The idle bound measures *provider* activity, so every
    frame resets it, empty ones included. The tick measures what the *caller*
    has seen, so it spans pulls and prose or queued display reasoning resets
    it. Content-free provider keepalives do not. Reset per pull instead
    and the adapters defeat it completely: they yield "" for every upstream SSE
    line (see `openrouter.stream`), so a model streaming reasoning fires frames
    far faster than the interval, restarts the clock each time, and the caller's
    connection stays silent for exactly as long as it would have without a
    heartbeat at all.
    """
    # Read at call time, not bound as a default: the constant is the knob tests
    # and any future config path turn, and a default argument would freeze
    # whatever it happened to be at import.
    tick = HEARTBEAT_INTERVAL if tick is None else tick
    it = agen.__aiter__()
    pull: asyncio.Task | None = None
    next_tick = (time.monotonic() + tick) if tick > 0 else None
    try:
        while True:
            pull = asyncio.ensure_future(it.__anext__())
            # The idle bound restarts here, on each new pull. The tick does not.
            deadline = None if timeout <= 0 else time.monotonic() + timeout
            while True:
                # Whichever clock is nearer sets the sleep; a tick therefore
                # consumes part of the timeout's budget rather than extending it
                # -- ticking forever is exactly the "never times out" failure the
                # bound exists to prevent.
                due = [t for t in (deadline, next_tick) if t is not None]
                wait = max(0.0, min(due) - time.monotonic()) if due else None
                done, _ = await asyncio.wait({pull}, timeout=wait)
                if done:
                    break
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise LLMError(
                        "timeout", f"the model sent nothing for {timeout:g}s — giving up")
                if next_tick is not None and now >= next_tick:
                    next_tick = now + tick
                    yield ""  # still waiting on the provider; tell the caller so
            try:
                chunk = pull.result()  # re-raises the provider's own LLMError
            except StopAsyncIteration:
                return
            if chunk or (pending is not None and pending()):
                next_tick = (time.monotonic() + tick) if tick > 0 else None
                yield chunk
            elif next_tick is not None and time.monotonic() >= next_tick:
                # A provider frame that carries no text, arriving on a stream so
                # chatty that the wait above never expires. The pull loop is the
                # only other place the clock can be read, so an overdue tick has
                # to fire from here or a busy-but-silent model never produces one.
                next_tick = time.monotonic() + tick
                yield ""
    finally:
        # Every exit settles the outstanding pull and closes the provider: on a
        # timeout the pull is cancelled here, and on a caller-side close (an SSE
        # client disconnecting) this is what still propagates the close down to
        # httpx — which iterating the provider directly used to do for free.
        if await _settle(pull, it):
            await _aclose(it)


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
    and its account block. So a fallback, a degrade sibling or a retry each
    describes itself, and a row that fell back names the fallback.
    """
    if usage is None:
        return
    if not isinstance(route, _Route):
        # One attempt handed alone, as its target.
        route = _Route(route, 0)
    target = route.target
    reasoning = usage.get(llm_reasoning.KEY)
    usage.clear()
    if isinstance(reasoning, llm_reasoning.Buffer):
        reasoning.begin()
        usage[llm_reasoning.KEY] = reasoning
    usage.update({"model": target.model, "connection": _label(target),
                  "provider": target.kind, "attempts": attempts,
                  # Which attempt is live, for the route that may have to
                  # report an outcome this facade never sees. See `ATTEMPTED`.
                  ATTEMPTED: target})
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
            log.warning("could not count the tokens %r did not report: %s "
                        "(said once until a count works again)",
                        _label(conn), type(exc).__name__)


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
        log.warning("could not record connection health for %r: %s", _label(conn), exc)


def fallback_sampling(primary: wire.Target, fallback: wire.Target) -> wire.Target:
    """`fallback` as it will be sent when it serves `primary`'s generation.

    A preset chosen for the ROUTE follows it onto the fallback; only a
    connection-level answer stays with its connection. Otherwise a route set to
    "no preset" -- to keep a role-play preset's token cap off absorb, say --
    would get that cap back the moment the primary rate-limited and a fallback
    carrying it took the call. Public because the prompt capture of a fallback
    attempt (`routes.common._record_prompt`) has to describe the same request.
    """
    if primary.sampling.scope in ROUTE_SCOPES:
        return dataclasses.replace(fallback, sampling=primary.sampling)
    return fallback


class PresetRefusalError(LLMError):
    """An `LLMError` that is a sampler parameter THIS request sent being
    refused, rather than the request itself (`_preset_refusal`).

    A subclass rather than a reworded detail alone, so a reader that has to
    tell the two apart asks the type instead of matching prose: the model test
    call (`routes.config`) sends its own reply cap, and a provider refusing
    that cap has said nothing about whether the model can do what was asked.
    Every `except LLMError` still catches it, with the same kind and status."""


#: Keys that select a variant rather than carry a setting: a refusal naming one
#: alone (a content block's `type`) is not about the control that sent it, and
#: one that IS names its parent (`thinking.type`, `thinking`) anyway.
_DISCRIMINATORS = frozenset({"type"})


def _wire_spellings(share: dict, prefix: str = "") -> set[str]:
    """Every name a provider could echo for the fields in `share`: each key, the
    dotted path of each nested key (`output_config.effort`) and the nested key
    itself (`effort`), lowercased -- a discriminator only as part of a path."""
    names: set[str] = set()
    for key, value in share.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}{key}".lower()
        names.add(path)
        if prefix and key not in _DISCRIMINATORS:
            names.add(key.lower())
        if isinstance(value, dict):
            names |= _wire_spellings(value, f"{path}.")
    return names


def _structured_share(target: wire.Target) -> dict:
    """The structured-output envelope this attempt puts on the wire, as a share
    `_wire_spellings` reads: `{}` unless the attempt is flagged
    (`STRUCTURED_KEY`), and `{}` for `claude`, which never takes it.

    The schema itself is emptied: its property names are question ids, and a
    question id that happened to spell a sampler (`temperature`) would be
    subtracted from that sampler's spellings, so a refusal of the sampler
    would stop reading as the preset refused. Only the adapters' own envelope
    keys are named. Read from the flag rather than from
    whether a schema was sent, because only a decide resolution carries the
    flag and `decide` always sends one."""
    if not target.structured:
        return {}
    kind = target.kind
    if kind == "claude":
        return {}
    if kind == "anthropic":
        return {"output_config": {"format": {"type": None, "schema": None}}}
    # `openrouter` and `openai_compatible` -- and whatever else `_dispatch`
    # sends through the OpenRouter adapter.
    return {"response_format": {"type": None, "json_schema": None}}


def _preset_refusal(exc: LLMError, target: wire.Target) -> PresetRefusalError | None:
    """The error to raise in place of `exc` when it is a preset being refused.

    None when it is not: `exc` carries no refusal status, or this attempt sent
    no sampler parameters at all, in which case a 400 is the connection's own
    business and the ordinary fallback rule applies.

    Why this is not handed to the fallback: `_resilient` moves to the next route
    on any failure, which is right for a connection that cannot serve and wrong
    for a setting it refused. Every turn would quietly run on the fallback, the
    primary's health dot would go red over a temperature, and nothing would
    ever say the preset was the problem.
    """
    if exc.status not in PRESET_REFUSAL_STATUSES or llm_errors.account_limit(exc):
        # A spend limit's 400 is the account refused, whatever its prose says.
        return None
    shares = llm_sampling.sent_fields(target)
    sent = list(shares)
    # Only when the provider's message NAMES something that was sent. A 400 is
    # also what a context-length overflow or an unknown model id gets, and with
    # a preset attached those must still reach the fallback and the health
    # verdict exactly as they did before presets existed. Matched on every
    # spelling a provider might echo back: the canonical name and its
    # hyphen/space forms prose uses, and every field the control actually put
    # on the wire (`_wire_spellings`) -- `max_completion_tokens`,
    # `stop_sequences`, `repeat_penalty` beside `repetition_penalty`, and the
    # reasoning control's whole share: adaptive thinking is sent as `thinking`
    # AND `output_config.effort`, and the Anthropic API's refusal of the effort
    # names only the second.
    detail = (exc.detail or "").lower()
    # Less the structured envelope's own spellings (I3): adaptive effort and a
    # schema share the `output_config` parent on the Anthropic API, and a
    # refusal of `output_config.format` is the SCHEMA refused -- which leaves
    # the fallback, sent the same prompt without the mode, to be tried. A
    # refusal naming `output_config.effort` (or `effort`) still matches.
    # Known limit: only the envelope's own names are subtracted, so a
    # structured refusal whose message ALSO names a sent sampler ("response_format
    # is not supported with reasoning") still reads as a preset refusal.
    envelope = _wire_spellings(_structured_share(target))
    spellings = {name: {name, name.replace("_", "-"), name.replace("_", " "),
                        *_wire_spellings(shares[name])} - envelope
                 for name in sent}
    # llama.cpp's spelling, whichever one this endpoint was sent.
    spellings.get("repetition_penalty", set()).add("repeat_penalty")
    if not any(form in detail for forms in spellings.values() for form in forms):
        return None
    name = target.sampling.preset_name or target.sampling.preset_id or "?"
    return PresetRefusalError(
        exc.kind,
        f"{exc.detail} — this request carried sampler preset “{name}” "
        f"({', '.join(sent)}) and the provider's refusal names one of them, so "
        "the fallback connection was not tried",
        exc.retry_after, status=exc.status, code=exc.code)


class SchemaRefusalError(LLMError):
    """A call that failed on every route, at least one of which failed because
    the structured-output field THAT attempt was sent was refused
    (`_schema_refusal`).

    A subclass so `inference.decide` can ask the type: it re-sends each
    refusing attempt once without the mode (spec M-4, ruling 3). The schema
    always rides the prompt, so the reply still parses -- and the call that
    worked as a prompt-only call before slice F still works after it, whether
    the refusal came from the primary or its fallback, and whatever the other
    route then did. Every `except LLMError` still catches it.

    `words` is what each route the call ran failed with -- the primary, then
    the fallback when there was one -- and `attempts[i]` is route i's target
    as it was sent, when its failure was the refusal, else None. The error
    itself is `routes_failed(words)`: a lone route's own failure (with its
    kind and status), or the two composed."""

    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None,
                 status: int | None = None, code: str | None = None, *,
                 attempts: tuple[wire.Target | None, ...] = (),
                 words: tuple[LLMError, ...] = ()):
        super().__init__(kind, detail, retry_after, status=status, code=code, words=words)
        self.attempts = attempts


def routes_failed(words: Sequence[LLMError]) -> LLMError:
    """The one error a call raises when every route it ran failed: a lone
    route's own failure, or -- the primary and its fallback both failed -- the
    two composed.

    Composed, neither error alone is the whole truth. The kind stays the
    PRIMARY's, because that is the connection the user chose and the one the
    frontend branches on -- reporting a refused connection to a local fallback
    would send someone off to debug an endpoint they were not using while their
    real problem was a rate limit. But dropping the fallback's failure is just
    as misleading: it leaves them fixing the primary and still getting nothing.
    The window comes from the primary for the same reason the kind does. It is
    what reaches the caller as the `Retry-After` of a 429 (#213), and a
    fallback's window would say when a connection they are not using will be
    ready.

    Except when the primary's failure is its structured field refused
    (`SchemaRefusalError`): that is a mode the call can drop, not the
    connection's word, so the fallback's failure -- a rate limit, and the
    window it named -- is the one that says what actually stopped the call.

    Composed, it keeps the routes' own failures as `words`, so a fact the one
    sentence cannot carry -- a caller's clock having stopped one of them --
    is still there to be asked."""
    if len(words) == 1:
        return words[0]
    primary, fallback = words[0], words[-1]
    word = (fallback if isinstance(primary, SchemaRefusalError)
            and not isinstance(fallback, SchemaRefusalError) else primary)
    return LLMError(word.kind, f"{primary.detail} — and the fallback failed too: "
                               f"{fallback.detail}", word.retry_after, words=tuple(words))


def _said(exc: LLMError) -> str:
    """`exc.detail`, followed by what the upstream endpoint said when an
    aggregator relayed its error under a message of its own (an `upstream`
    attribute, `openrouter.OpenRouterError`'s)."""
    upstream = getattr(exc, "upstream", "")
    detail = exc.detail or ""
    return f"{detail}: {upstream}" if isinstance(upstream, str) and upstream else detail


def _schema_refusal(exc: LLMError, target: wire.Target) -> bool:
    """Whether `exc` is the structured envelope `target` was sent being refused:
    an attempt flagged for the mode, a refusal status, and a message that
    names one of the envelope's own spellings and nothing else that was sent.

    Only the envelope's specific spellings count (a dotted path or an
    underscored key -- `response_format`, `output_config.format`), never its
    bare words: "format" and "schema" are in plenty of 400s that are about
    something else. A message that also names a sent sampler is that
    sampler's business (`_preset_refusal` reads it first on a primary)."""
    envelope = _wire_spellings(_structured_share(target))
    if (not envelope or exc.status not in PRESET_REFUSAL_STATUSES
            or llm_errors.account_limit(exc)):
        return False
    # With what the upstream said, when an aggregator relayed its refusal
    # under a wrapper message of its own (`openrouter.OpenRouterError`): the
    # wrapper names nothing, and the endpoint's own sentence names the field.
    detail = _said(exc).lower()
    if not any(form in detail for form in envelope if "." in form or "_" in form):
        return False
    shares = llm_sampling.sent_fields(target)
    others = {form for name in shares
              for form in (name, name.replace("_", "-"), name.replace("_", " "),
                           *_wire_spellings(shares[name]))} - envelope
    return not any(form in detail for form in others)


async def _resilient(open_stream, routes: list[_Route], timeout: float,
                     tick: float | None = None,
                     usage: dict | None = None,
                     observer=None, capture: llm_capture.Sink | None = None,
                     counter=None) -> AsyncIterator[str]:
    """Run `routes` in order, retrying each for as many attempts as it carries.

    `routes` is a list of `_Route`s -- the active connection first, then the
    configured fallback (if any) with a single attempt of its own, per #144's
    "tried once after the primary's retries are exhausted".

    **Retrying and falling back are two different questions and are gated
    separately.** A retry re-runs the request that just failed, so it is only
    worth doing for the failures a repeat could plausibly fix and detect
    cheaply (`RETRYABLE_KINDS`) — and only when the provider has not told us
    the wait would be longer than we are willing to sit out
    (`RETRY_AFTER_CAP`). Moving to the *next route* is a different
    request to a different place, so it is worth doing for any failure at all
    -- a bad key, an uninstalled SDK, a timeout, a 500 -- because "the primary
    could not serve this" is the entire condition the user configured a
    fallback for. So a non-retryable failure stops attempting *this* route
    immediately and hands the generation to the next one, rather than ending
    the whole call.

    **Nothing is ever retried once prose has reached the caller.** Display
    reasoning is separate and explicitly reset at each attempt. That is the
    whole reason this wraps `stream` rather than only `complete`: a retry is
    only safe while the caller has seen nothing, and the facade is the one
    place that knows. For the blocking routes that is the entire call (nothing
    is visible until `complete` returns); for the streamed ones it is the
    pre-first-token window -- connect, auth, rate-limit rejection, and
    time-to-first-token -- which is where the transient failures actually live.
    A provider that dies mid-prose still surfaces as an error, because the
    bytes are already on the wire to the browser and there is no way to retract
    them. Fixing *that* needs buffered re-streaming or a client-side reconnect
    protocol; neither exists here, and pretending otherwise would duplicate
    already-shown text. Option B of #144 says so out loud; this is the code
    that means it.

    An empty chunk does not count as text. It is either a provider keep-alive
    or the facade's own heartbeat (`_guard`), and the callers turn it into an
    SSE comment -- framing, carrying no content, so a fresh attempt after one
    duplicates nothing.

    Retries are bounded but not free, in two currencies. Time: they run
    *inside* whatever ceiling the caller already imposes
    (`routes.common._bounded_call` for the one-shots, the absorb budget for
    absorb), so a sequence can be cut short by those but can never overrun
    them. And money: a connection that drops after the provider generated but
    before the first delta arrived is billed for work nobody received, and the
    retry is billed again. That is inherent to retrying at all -- there is no
    way to tell that case from a connection refused -- and it is why the count
    is a setting with a documented 0.

    When both routes fail the caller gets the *primary's* kind, with the
    fallback's failure appended: see `routes_failed`. When a route's failure
    was its structured field refused, the error is a `SchemaRefusalError`
    carrying that attempt, which `inference.decide` re-sends without the mode.

    `counter` (`str -> int`, or None for no estimate) counts what a provider
    did not report, for an attempt whose stream ended on its own: each prose
    chunk is noted as it is yielded (reasoning is noted by the adapters), and
    `_estimate` runs before the return. An attempt the caller broke out of,
    closed or that failed never reaches it, so it is never estimated.
    """
    # A holder is needed even for callers uninterested in usage: the adapters
    # receive their recorder through this existing per-attempt seam, and a
    # degrade sibling (#377) is decided by how many images the failed attempt
    # sent, which `_dispatch` writes here. Otherwise none is allocated -- one
    # per call for nobody would be pure overhead.
    if usage is None and (capture is not None or any(r.target.degrade for r in routes)):
        usage = {}
    call_id = uuid.uuid4().hex if capture is not None else ""
    sent = False
    tries = 0
    first: LLMError | None = None
    last: LLMError | None = None
    #: Route (0 the primary, 1 the fallback) -> its target as sent
    #: (`_Route.sent`), while that route's failure is its structured field
    #: refused.
    schema_refused: dict[int, wire.Target] = {}
    fell_back = False
    sent_images = 0
    previous: _Route | None = None
    for index, route in enumerate(routes):
        target = route.target
        if target.degrade:
            # The same connection again, sent text -- only when it just refused
            # a request that carried images. Not a fallback: it sets nothing
            # `fell_back` reads, and its failure is that connection's word.
            if not (last is not None and last.status in REJECTED_STATUSES and sent_images > 0):
                continue
            log.warning("images refused by %r; retried as text", _label(target))
        elif index > 0:
            fell_back = True
            log.warning("LLM connection %r gave up (%s: %s); falling back to %r",
                        _label((previous or route).target), last.kind, last.detail,
                        _label(target))
        if not target.degrade:
            previous = route
        retryable = True
        for attempt in range(max(0, route.retries) + 1):
            if attempt:
                # Sliced at the heartbeat interval, and yielding the same
                # content-free chunk `_guard` does. Between attempts there is no
                # provider stream for `_guard` to time, so an unsliced sleep is a
                # window where nothing crosses the caller's SSE connection at
                # all -- survivable at the default two retries (under two
                # seconds in total), not at the ten the setting allows, where
                # the backoffs add up to well past the interval a proxy will
                # hold a silent connection for.
                # The provider's own window wins whenever it named one and it is
                # longer than ours: retrying before it is a request the provider
                # has already told us it will reject. `max`, not a replacement,
                # so a `Retry-After: 1` on the third attempt cannot walk the
                # backoff back down to a shorter wait than the second one had.
                delay = max(_backoff_delay(attempt - 1), (last.retry_after or 0.0))
                while delay > 0:
                    step = delay if HEARTBEAT_INTERVAL <= 0 else min(delay, HEARTBEAT_INTERVAL)
                    await asyncio.sleep(step)
                    delay -= step
                    if delay > 0:
                        yield ""  # still here, waiting the provider out
            tries += 1
            _stamp(usage, route, tries)
            if capture is not None and usage is not None:
                usage[llm_capture.KEY] = llm_capture.Capture(
                    capture, call_id, tries, target.model, target.kind)
                llm_capture.emit(usage, "start", None)
            outcome = "interrupted"
            if llm_reasoning.pending(usage):
                yield ""
            agen = _guard(open_stream(route, usage), timeout, tick,
                          pending=lambda: llm_reasoning.pending(usage))
            try:
                async for chunk in agen:
                    sent = sent or bool(chunk)
                    if chunk:
                        llm_usage.note_reply(usage, chunk)
                    yield chunk
                outcome = "complete"
                _observe(observer, target, None)
                # The reply has been yielded in full, but the call is not
                # over until this returns: a cancel landing here (a
                # disconnect, a detached run's cancel) or `_bounded_call`'s
                # ceiling discards a generated reply, and the ceiling's
                # `on_timeout` marks the connection timed out after `_observe`
                # recorded its success. Accepted, because the window is
                # bounded by `COUNT_TIMEOUT_S` and is open only when a
                # provider reported no counts (usually warm: a turn's compose
                # has already loaded, or failed to load, the encoder). This is
                # E's cancel window (ruling 11; `docs/store-guarantees.md`,
                # "What is not promised"): `_estimate` stays here, where the
                # attempt ended on its own, until a meter files its row
                # asynchronously.
                await _estimate(usage, target, counter)
                return
            except LLMError as exc:
                outcome = "error"
                # The primary only -- which includes its own degrade sibling
                # (#377), the same connection re-sent as text. A FALLBACK that
                # refuses a preset has nothing further to skip, and the
                # both-failed message below already names its failure beside
                # the primary's.
                primary = index == 0 or (index == 1 and target.degrade)
                refused = _preset_refusal(exc, target) if primary and not sent else None
                if refused is not None:
                    # Not observed: the connection answered, and what it
                    # refused was a setting. A health verdict here would mark
                    # it failing for a problem no connection change can fix.
                    raise refused from exc
                schema = _schema_refusal(exc, target)
                if schema:
                    # Not observed either, for the preset's reason (CODE-M5):
                    # the connection answered and refused the mode -- a
                    # catalog that over-advertised it -- and it still serves
                    # every generate call. The next route, sent the same
                    # prompt, is tried as for any failure; should every route
                    # fail, the error carries this attempt, for `decide` to
                    # re-send once without the mode.
                    log.warning("structured output refused by %r: %s", _label(target),
                                _said(exc))
                else:
                    _observe(observer, target, exc)
                if sent:
                    raise
                # Keyed by route (a degrade sibling is its primary's), and
                # cleared by a later failure that is not one: a route's word is
                # what it failed with, so only a refusal that IS that word
                # earns the re-send.
                if schema:
                    schema_refused[0 if primary else 1] = route.sent()
                else:
                    schema_refused.pop(0 if primary else 1, None)
                last = (SchemaRefusalError(exc.kind, _said(exc), exc.retry_after,
                                           status=exc.status, code=exc.code)
                        if schema else exc)
                sent_images = usage.get("images", 0) if usage is not None else 0
                # The primary's word is its first failure -- or, when its own
                # degrade sibling ran, that sibling's (#377).
                first = last if first is None or (primary and index == 1) else first
                retryable = (exc.kind in RETRYABLE_KINDS and not schema
                             and not (exc.retry_after or 0.0) > RETRY_AFTER_CAP)
            finally:
                # A no-op for the exhausted and the raised cases, and the whole
                # point in the third one: when the *caller* closes us mid-yield
                # (an SSE client disconnecting), GeneratorExit unwinds through
                # here and this is what still propagates the close down to
                # `_guard`, and from there to httpx. Without it the provider
                # connection would wait for the garbage collector.
                try:
                    await agen.aclose()
                finally:
                    llm_capture.emit(usage, "end", {"status": outcome})
                    # The prompt reference does not outlive the attempt.
                    if usage is not None:
                        usage.pop(llm_usage.ESTIMATE_KEY, None)
            if not retryable:
                break  # a repeat cannot fix this one; the next route might
    # Only reachable with every attempt swallowed above, which is the only way
    # out of the loops without a return or a raise -- so both are always set.
    assert first is not None and last is not None
    # One route, however many attempts it took: the failure it ended on is the
    # whole story, and its `retry_after` is the freshest window the provider
    # named. The test used to be `first is last`, which is a different question
    # and got this wrong -- a RETRIED route raises two distinct exception
    # objects, so three attempts against a lone connection reported "and the
    # fallback failed too" to a user who had configured no fallback at all.
    # Both routes failed: `routes_failed` composes the two.
    words = (first, last) if fell_back else (last,)
    error = routes_failed(words)
    if not schema_refused:
        raise error
    raise SchemaRefusalError(error.kind, error.detail, error.retry_after,
                             status=error.status, code=error.code,
                             attempts=tuple(schema_refused.get(pos) for pos in range(len(words))),
                             words=words)


def _native_route(target: wire.Target) -> _Route:
    """The one route a native decision makes: `target` alone and without its
    sampler preset, which a decision is never sent (spec 8) -- so neither the
    wire nor the ledger's `preset` names one."""
    return _Route(_target_of(target).without_sampling(), 0)


def native_body(item: decisions.Item, target: wire.Target) -> dict:
    """The body a native decision on `target` sends for `item`: its kind's
    adapter's `decision_body`, on the model the call runs on. Pure, and holds
    no key or URL, so a capture can record what was asked. A kind with no
    native endpoint is refused with the `bad_response` a call on it gets,
    unsent."""
    return adapters.decision_body(item, _target_of(target))


class LLMClient:
    """Dispatches each call to the resolved connection's kind."""

    def __init__(self, openrouter=None, claude=None, openai_compatible=None, timeout=None,
                 retries=None, observer=None, capture=None,
                 images=None, load_image=None, anthropic=None, count_tokens=None):
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()
        self._openai_compatible = (openai_compatible if openai_compatible is not None
                                    else OpenAICompatibleClient())
        # Last in the signature so no positional caller shifts.
        self._anthropic = anthropic if anthropic is not None else AnthropicClient()
        #: One adapter per kind (`adapters`), around the clients above, which
        #: stay attributes: they are the test seams.
        self._adapters = adapters.build(
            openrouter=self._openrouter, openai_compatible=self._openai_compatible,
            anthropic=self._anthropic, claude=self._claude)
        # A number, or a callable returning one. Callable is how routes hands
        # over the config.md setting without this module importing the store —
        # the gateway's imports are kept acyclic and store-free on purpose
        # (#239) — and resolving per call is also what lets a Configuration-page
        # change land without a restart.
        self._timeout = timeout
        # Same contract, for the same reason: the retry count is a config.md
        # setting. The fallback (#144) is not the client's: every call carries
        # its own, the resolver's per-call choice (`wire.Chain.fallback`).
        self._retries = retries
        #: Called with `(conn, error_or_None)` as each attempt settles, so the
        #: health registry learns what the provider actually did without
        #: anything having to poll it (#146). A callable rather than the
        #: registry itself, for the third time on this constructor and the same
        #: reason: the registry lives on `app.state` and this module may not
        #: reach into the app any more than it may reach into the store.
        self._observer = observer
        # Resolver returns a sink, or None when capture is off. Like timeout,
        # this keeps runtime configuration in the store and out of the gateway.
        self._capture = capture
        # Post images (#377), the same contract once more: `images(attempt)` is
        # how many a prompt for that attempt may carry right now (0 = none) and
        # `load_image(campaign, part)` turns one reference into a data URI.
        # Both are store lookups, so both arrive as callables.
        self._images = images
        self._load_image = load_image
        # Once more, and last in the signature so no positional caller shifts:
        # `count_tokens(text)` counts what a provider did not report (spec
        # 9.1). It is the store's tokenizer, so it arrives as a callable. None
        # counts nothing -- a hand-built client files exactly what it did.
        self._count_tokens = count_tokens

    def _timeout_seconds(self) -> float:
        if self._timeout is None:
            return DEFAULT_TIMEOUT
        return float(self._timeout() if callable(self._timeout) else self._timeout)

    def _retry_count(self) -> int:
        """Retries after the first attempt. Never negative, and never an
        exception: a malformed setting must not take generation down with it,
        which is the same posture `store.config` takes on every other knob."""
        if self._retries is None:
            return DEFAULT_RETRIES
        try:
            return max(0, int(self._retries() if callable(self._retries) else self._retries))
        except (TypeError, ValueError):
            return DEFAULT_RETRIES

    def _adapter(self, kind: str) -> adapters.Adapter:
        """The adapter that sends a target of `kind`. A kind the registry does
        not know is sent through OpenRouter's, as the facade always sent one."""
        return self._adapters.get(kind) or self._adapters["openrouter"]

    def _native_adapter(self, kind: str) -> adapters.Adapter:
        """The adapter that sends a native decision on `kind`, or the
        `bad_response` a kind with no native endpoint is refused with,
        unsent (`adapters.decides_natively`)."""
        if not adapters.decides_natively(kind):
            raise LLMError("bad_response", f"{kind} connections have no native decisions endpoint")
        return self._adapters[kind]

    def _routes(self, attempt: wire.Chain | wire.Target,
                retries: int | None = None) -> list[_Route]:
        """The attempts one generation may make, in order.

        The primary with its retry budget -- `retries` when the call names
        one (`complete(retries=)`), else the client's -- then the fallback
        the CALL carries (`wire.Chain.fallback`, the resolver's own per-call
        choice) with a single attempt, sent the route's preset when the
        primary's came from a route scope (`fallback_sampling`).

        The fallback is dropped when it is the connection that is already
        primary -- see `_same_route`.
        """
        chain = _chain_of(attempt)
        routes = [_Route(chain.primary,
                         self._retry_count() if retries is None else max(0, retries))]
        fallback = chain.fallback
        if fallback is not None and not _same_route(chain.primary, fallback):
            routes.append(_Route(fallback_sampling(chain.primary, fallback), 0))
        return routes

    def _usable_routes(self, messages: list[dict], attempt: wire.Chain | wire.Target,
                       retries: int | None = None) -> list[_Route]:
        """`_routes`, minus a FALLBACK that cannot carry these messages.

        An image description is drafted from a multimodal message, and the
        route layer refuses a primary connection whose client would flatten it
        (`store.image_drafts`). The fallback was never checked: with an
        OpenRouter primary and a Claude fallback, a primary failure sent those
        same content parts down the SDK path, which joins content as a string
        and raises -- so the user was shown "and the fallback failed too" about
        a connection they had not chosen for this call, in place of the real
        error from the one they had (PR review).

        The PRIMARY is never dropped here, even when it cannot carry them: the
        route above returns a 409 the reader can act on, and answering "no
        route at all" from this layer would replace that with something worse.
        """
        routes = self._routes(attempt, retries)
        if _carries_parts(messages):
            routes = routes[:1] + [r for r in routes[1:]
                                   if r.target.kind not in TEXT_ONLY_KINDS]
        # A pure check: no resolver runs while routes are built, so nothing reads
        # a catalog sidecar on the event loop. Whether a sibling is attempted is
        # `_resilient`'s question, asked after the attempt before it.
        return _with_degrades(routes) if _may_send_refs(messages) else routes

    def _dispatch(self, messages: list[dict], route: _Route, usage: dict | None = None,
                  schema: dict | None = None):
        # Read before selecting: `for_model` returns a plain list.
        campaign = getattr(messages, "campaign", "")
        target = route.target
        sent = route.sent()
        # Select per ATTEMPT: retries retain the frozen prompt, while a
        # fallback repacks that same context with its own model's guidance.
        # Ordinary message lists (JSON extraction, judges, drafts) stay ordinary.
        if isinstance(messages, model_guidance.PreparedMessages):
            # Per attempt, not `for_model`: a prompt with per-attempt tails
            # ("Keep writing") ends the way THIS attempt can take -- a
            # fallback that cannot continue a prefill is sent the instruction
            # instead. Untailed prompts are `for_model`.
            messages = messages.for_target(sent)
        if usage is not None:
            usage["images"] = 0
        # After `_stamp` cleared the holder, so each attempt counts only itself.
        llm_usage.note_prompt(usage, messages)
        if not content_parts.needs_lowering(messages):
            return self._generate(messages, target, usage, schema)
        return self._lowered(messages, route, usage, campaign, schema)

    def _generate(self, messages: list[dict], target: wire.Target, usage: dict | None,
                  schema: dict | None):
        """One attempt's provider stream, through its kind's adapter.

        Structured output is decided per ATTEMPT like the preset (spec 7.2):
        only a target its resolver flagged capable (`structured`) is asked
        for it, and the adapter passes the keyword only when there is
        something to send, so every other call is the call it was before
        slice F. The prompt carries the schema either way; this adds the
        provider's mode. The adapter decides the sampler controls against
        that attempt's own target (`llm_sampling.effective`), so a fallback
        of another kind is held to what ITS backend takes."""
        return self._adapter(target.kind).generate(
            messages, target, usage, schema=schema if target.structured else None)

    async def _lowered(self, messages: list[dict], route: _Route, usage: dict | None,
                       campaign: str, schema: dict | None = None):
        """`messages` lowered for `route`, then streamed (#377).

        Lowering resolves the image budget (a catalog sidecar read) and loads
        pictures (a decode, cached), so it runs off the event loop. It runs
        inside the attempt, so its time counts against the first-delta idle
        bound: a pathological stall here is recorded as that connection's
        timeout -- accepted, since a warm picture is a cache hit and a cold one
        is one bounded decode. The provider stream is closed in `finally`, so a
        caller's close or a timeout still reaches httpx exactly as `_guard` and
        `_resilient` intend."""
        lowered, sent = await asyncio.to_thread(self._lower, messages, route, campaign)
        if usage is not None:
            usage["images"] = sent
        # What was sent, not what was asked: text lowering drops carriers (M10).
        llm_usage.note_prompt(usage, lowered)
        inner = self._generate(lowered, route.target, usage, schema)
        try:
            async for chunk in inner:
                yield chunk
        finally:
            aclose = getattr(inner, "aclose", None)
            if aclose is not None:
                await aclose()

    def _lower(self, messages: list[dict], route: _Route,
               campaign: str) -> tuple[list[dict], int]:
        keep = 0 if route.target.degrade else self._image_budget(route)
        if keep <= 0:
            return content_parts.as_text(messages), 0
        return content_parts.as_images(messages, keep, lambda part: self._load(campaign, part))

    def _image_budget(self, route: _Route) -> int:
        """How many images `route` may be sent now. Never more than 0 for a kind
        whose client cannot carry a part, and 0 when the resolver is missing or
        raises: a broken lookup must not fail a turn the text would serve. The
        resolver is handed the attempt's target, less the degrade marker."""
        if self._images is None or route.target.kind in TEXT_ONLY_KINDS:
            return 0
        try:
            return max(0, int(self._images(route.sent())))
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("could not resolve whether %r reads images: %s", _label(route.target), exc)
            return 0

    def _load(self, campaign: str, part: dict) -> str | None:
        if self._load_image is None:
            return None
        try:
            return self._load_image(campaign, part)
        except Exception as exc:  # noqa: BLE001 - one picture is never worth a turn
            log.warning("could not load a post image to send: %s", exc)
            return None

    def stream(self, messages: list[dict], chain: wire.Chain | wire.Target,
               usage: dict | None = None, *, schema: dict | None = None):
        """Every provider stream leaves the facade idle-bounded — the one place
        the bound is provider-independent (the Claude SDK has no httpx client
        to configure at all) — and retried-then-fallen-back, which for the same
        reason can only be decided here: `_resilient` is what knows whether a
        delta has already reached the caller (#144).

        The dispatch is deliberately deferred into a lambda rather than built
        once: each attempt needs its *own* provider stream, aimed at whichever
        route it is running.

        `usage`, when given, is a plain dict this fills in place with what the
        call cost (#152) -- tokens, money where the provider names it, and which
        route answered. A holder rather than a return value because this IS the
        return value: an async generator has nowhere to put a summary, and the
        numbers arrive on the provider's last frame anyway, after the caller has
        consumed every delta. `store.usage.Meter` owns one and files it.

        `schema`, when given, asks for JSON matching that JSON Schema (spec
        7.2): each target flagged `structured` is sent its provider's
        structured mode, and every other attempt -- a fallback without the
        mode included -- is sent the call it would have been sent anyway. The
        caller puts the schema in the prompt too, so an unflagged attempt can
        still answer it.

        `chain` is the primary and the fallback it may fail over to (a lone
        `Target` is a chain of one). A lowered connection dict is a
        `TypeError`: the facade sends typed targets only.
        """
        return self._streamed(messages, chain, usage, schema, None)

    def _streamed(self, messages: list[dict], chain: wire.Chain | wire.Target, usage: dict | None,
                  schema: dict | None, retries: int | None):
        """`stream`'s body, with the primary's retry count `complete` may
        name (`_routes`)."""
        try:
            sink = self._capture() if self._capture is not None else None
        except Exception:  # noqa: BLE001 - failed diagnostic setup must not stop generation
            sink = None
        return _resilient(lambda route, holder: self._dispatch(messages, route, holder, schema),
                          self._usable_routes(messages, chain, retries), self._timeout_seconds(),
                          usage=usage, observer=self._observer, capture=sink,
                          counter=self._count_tokens)

    async def complete(self, messages: list[dict], chain: wire.Chain | wire.Target,
                       usage: dict | None = None, *, schema: dict | None = None,
                       retries: int | None = None) -> str:
        """`stream`, joined. `schema` is `stream`'s; slice F's only caller is
        `decide` (spec 7.2's `generate(schema=)` until slice I).

        `retries`, when given, is the primary route's retry count in place of
        the client's, and nothing else changes: a decide chain's fallback
        STAGE is sent as a call of its own, and gets the one attempt a
        fallback gets (spec 5.4, slice H ruling 12). Absent, the call is
        exactly `stream`'s."""
        return "".join([chunk async for chunk in self._streamed(messages, chain, usage,
                                                                schema, retries)])

    async def single(self, messages: list[dict], target: wire.Target,
                     usage: dict | None = None) -> str:
        """Exactly one attempt on `target`, joined: the model test call's way in.

        No retry, no fallback route and no degrade sibling -- the route list is
        `target` with zero retries and nothing else, so `_resilient` makes one
        attempt and raises what it raised, exactly as `stream` would have from
        that attempt. A test is a question about ONE model on ONE provider, and
        each further attempt is money spent answering a different question:
        a fallback's success would be recorded against a model that never
        answered, a retry would pay twice for a 429, and a text-only re-send
        would call a model that refused the picture one that read it.

        Everything else is `stream`'s, through the same `_resilient` and
        `_dispatch`: the idle bound, the per-attempt `usage` stamp a
        `store.usage.Meter` files, the incoming-response capture, and the
        adapter's `llm_sampling.effective` -- which is how the probe's reply
        cap reaches the Anthropic API as its required `max_tokens`.

        Not reported to the health observer. A probe's refusal is about the
        model (this one reads no images), not about whether the connection
        serves, and a status dot turned red by a vision probe would send the
        reader to fix a connection that works.
        """
        try:
            sink = self._capture() if self._capture is not None else None
        except Exception:  # noqa: BLE001 - failed diagnostic setup must not stop the call
            sink = None
        agen = _resilient(lambda route, holder: self._dispatch(messages, route, holder),
                          [_Route(_target_of(target), 0)],
                          self._timeout_seconds(),
                          usage=usage, capture=sink, counter=self._count_tokens)
        return "".join([chunk async for chunk in agen])

    async def decide_native(self, item: decisions.Item, target: wire.Target,
                            usage: dict | None = None, *,
                            retries: int | None = None) -> decisions.ItemResult:
        """Ask `item` of `target`'s native decisions endpoint (spec 7.4), through
        its kind's adapter: one attempt, retried, and never fallen back -- the
        chain of stages is `inference.decide`'s.

        Refused unsent, before any stamp (so a meter files no row, and still
        records the failure): a kind with no native endpoint
        (`adapters.decides_natively`), and an item the endpoint cannot
        represent (`decisions.native_gap`, ruling 25), as a `bad_response`
        with the code `native_unrepresentable`.

        Retries are `_resilient`'s rule: only `RETRYABLE_KINDS`, never past a
        `Retry-After` over `RETRY_AFTER_CAP`, after the longer of the backoff
        and the provider's window. `retries` overrides the client's count.
        Each attempt is stamped and captured like a generation's; none is
        estimated (no `Estimate`, no `note_prompt`), because a native row's
        billing unit is not a chat prompt, and none is sent sampling, which a
        decision takes none of (spec 8) -- nor stamped with it: the target is
        sent `without_sampling`, so the row files no `preset`. A status in
        `NATIVE_REJECTED_STATUSES` is not reported to the observer.
        """
        route = _native_route(target)
        attempt = route.target
        adapter = self._native_adapter(attempt.kind)
        gap = decisions.native_gap(item)
        if gap:
            raise LLMError("bad_response", gap, code="native_unrepresentable")
        try:
            sink = self._capture() if self._capture is not None else None
        except Exception:  # noqa: BLE001 - failed diagnostic setup must not stop the call
            sink = None
        if usage is None and sink is not None:
            usage = {}  # the adapter receives its recorder through the holder
        call_id = uuid.uuid4().hex if sink is not None else ""
        attempts = 1 + (self._retry_count() if retries is None else max(0, retries))
        last: LLMError | None = None
        for tries in range(1, attempts + 1):
            if last is not None:
                await asyncio.sleep(max(_backoff_delay(tries - 2), last.retry_after or 0.0))
            _stamp(usage, route, tries)
            if sink is not None and usage is not None:
                usage[llm_capture.KEY] = llm_capture.Capture(
                    sink, call_id, tries, attempt.model, attempt.kind)
                llm_capture.emit(usage, "start", None)
            outcome = "interrupted"
            try:
                result = await adapter.decide(item, attempt, usage,
                                              bound=self._timeout_seconds())
                outcome = "complete"
            except LLMError as exc:
                outcome = "error"
                if exc.status not in NATIVE_REJECTED_STATUSES:
                    _observe(self._observer, attempt, exc)
                retryable = (exc.kind in RETRYABLE_KINDS
                             and not (exc.retry_after or 0.0) > RETRY_AFTER_CAP)
                if not retryable or tries == attempts:
                    raise
                last = exc
                continue
            finally:
                llm_capture.emit(usage, "end", {"status": outcome})
            _observe(self._observer, attempt, None)
            return result
        raise AssertionError("unreachable: the last attempt returns or raises")

    def note_outcome(self, target: wire.Target, error: LLMError | None) -> None:
        """File an outcome this facade did not itself observe (#146).

        There is exactly one such outcome, and it is the reason this is public.
        A one-shot generation runs under a total-duration ceiling imposed by the
        route (`routes.common._bounded_call`), and an overrun *cancels* the
        stream from outside — so `_resilient` unwinds through `GeneratorExit`
        rather than through its `except LLMError`, and the attempt that
        provoked the ceiling would be the one failure the registry never hears
        about. The reader would then be shown a 504 and a green dot.

        Cancellation cannot be read as a failure from inside `_resilient`,
        which is why it is not: a caller walking away (an SSE client
        disconnecting) unwinds identically and is nobody's fault. Only the
        holder of the ceiling knows which of the two just happened, so only it
        can say.

        Filed for the attempt alone, as it is: a target names no fallback.
        """
        _observe(self._observer, _target_of(target), error)

    async def list_models(self, target: wire.Target) -> list[dict]:
        """The catalog `target`'s provider offers, normalized (#149).

        On the facade because the answer depends on the connection's kind, and
        dispatching by kind is the one thing this class is. Before #149 the
        catalog was a second seam of its own that only knew how to ask an
        OpenAI-compatible endpoint — so the picker showed a *custom* endpoint
        its own models and showed every other connection OpenRouter's, fetched
        from the browser against a hardcoded URL whichever provider was
        configured.

        Deliberately **not** retried and **not** fallen back. `_resilient`
        exists so a scene survives a blip; a catalog is a question about one
        named connection, and answering it from a different provider's models
        would hand the reader a list of ids their connection cannot run.
        """
        attempt = _target_of(target)
        adapter = self._adapters.get(attempt.kind)
        if adapter is None or not adapter.lists_models:
            # Unreachable through the API — the route refuses these before it
            # gets here, with a message about the kind rather than a transport
            # failure. Kept as a backstop so a future caller that forgets the
            # check gets an error rather than an AttributeError from a provider
            # with no `list_models`.
            raise LLMError("bad_response", f"{attempt.kind} connections have no model catalog")
        return await adapter.models(attempt)

    async def check(self, target: wire.Target) -> None:
        """Ask `target`'s provider whether it can serve. Returns on yes, raises
        the same `LLMError` a generation would on no (#146).

        One vocabulary, not two: the health report a reader sees is the `kind`
        and `detail` of the error their next turn would have failed with, so a
        check that says `auth` and a scene that says `auth` are saying the same
        thing about the same connection.

        Not retried and not fallen back, for a sharper version of
        `list_models`' reason: "is this connection healthy" answered by trying
        a *different* connection is not an answer, and a retry would report a
        rate-limited provider as healthy after waiting out the window the
        reader is asking about.
        """
        attempt = _target_of(target)
        await self._adapter(attempt.kind).check(attempt)

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
        await self._anthropic.aclose()
