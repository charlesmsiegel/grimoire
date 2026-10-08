"""The error type every LLM provider raises.

A leaf module on purpose: the providers need it and `llm.py` needs the
providers, so parking it in `llm.py` made the two import each other (#239).
Nothing here may import from the rest of the package.
"""

from __future__ import annotations

import math

#: Every failure kind an `LLMError` carries.
#:
#: A named set rather than the comment this used to be, because something now
#: has to answer for each one: `routes.common` maps kind to an HTTP status
#: (#213) and a test holds that map to exactly this set, so a kind added here
#: without a status decided for it fails loudly instead of quietly arriving as
#: the fallback 502 nobody chose.
KINDS = frozenset({
    "missing_key", "auth", "rate_limit", "network", "bad_response",
    "missing_dependency", "timeout",
})


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None,
                 status: int | None = None, code: str | None = None, *,
                 words: tuple[LLMError, ...] = ()):
        super().__init__(detail or kind)
        #: One of `KINDS` -- unvalidated on purpose. This constructor runs on
        #: the failure path, where raising over a typo would replace the error
        #: the caller needs to see with one about our own bookkeeping; the
        #: status map answers an unknown kind with 502 instead.
        self.kind = kind
        self.detail = detail or kind
        #: Seconds the provider itself asked us to wait before trying again,
        #: or None if it did not say (#144). Optional, and defaulted, because
        #: this is raised from dozens of places that have no such information —
        #: only an HTTP error response carries it. Two readers: `llm._resilient`
        #: waits it out rather than trusting its own backoff schedule, because a
        #: provider that names its own window is more accurate than any guess at
        #: it, and `routes.common` passes it on to the caller as the
        #: `Retry-After` of the 429 it becomes (#213).
        self.retry_after = retry_after
        #: The provider's HTTP status, for an error that came from an HTTP
        #: response, else None. `kind` cannot carry the distinctions the facade
        #: needs: `bad_response` is both "the provider refused this request"
        #: and "the provider broke" (a 500). Read by `llm._resilient` for two
        #: decisions: a 400/422 answering a request that carried sampler
        #: parameters is the preset being refused, not the connection failing,
        #: so it must not be handed to the fallback (see the sampler-presets
        #: spec); and a refusal of a request that carried pictures is retried
        #: once as text on the same connection (#377).
        self.status = status
        #: The provider's machine-readable error code, where its error body
        #: names one beside the message (the Anthropic API's
        #: `error.details.error_code`), else None. Read by `account_limit`:
        #: a 429 that is a spend cap and one that is a rate limit differ only
        #: there.
        self.code = code
        #: When this error is several routes' failures composed into one
        #: (`llm.routes_failed`), each route's own -- the primary first --
        #: else empty. The composition's one kind and detail are written for
        #: a reader; a caller that needs a fact only one route's failure
        #: carried asks the routes. The one today is a caller's own clock: an
        #: absorb whose deadline stopped a prompt-only re-send (`inference`
        #: re-sends each route that refused the structured field) still has
        #: to say the clock is why (`routes.scenes._budget_overrun`), and the
        #: composed sentence no longer equals the clock's.
        self.words = words


#: How the Anthropic API opens the message of a 400 that is a spend limit the
#: user set -- "You have reached your specified API usage limits" and its
#: "... workspace API usage limits" twin. Matched as a prefix, case-folded.
SPEND_LIMIT_PREFIX = "you have reached your specified"
#: The error codes that make a 429 the account's spend cap rather than a rate
#: limit (no retry-after: waiting does not lift it).
SPEND_LIMIT_CODES = frozenset({"enforced_spend_limit_reached"})


def account_limit(exc: LLMError) -> bool:
    """Whether `exc` is the ACCOUNT refused for money, not the request: a spend
    limit the user set (a 400 whose message opens `SPEND_LIMIT_PREFIX`), the
    tier's spend cap (a 429 carrying a `SPEND_LIMIT_CODES` code), or no credit
    at all (any 402).

    One answer for every reader that has to tell it apart: it is no verdict on
    any model (the model test files nothing for it), every further request
    meets the same limit (the test sends nothing more), and it is never a
    sampler preset refused -- the 400 carries no parameter name, whatever its
    wording happens to contain."""
    status = exc.status
    if status == 402:
        return True
    if status == 429:
        return exc.code in SPEND_LIMIT_CODES
    if status == 400:
        return (exc.detail or "").lstrip().lower().startswith(SPEND_LIMIT_PREFIX)
    return False


def retry_after_seconds(headers) -> float | None:
    """A response's `Retry-After` as a number of seconds, or None.

    Lives here rather than in a provider because both providers need it and
    this is the leaf module they already share — the same reason `LLMError`
    itself is here (#239).

    Only the delta-seconds form is read. The HTTP-date form is equally legal
    and every LLM API in practice sends the numeric one; parsing a date would
    mean reading a clock inside an error path to compute a delta, and getting
    *that* wrong yields a wait of thousands of seconds rather than none. Every
    unreadable case therefore answers None, which means "back off on our own
    schedule" — the safe direction, and the behaviour of every version before
    this one.

    Non-finite is rejected explicitly: `float("inf")` and `float("nan")` both
    parse, and either would reach the caller as a comparison that is never
    true or a wait that never ends.
    """
    try:
        raw = (headers.get("retry-after") or "").strip()
    except AttributeError:
        return None  # not a headers mapping at all
    try:
        seconds = float(raw)
    except ValueError:
        return None  # absent, an HTTP-date, or junk
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds
