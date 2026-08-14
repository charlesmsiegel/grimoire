"""The error type every LLM provider raises.

A leaf module on purpose: the providers need it and `llm.py` needs the
providers, so parking it in `llm.py` made the two import each other (#239).
Nothing here may import from the rest of the package.
"""

from __future__ import annotations

import math


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = "", retry_after: float | None = None):
        super().__init__(detail or kind)
        # missing_key | auth | rate_limit | network | bad_response |
        # missing_dependency | timeout
        self.kind = kind
        self.detail = detail or kind
        #: Seconds the provider itself asked us to wait before trying again,
        #: or None if it did not say (#144). Optional, and defaulted, because
        #: this is raised from dozens of places that have no such information —
        #: only an HTTP error response carries it. `llm._resilient` is the one
        #: reader: a provider that names its own window is more accurate than
        #: any backoff schedule guessing at it.
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
