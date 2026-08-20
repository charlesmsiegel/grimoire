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
    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None):
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
