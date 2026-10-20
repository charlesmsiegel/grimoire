"""common: errors responsibilities."""

from __future__ import annotations

import math

from fastapi import HTTPException

from ...llm_errors import LLMError


def _fresh_or_409(expected: str | None, current: str | None) -> None:
    """Refuse a write whose base is no longer what is on disk (#35).

    The store is a folder of markdown files the user is invited to point at
    Dropbox or Syncthing, so "somebody else changed this while you had it open"
    is an ordinary event here rather than an exotic one -- and until now it
    resolved as last-writer-wins, silently. A save that carries the rev it read
    turns that into a 409 the editor can act on.

    An empty or absent `expected` means the caller opted out (see
    `EntityUpdate.rev`); a client that has no rev to offer must not be turned
    into one that can never write. `current is None` cannot happen for a record
    that exists, so it only reaches here for one that has been deleted
    underneath -- also a conflict, and one whose 404-shaped alternative would be
    a worse answer: the record is exactly as gone as the user's unsaved edit is
    real.

    Two limits, both narrower than the window this closes and neither of them
    closed by it:

    - **Check and write are not atomic.** Nothing holds a lock across the two,
      so a writer landing in that microsecond still wins silently. What the
      precondition removes is the *human*-scale window -- the minutes a record
      sits open in an editor -- which is the one a sync client or a second
      device actually lands in. Two of this app's own tabs saving the same
      record in the same instant can still both pass. Closing that needs the
      compare and the write under one lock, and entity writes take no lock
      today (`store/locks/_domain.py`: entities are outside the campaign domain).
    - **As sharp as `statcache`, and no sharper.** `current` comes from a hash
      memoized on `(path, mtime_ns, size)`, so an external write landing with
      the same size inside the filesystem's timestamp granularity is invisible
      here exactly as it is to every other reader in the app. `statcache`'s
      one-second racy window narrows that to a case a synced folder is unlikely
      to produce; closing it outright would mean re-reading every record on
      every request, which is the cost that cache exists to avoid.
    """
    if not expected or expected == current:
        return
    raise HTTPException(status_code=409, detail={
        "kind": "stale_record", "rev": current,
        "detail": "This record changed on disk since you opened it — "
                  "reload to see the current version before saving."})


# ---- the LLM error taxonomy (#213) ----
#: The HTTP status each `LLMError.kind` answers with.
#:
#: Every non-stream LLM route used to answer 502 for all of them. The `kind` was
#: in the body the whole time, but a status code is the part a browser, a proxy,
#: a retry helper and a log reader all understand without knowing this app's
#: vocabulary -- and 502 told every one of them the same wrong thing: that the
#: provider had misbehaved, when it had in fact answered clearly.
#:
#: Streaming routes are deliberately not covered. By the time a provider fails
#: there, the reply is already a `200 text/event-stream`, so their errors stay
#: in-band SSE events carrying the same `kind` (`routes/streaming/_streams.py`). No status code
#: can be sent after the headers are gone, and inventing one for the body would
#: be a second, disagreeing taxonomy.
_LLM_STATUS = {
    # Not 401. This API has no authentication of its own, so 401 would be a
    # claim about the *caller's* credentials that is simply untrue -- and RFC
    # 9110 requires a `WWW-Authenticate` on one, which there is no honest value
    # for. An upstream that refused our key is a gateway that could not serve
    # the request, which is what 502 says. The `kind` still tells the frontend
    # which gateway failure this is.
    "auth": 502,
    # Setup rather than failure: nothing can be sent until the user changes a
    # connection. 409 because `require_inference` already answers exactly that
    # for exactly this, and two codes for one condition would be worse than
    # either alone. `missing_dependency` is the same condition with a different
    # piece missing -- a `claude` connection whose SDK is not installed sails
    # past `require_inference`, which checks a key and a base URL, neither of
    # which that kind has -- so it gets the same answer rather than a second one.
    "missing_key": 409,
    "missing_dependency": 409,
    # The one this issue is named for. A caller that can tell "slow down" from
    # "the provider is broken" can wait and try again; one reading 502 cannot.
    "rate_limit": 429,
    # Upstream never finished, or our own ceiling expired waiting on it
    # (`_bounded_call`). Both are 504 rather than 502: the gateway reached the
    # provider fine, it just never got an answer back.
    "timeout": 504,
    # Could not talk to the provider at all, which includes httpx's own connect
    # and read timeouts -- the providers raise `network` for every `HTTPError`.
    # That looks like a `timeout` in the making and is deliberately left alone:
    # `network` is retryable and `timeout` is not (`llm.RETRYABLE_KINDS` says
    # why), so reclassifying a refused connection would stop it being retried.
    # 502 is also the right answer for it -- there was no answer to be late.
    "network": 502,
    "bad_response": 502,
}
#: What a kind with no entry above answers with. It cannot happen today -- a
#: test holds `_LLM_STATUS`'s keys to `llm_errors.KINDS` -- but this runs on the
#: failure path, where a KeyError would replace the provider's error with our
#: own, and 502 is the answer every kind gave before this map existed.
_LLM_STATUS_FALLBACK = 502


def _retry_after_header(seconds: float | None) -> str | None:
    """A provider-named wait as a `Retry-After` value, or None for no header.

    Rounded *up* to whole seconds: the header's delta-seconds form is an
    integer, and rounding down would advise a retry fractionally inside a window
    the provider already said it would reject.

    Non-finite and non-positive both answer None. `llm_errors.retry_after_seconds`
    already rejects those where a header is parsed, but a provider client is free
    to construct an `LLMError` with any float it likes, and `Retry-After: inf` is
    worse than no header at all.
    """
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return None
    return str(math.ceil(seconds))


def _llm_http_error(exc: LLMError) -> HTTPException:
    """The HTTP failure one non-stream LLM error becomes (#213).

    One function rather than a raise at each of the ten call sites, because the
    reason those sites were all wrong is that they each spelled the answer out
    themselves. Callers `raise _llm_http_error(exc) from exc`: the provider failure
    IS the cause, and dropping it leaves a traceback that starts at the `raise`
    with nothing saying which call failed or why.

    A rate limit carries the provider's own `Retry-After` when it named one.
    Gated on the *kind* rather than on the status being 429, because the kind is
    what makes the window mean anything: `retry_after` is set from an error
    response's header, and only a rate-limiter's is advice about when this
    request will be served. Reading the status instead would tie the header to a
    number that could be revisited, and silently drop it when it was.

    It can be absent even for a provider that did name one -- `llm._resilient`
    waits such a window out and re-raises whatever the *last* attempt said -- so
    the header is a bonus, never a promise, which is exactly what RFC 9110 makes
    it.
    """
    status = _LLM_STATUS.get(exc.kind, _LLM_STATUS_FALLBACK)
    retry_after = _retry_after_header(exc.retry_after) if exc.kind == "rate_limit" else None
    return HTTPException(
        status_code=status,
        detail={"detail": exc.detail, "kind": exc.kind},
        headers={"Retry-After": retry_after} if retry_after else None)


def run_error(exc: HTTPException) -> dict:
    """One refusal as the error a POLLING client reads off a detached run.

    Carries the HTTP status the same failure would have had when these routes
    answered synchronously, so a client's existing handling of `409
    missing_key` or `504 timeout` needs no second shape to understand -- the
    only thing that moved is where it reads it from. That equivalence is the
    whole migration contract for a computing route: what the client sees must
    not depend on whether the work was detached.

    Here rather than in `runs`, which the `draft` routes' modules already reach
    through this one, and which may not import anything that would let it build
    an `HTTPException` from an `LLMError` (`_llm_http_error` lives here).

    **`retry_after` moves into the BODY, because a header cannot survive
    detachment.** A rate limit's window is advice about when this work can be
    tried again, and the response that would have carried it as `Retry-After`
    went out as a 202 before the provider was even called -- the same thing
    that has always been true of a streamed failure, whose errors are SSE
    events for exactly this reason. Dropping it instead would silently lose the
    one number a client needs to schedule its retry.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        out = {"kind": detail.get("kind", "refused"),
               "detail": str(detail.get("detail", "")),
               "status": exc.status_code}
    else:
        out = {"kind": "refused", "detail": str(detail), "status": exc.status_code}
    window = (exc.headers or {}).get("Retry-After")
    return {**out, "retry_after": window} if window else out
