"""Provider-agnostic LLM surface: the dispatch facade over the providers.

The shared error type lives in `llm_errors.py`, not here — see its docstring.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator

from . import content_parts, llm_capture, llm_reasoning, llm_sampling, model_guidance
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
# empty model.
CLAUDE_DEFAULT_MODEL = "opus"

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


def _label(conn: dict) -> str:
    """How a connection is named in a log line: what the user called it, or
    whatever identifies it at all for a connection dict that has no name."""
    return conn.get("name") or conn.get("id") or conn.get("kind") or "?"


#: Connection kinds whose client cannot carry OpenAI-style content PARTS: the
#: Claude SDK path joins a message's content into one string, so a multimodal
#: message raises deep inside it. `store.image_drafts.SUPPORTED_KINDS` states
#: the same rule positively; the ROUTE layer refuses such a connection as the
#: PRIMARY with a message the user can act on, through the `claude` provider
#: preset's `never` (`store.inference.providers`) at the inference seam;
#: `test_image_description_draft.py` pins the two halves to agree.
TEXT_ONLY_KINDS = frozenset({"claude"})

#: Connection kinds whose provider can be asked for a model catalog (#149).
#:
#: `claude` is absent, and not as an oversight: that path's models are aliases
#: the SDK resolves at request time, with no endpoint to enumerate them, which
#: is why `ConnectionForm` offers it a fixed list rather than a live one. The
#: route reads this to refuse a catalog request the provider cannot serve
#: *before* making one, so the reader gets "this kind has no catalog" instead
#: of a transport error from a URL that was never going to exist.
LISTABLE_KINDS = frozenset({"openrouter", "openai_compatible", "anthropic"})

#: Key under which `_stamp` records the connection the *current* attempt is
#: running on, in the usage holder the caller already threads down.
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


def prefill_capable(conn: dict) -> bool:
    """Whether a prompt ending in a partial assistant turn may be sent on `conn`
    as a prefill for the model to continue ("Keep writing", play controls IV).

    The connection's own opt-in and nothing else -- no kind check. Whether a
    trailing assistant message is continued depends on the model behind the
    route, not on the adapter: current Claude models refuse one, and most chat
    templates read it as history and write a fresh reply. So the default is
    the instruction, and the user says where prefill works."""
    return conn.get("prefill") is True


def _carries_parts(messages: list[dict]) -> bool:
    """Does any message hold content parts this facade cannot LOWER to text?

    Image references (#377, `content_parts`) are lowered per route -- a route
    that cannot read images is sent their text -- so they are no reason to
    keep a text-only fallback away. An image-description draft's own
    `image_url` parts are."""
    return any(not isinstance(m.get("content", ""), str)
               and not content_parts.lowerable(m["content"]) for m in messages)


#: The route marker for a DEGRADE sibling (#377): the same connection, sent
#: the text lowering of a prompt whose images it just refused. Never persisted
#: and never sent -- `_dispatch` strips it.
DEGRADE = "_degrade"

#: The HTTP statuses that mean "not this request" -- a provider refusing an
#: image for its format, size or content. `bad_response` alone cannot say this:
#: a 500 maps to it too, and re-sending as text on a server error would drop
#: the pictures from a turn a plain retry would have served. 404 because
#: OpenRouter answers "No endpoints found that support image input" with one
#: when no endpoint it may route to takes images; a 404 for a genuinely missing
#: model costs one extra text attempt that fails the same way.
REJECTED_STATUSES = frozenset({400, 404, 413, 415, 422})


def _with_degrades(routes: list[tuple[dict, int]]) -> list[tuple[dict, int]]:
    """Each route followed by its degrade sibling, with no retries of its own."""
    return [r for conn, n in routes for r in ((conn, n), ({**conn, DEGRADE: True}, 0))]


def _may_send_refs(messages: list[dict]) -> bool:
    """Whether any route could be sent an image reference -- asked of every
    variant a prepared prompt holds, not only the primary's: a fallback packed
    for a larger window can keep a picture the primary's packing gave up, and
    without a sibling its refusal of that picture would end the turn."""
    if isinstance(messages, model_guidance.PreparedMessages):
        return messages.any_variant(content_parts.has_refs)
    return content_parts.has_refs(messages)


def _same_route(a: dict, b: dict) -> bool:
    """Whether two connections would send the same request to the same place.

    Falling back to the connection that just failed is not a fallback: it is a
    third attempt wearing a different name, and it doubles the time a user
    waits to be told the provider is down. Identity first, then the store id --
    two connection dicts read from disk are never the same object.
    """
    if a is b:
        return True
    aid, bid = a.get("id", ""), b.get("id", "")
    return bool(aid) and aid == bid


def effective_model(conn: dict) -> str:
    """The model a generation on `conn` will actually run on.

    Only the Claude path substitutes anything, so this differs from
    ``conn["model"]`` for exactly one kind -- but it is the difference between
    telling the reader "no model" and naming the one about to answer them.
    Both the dispatcher and the config route read the answer from here so the
    status bar cannot drift from what generation does.
    """
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


def _stamp(usage: dict | None, conn: dict, attempts: int) -> None:
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

    `model` is what the request will really run on (`effective_model`), not
    `conn["model"]`, and a provider that reports its own overwrites it: an alias
    resolves to a dated snapshot, and a ledger that says `opus` where the bill
    says `opus-2026-08` cannot be reconciled against an invoice.
    """
    if usage is None:
        return
    reasoning = usage.get(llm_reasoning.KEY)
    usage.clear()
    if isinstance(reasoning, llm_reasoning.Buffer):
        reasoning.begin()
        usage[llm_reasoning.KEY] = reasoning
    usage.update({"model": effective_model(conn), "connection": _label(conn),
                  "provider": conn.get("kind", "openrouter"), "attempts": attempts,
                  # Which connection is live, for the route that may have to
                  # report an outcome this facade never sees. See `ATTEMPTED`.
                  ATTEMPTED: conn})


def _observe(observer, conn: dict, error: LLMError | None) -> None:
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


def fallback_sampling(primary: dict, fallback: dict) -> dict:
    """`fallback` as it will be sent when it serves `primary`'s generation.

    A preset chosen for the ROUTE follows it onto the fallback; only a
    connection-level answer stays with its connection. Otherwise a route set to
    "no preset" -- to keep a role-play preset's token cap off absorb, say --
    would get that cap back the moment the primary rate-limited and a fallback
    carrying it took the call. Public because the prompt capture of a fallback
    attempt (`routes.common._record_prompt`) has to describe the same request.
    """
    sampling = primary.get("sampling")
    if isinstance(sampling, dict) and sampling.get("scope") in ROUTE_SCOPES:
        return {**fallback, "sampling": sampling}
    return fallback


def _preset_refusal(exc: LLMError, conn: dict) -> LLMError | None:
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
    if exc.status not in PRESET_REFUSAL_STATUSES:
        return None
    sent = llm_sampling.sent_names(conn)
    # Only when the provider's message NAMES something that was sent. A 400 is
    # also what a context-length overflow or an unknown model id gets, and with
    # a preset attached those must still reach the fallback and the health
    # verdict exactly as they did before presets existed. Matched on every
    # spelling a provider might echo back: the canonical name, the wire name
    # it was sent under (`max_completion_tokens`, `stop_sequences`,
    # `reasoning`, `thinking`), the wire duplicate (`repeat_penalty`), and the
    # hyphen/space forms prose uses.
    detail = (exc.detail or "").lower()
    wires = llm_sampling.effective(conn)["controls"] if sent else {}
    spellings = {name: {name, name.replace("_", "-"), name.replace("_", " "),
                        wires[name]["wire"] or name}
                 for name in sent}
    spellings.get("repetition_penalty", set()).add("repeat_penalty")
    if not any(form in detail for forms in spellings.values() for form in forms):
        return None
    sampling = conn.get("sampling") or {}
    name = sampling.get("preset_name") or sampling.get("preset_id") or "?"
    return LLMError(
        exc.kind,
        f"{exc.detail} — this request carried sampler preset “{name}” "
        f"({', '.join(sent)}) and the provider's refusal names one of them, so "
        "the fallback connection was not tried",
        exc.retry_after, status=exc.status)


async def _resilient(open_stream, routes, timeout: float,
                     tick: float | None = None,
                     usage: dict | None = None,
                     observer=None, capture: llm_capture.Sink | None = None) -> AsyncIterator[str]:
    """Run `routes` in order, retrying each for as many attempts as it carries.

    `routes` is a list of `(conn, retries)` -- the active connection first,
    then the configured fallback (if any) with a single attempt of its own, per
    #144's "tried once after the primary's retries are exhausted".

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
    fallback's failure appended: see the tail of this function.
    """
    # A holder is needed even for callers uninterested in usage: the adapters
    # receive their recorder through this existing per-attempt seam, and a
    # degrade sibling (#377) is decided by how many images the failed attempt
    # sent, which `_dispatch` writes here. Otherwise none is allocated -- one
    # per call for nobody would be pure overhead.
    if usage is None and (capture is not None or any(c.get(DEGRADE) for c, _n in routes)):
        usage = {}
    call_id = uuid.uuid4().hex if capture is not None else ""
    sent = False
    tries = 0
    first: LLMError | None = None
    last: LLMError | None = None
    fell_back = False
    sent_images = 0
    previous: dict | None = None
    for index, (conn, retries) in enumerate(routes):
        if conn.get(DEGRADE):
            # The same connection again, sent text -- only when it just refused
            # a request that carried images. Not a fallback: it sets nothing
            # `fell_back` reads, and its failure is that connection's word.
            if not (last is not None and last.status in REJECTED_STATUSES and sent_images > 0):
                continue
            log.warning("images refused by %r; retried as text", _label(conn))
        elif index > 0:
            fell_back = True
            log.warning("LLM connection %r gave up (%s: %s); falling back to %r",
                        _label(previous or conn), last.kind, last.detail, _label(conn))
        if not conn.get(DEGRADE):
            previous = conn
        retryable = True
        for attempt in range(max(0, retries) + 1):
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
            _stamp(usage, conn, tries)
            if capture is not None and usage is not None:
                usage[llm_capture.KEY] = llm_capture.Capture(
                    capture, call_id, tries, effective_model(conn), conn.get("kind", "openrouter"))
                llm_capture.emit(usage, "start", None)
            outcome = "interrupted"
            if llm_reasoning.pending(usage):
                yield ""
            agen = _guard(open_stream(conn, usage), timeout, tick,
                          pending=lambda: llm_reasoning.pending(usage))
            try:
                async for chunk in agen:
                    sent = sent or bool(chunk)
                    yield chunk
                outcome = "complete"
                _observe(observer, conn, None)
                return
            except LLMError as exc:
                outcome = "error"
                # The primary only -- which includes its own degrade sibling
                # (#377), the same connection re-sent as text. A FALLBACK that
                # refuses a preset has nothing further to skip, and the
                # both-failed message below already names its failure beside
                # the primary's.
                primary = index == 0 or (index == 1 and bool(conn.get(DEGRADE)))
                refused = _preset_refusal(exc, conn) if primary and not sent else None
                if refused is not None:
                    # Not observed: the connection answered, and what it
                    # refused was a setting. A health verdict here would mark
                    # it failing for a problem no connection change can fix.
                    raise refused from exc
                _observe(observer, conn, exc)
                if sent:
                    raise
                last = exc
                sent_images = usage.get("images", 0) if usage is not None else 0
                # The primary's word is its first failure -- or, when its own
                # degrade sibling ran, that sibling's (#377).
                first = exc if first is None or (primary and index == 1) else first
                retryable = (exc.kind in RETRYABLE_KINDS
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
            if not retryable:
                break  # a repeat cannot fix this one; the next route might
    # Only reachable with every attempt swallowed above, which is the only way
    # out of the loops without a return or a raise -- so both are always set.
    if not fell_back:
        # One route, however many attempts it took: the failure it ended on is
        # the whole story, and its `retry_after` is the freshest window the
        # provider named. The test used to be `first is last`, which is a
        # different question and got this wrong -- a RETRIED route raises two
        # distinct exception objects, so three attempts against a lone
        # connection reported "and the fallback failed too" to a user who had
        # configured no fallback at all.
        raise last
    # Both routes failed, and neither error alone is the whole truth. The kind
    # stays the PRIMARY's, because that is the connection the user chose and
    # the one the frontend branches on -- reporting a refused connection to a
    # local fallback would send someone off to debug an endpoint they were not
    # using while their real problem was a rate limit. But dropping the
    # fallback's failure is just as misleading: it leaves them fixing the
    # primary and still getting nothing.
    raise LLMError(first.kind,
                   f"{first.detail} — and the fallback failed too: {last.detail}",
                   # The window comes from the primary for the same reason the
                   # kind does. It is what reaches the caller as the
                   # `Retry-After` of a 429 (#213), and a fallback's window
                   # would say when a connection they are not using will be
                   # ready.
                   first.retry_after)


class LLMClient:
    """Dispatches each call to the resolved connection's kind."""

    def __init__(self, openrouter=None, claude=None, openai_compatible=None, timeout=None,
                 retries=None, fallback=None, observer=None, capture=None,
                 images=None, load_image=None, anthropic=None):
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()
        self._openai_compatible = (openai_compatible if openai_compatible is not None
                                    else OpenAICompatibleClient())
        # Last in the signature so no positional caller shifts.
        self._anthropic = anthropic if anthropic is not None else AnthropicClient()
        # A number, or a callable returning one. Callable is how routes hands
        # over the config.md setting without this module importing the store —
        # the gateway's imports are kept acyclic and store-free on purpose
        # (#239) — and resolving per call is also what lets a Configuration-page
        # change land without a restart.
        self._timeout = timeout
        # Same contract, for the same two reasons: the retry count is a
        # config.md setting, and `fallback` is a callable that resolves the
        # *connection record* to fall back to (#144). A store lookup behind a
        # callable is what keeps this module free of the store — and it is
        # re-resolved per generation, so repointing the fallback on the
        # Configuration page takes effect on the next send.
        self._retries = retries
        self._fallback = fallback
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
        # Post images (#377), the same contract once more: `images(conn)` is how
        # many a prompt for that connection may carry right now (0 = none) and
        # `load_image(campaign, part)` turns one reference into a data URI.
        # Both are store lookups, so both arrive as callables.
        self._images = images
        self._load_image = load_image

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

    def _routes(self, conn: dict) -> list[tuple[dict, int]]:
        """The connections one generation may be attempted on, in order.

        The active connection with its retry budget, then the configured
        fallback with a single attempt. The fallback is dropped when it
        resolves to the connection that is already primary — see `_same_route`
        — and a resolver that raises is treated as "no fallback": a broken
        fallback must not be able to fail a generation the primary would have
        served.
        """
        routes = [(conn, self._retry_count())]
        try:
            fallback = self._fallback() if callable(self._fallback) else self._fallback
        except Exception as exc:  # noqa: BLE001 - see the docstring; best-effort
            # Logged rather than swallowed in silence: the symptom of a broken
            # resolver is a fallback that is configured and simply never fires,
            # which is invisible from the outside and indistinguishable from
            # "the primary kept working". One line here is the difference
            # between a diagnosable bug and a haunted setting.
            log.warning("could not resolve the fallback connection: %s", exc)
            fallback = None
        if fallback and not _same_route(conn, fallback):
            routes.append((fallback_sampling(conn, fallback), 0))
        return routes

    def _usable_routes(self, messages: list[dict], conn: dict) -> list[tuple[dict, int]]:
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
        routes = self._routes(conn)
        if _carries_parts(messages):
            routes = routes[:1] + [(c, n) for c, n in routes[1:]
                                   if c.get("kind", "openrouter") not in TEXT_ONLY_KINDS]
        # A pure check: no resolver runs while routes are built, so nothing reads
        # a catalog sidecar on the event loop. Whether a sibling is attempted is
        # `_resilient`'s question, asked after the attempt before it.
        return _with_degrades(routes) if _may_send_refs(messages) else routes

    def _dispatch(self, messages: list[dict], conn: dict, usage: dict | None = None):
        # Read before selecting: `for_model` returns a plain list.
        campaign = getattr(messages, "campaign", "")
        degrade = bool(conn.get(DEGRADE))
        if degrade:
            conn = {k: v for k, v in conn.items() if k != DEGRADE}
        # Select per ATTEMPT: retries retain the frozen prompt, while a
        # fallback repacks that same context with its own model's guidance.
        # Ordinary message lists (JSON extraction, judges, drafts) stay ordinary.
        if isinstance(messages, model_guidance.PreparedMessages):
            # `for_connection`, not `for_model`: a prompt with per-attempt
            # tails ("Keep writing") ends the way THIS attempt's connection
            # can take -- a fallback that cannot continue a prefill is sent
            # the instruction instead. Untailed prompts are `for_model`.
            messages = messages.for_connection(conn, effective_model(conn))
        if usage is not None:
            usage["images"] = 0
        if not content_parts.needs_lowering(messages):
            return self._provider(messages, conn, usage)
        return self._lowered(messages, conn, usage, campaign, degrade)

    async def _lowered(self, messages: list[dict], conn: dict, usage: dict | None,
                       campaign: str, degrade: bool):
        """`messages` lowered for `conn`, then streamed (#377).

        Lowering resolves the image budget (a catalog sidecar read) and loads
        pictures (a decode, cached), so it runs off the event loop. It runs
        inside the attempt, so its time counts against the first-delta idle
        bound: a pathological stall here is recorded as that connection's
        timeout -- accepted, since a warm picture is a cache hit and a cold one
        is one bounded decode. The provider stream is closed in `finally`, so a
        caller's close or a timeout still reaches httpx exactly as `_guard` and
        `_resilient` intend."""
        lowered, sent = await asyncio.to_thread(self._lower, messages, conn, campaign, degrade)
        if usage is not None:
            usage["images"] = sent
        inner = self._provider(lowered, conn, usage)
        try:
            async for chunk in inner:
                yield chunk
        finally:
            aclose = getattr(inner, "aclose", None)
            if aclose is not None:
                await aclose()

    def _lower(self, messages: list[dict], conn: dict, campaign: str,
               degrade: bool) -> tuple[list[dict], int]:
        keep = 0 if degrade else self._image_budget(conn)
        if keep <= 0:
            return content_parts.as_text(messages), 0
        return content_parts.as_images(messages, keep, lambda part: self._load(campaign, part))

    def _image_budget(self, conn: dict) -> int:
        """How many images `conn` may be sent now. Never more than 0 for a kind
        whose client cannot carry a part, and 0 when the resolver is missing or
        raises: a broken lookup must not fail a turn the text would serve."""
        if self._images is None or conn.get("kind", "openrouter") in TEXT_ONLY_KINDS:
            return 0
        try:
            return max(0, int(self._images(conn)))
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("could not resolve whether %r reads images: %s", _label(conn), exc)
            return 0

    def _load(self, campaign: str, part: dict) -> str | None:
        if self._load_image is None:
            return None
        try:
            return self._load_image(campaign, part)
        except Exception as exc:  # noqa: BLE001 - one picture is never worth a turn
            log.warning("could not load a post image to send: %s", exc)
            return None

    def _provider(self, messages: list[dict], conn: dict, usage: dict | None):
        kind = conn.get("kind", "openrouter")
        # Decided per ATTEMPT against that attempt's own connection, so a
        # fallback of a different kind is held to what ITS backend takes
        # (`llm_sampling.effective`, the one function that decides). Passed
        # only when there is something to send: a provider call with no preset
        # is byte-for-byte the call it was before presets existed.
        controls = llm_sampling.effective(conn)
        applied, dropped = llm_sampling.split(conn)
        reasoning = llm_sampling.reasoning_wire(controls)
        if dropped:
            log.debug("sampler preset on %r: not sent %s", _label(conn),
                      ", ".join(f"{d['param']} ({d['reason']})" for d in dropped))
        if kind == "claude":
            return self._claude.stream(messages, effective_model(conn), usage=usage)
        if kind == "anthropic":
            # The whole body share, not `split`'s sampler half: `max_tokens` is
            # always in it (the API requires one), and `thinking` /
            # `output_config` are the reasoning control's.
            return self._anthropic.stream(
                messages, conn.get("model", ""), conn.get("api_key", ""), usage=usage,
                base_url=conn.get("base_url", ""), effective=controls["effective"])
        if kind == "openai_compatible":
            # Its reasoning travels as the adapter's own keyword, as the GLM
            # setting always has (`llm_reasoning.glm_effort`, via `effective`).
            effort = reasoning.get("reasoning_effort", "")
            return self._openai_compatible.stream(
                messages, conn.get("model", ""), conn.get("api_key", ""),
                conn.get("base_url", ""), strict=conn.get("post_process") == "strict",
                usage=usage, **({"reasoning_effort": effort} if effort else {}),
                **({"sampling": applied} if applied else {}))
        sampling = {**applied, **reasoning}
        return self._openrouter.stream(messages, conn["model"], conn.get("api_key", ""),
                                       usage=usage, **({"sampling": sampling} if sampling else {}))

    def stream(self, messages: list[dict], conn: dict, usage: dict | None = None):
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
        """
        try:
            sink = self._capture() if self._capture is not None else None
        except Exception:  # noqa: BLE001 - failed diagnostic setup must not stop generation
            sink = None
        return _resilient(lambda route, holder: self._dispatch(messages, route, holder),
                          self._usable_routes(messages, conn), self._timeout_seconds(),
                          usage=usage, observer=self._observer, capture=sink)

    async def complete(self, messages: list[dict], conn: dict,
                       usage: dict | None = None) -> str:
        return "".join([chunk async for chunk in self.stream(messages, conn, usage)])

    async def single(self, messages: list[dict], conn: dict,
                     usage: dict | None = None) -> str:
        """Exactly one attempt on `conn`, joined: the model test call's way in.

        No retry, no fallback route and no degrade sibling -- the route list is
        `conn` with zero retries and nothing else, so `_resilient` makes one
        attempt and raises what it raised, exactly as `stream` would have from
        that attempt. A test is a question about ONE model on ONE provider, and
        each further attempt is money spent answering a different question:
        a fallback's success would be recorded against a model that never
        answered, a retry would pay twice for a 429, and a text-only re-send
        would call a model that refused the picture one that read it.

        Everything else is `stream`'s, through the same `_resilient` and
        `_dispatch`: the idle bound, the per-attempt `usage` stamp a
        `store.usage.Meter` files, the incoming-response capture, and
        `_provider`'s `llm_sampling.effective` per adapter -- which is how the
        probe's reply cap reaches the Anthropic API as its required
        `max_tokens`.

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
                          [(conn, 0)], self._timeout_seconds(), usage=usage, capture=sink)
        return "".join([chunk async for chunk in agen])

    def note_outcome(self, conn: dict, error: LLMError | None) -> None:
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
        """
        _observe(self._observer, conn, error)

    async def list_models(self, conn: dict) -> list[dict]:
        """The catalog `conn`'s provider offers, normalized (#149).

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
        kind = conn.get("kind", "openrouter")
        if kind not in LISTABLE_KINDS:
            # Unreachable through the API — the route refuses these before it
            # gets here, with a message about the kind rather than a transport
            # failure. Kept as a backstop so a future caller that forgets the
            # check gets an error rather than an AttributeError from a provider
            # with no `list_models`.
            raise LLMError("bad_response", f"{kind} connections have no model catalog")
        if kind == "openai_compatible":
            return await self._openai_compatible.list_models(
                conn.get("base_url", ""), conn.get("api_key", ""))
        if kind == "anthropic":
            return await self._anthropic.list_models(conn.get("api_key", ""),
                                                     conn.get("base_url", ""))
        return await self._openrouter.list_models(conn.get("api_key", ""))

    async def check(self, conn: dict) -> None:
        """Ask `conn`'s provider whether it can serve. Returns on yes, raises
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
        kind = conn.get("kind", "openrouter")
        if kind == "claude":
            await self._claude.probe(effective_model(conn))
        elif kind == "openai_compatible":
            await self._openai_compatible.probe(conn.get("base_url", ""),
                                                conn.get("api_key", ""))
        elif kind == "anthropic":
            await self._anthropic.probe(conn.get("api_key", ""), conn.get("base_url", ""))
        else:
            await self._openrouter.probe(conn.get("api_key", ""))

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
        await self._anthropic.aclose()
