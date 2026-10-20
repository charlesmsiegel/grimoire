"""Retry transient failures and visit fallback routes only before visible prose."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator

from .. import llm_capture, llm_reasoning, llm_usage, wire
from ..llm_errors import LLMError
from . import _accounting, _lifecycle, _refusals, _routes
from ._routes import _Route as _Route

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


async def _resilient(open_stream, routes: list[_Route], timeout: float,
                     tick: float | None = None,
                     usage: dict | None = None,
                     observer=None, capture: llm_capture.Sink | None = None,
                     counter=None, offered: bool = False) -> AsyncIterator[str]:
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
    carrying that attempt, which `inference.decide` and `inference.generate`
    re-send without the mode.

    `offered` says the call offered tools (01g). A route that refused them
    (`_tools_refusal`) is neither observed nor retried -- the connection
    answered, and a repeat is the same refusal -- and the next route is
    tried; its failure is a `ToolsRefusalError`, composed by `routes_failed`
    as a schema refusal is.

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
            if not (last is not None and last.status in _routes.REJECTED_STATUSES and sent_images > 0):
                continue
            _routes.log.warning("images refused by %r; retried as text", _routes._label(target))
        elif index > 0:
            fell_back = True
            _routes.log.warning("LLM connection %r gave up (%s: %s); falling back to %r",
                        _routes._label((previous or route).target), last.kind, last.detail,
                        _routes._label(target))
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
                    step = delay if _lifecycle.HEARTBEAT_INTERVAL <= 0 else min(delay, _lifecycle.HEARTBEAT_INTERVAL)
                    await asyncio.sleep(step)
                    delay -= step
                    if delay > 0:
                        yield ""  # still here, waiting the provider out
            tries += 1
            _accounting._stamp(usage, route, tries)
            if capture is not None and usage is not None:
                usage[llm_capture.KEY] = llm_capture.Capture(
                    capture, call_id, tries, target.model, target.kind,
                    run_id=llm_capture.run_id(usage))
                llm_capture.emit(usage, "start", None)
            outcome = "interrupted"
            if llm_reasoning.pending(usage):
                yield ""
            agen = _lifecycle._guard(open_stream(route, usage), timeout, tick,
                          pending=lambda: llm_reasoning.pending(usage))
            try:
                async for chunk in agen:
                    sent = sent or bool(chunk)
                    if chunk:
                        llm_usage.note_reply(usage, chunk)
                    yield chunk
                outcome = "complete"
                _accounting._observe(observer, target, None)
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
                await _accounting._estimate(usage, target, counter)
                return
            except LLMError as exc:
                outcome = "error"
                # The primary only -- which includes its own degrade sibling
                # (#377), the same connection re-sent as text. A FALLBACK that
                # refuses a preset has nothing further to skip, and the
                # both-failed message below already names its failure beside
                # the primary's.
                primary = index == 0 or (index == 1 and target.degrade)
                refused = _refusals._preset_refusal(exc, target) if primary and not sent else None
                if refused is not None:
                    # Not observed: the connection answered, and what it
                    # refused was a setting. A health verdict here would mark
                    # it failing for a problem no connection change can fix.
                    raise refused from exc
                tools = _refusals._tools_refusal(exc, offered)
                schema = not tools and _refusals._schema_refusal(exc, target)
                if tools:
                    # Not observed, for the schema's reason: the connection
                    # answered and refused a feature, and still serves every
                    # call that offers none.
                    _routes.log.warning("tools refused by %r: %s", _routes._label(target), _refusals._said(exc))
                elif schema:
                    # Not observed either, for the preset's reason (CODE-M5):
                    # the connection answered and refused the mode -- a
                    # catalog that over-advertised it -- and it still serves
                    # every generate call. The next route, sent the same
                    # prompt, is tried as for any failure; should every route
                    # fail, the error carries this attempt, for `decide` to
                    # re-send once without the mode.
                    _routes.log.warning("structured output refused by %r: %s", _routes._label(target),
                                _refusals._said(exc))
                else:
                    _accounting._observe(observer, target, exc)
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
                last = (_refusals.ToolsRefusalError(_refusals._said(exc), status=exc.status) if tools
                        else _refusals.SchemaRefusalError(exc.kind, _refusals._said(exc), exc.retry_after,
                                                status=exc.status, code=exc.code)
                        if schema else exc)
                sent_images = usage.get("images", 0) if usage is not None else 0
                # The primary's word is its first failure -- or, when its own
                # degrade sibling ran, that sibling's (#377).
                first = last if first is None or (primary and index == 1) else first
                retryable = (exc.kind in RETRYABLE_KINDS and not schema and not tools
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
    error = _refusals.routes_failed(words)
    if not schema_refused:
        raise error
    raise _refusals.SchemaRefusalError(error.kind, error.detail, error.retry_after,
                             status=error.status, code=error.code,
                             attempts=tuple(schema_refused.get(pos) for pos in range(len(words))),
                             words=words)
