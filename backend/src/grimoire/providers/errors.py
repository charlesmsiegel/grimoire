"""Provider failure kinds and the adapter's errors (none is an ``OSError``).

An ``OSError`` can be swallowed by file-handling code that expects one, so a
refused or failed model call must never look like one.
"""

from __future__ import annotations

#: Every failure kind an adapter result or transport error can carry.
KINDS = frozenset({
    "network", "timeout", "tls", "peer_not_loopback", "auth", "account_limit", "rate_limit",
    "bad_request", "server_error", "bad_response", "truncated", "provider_error",
    "consumer_error",
})
#: Kinds worth another attempt, and only while nothing was delivered to a consumer.
RETRYABLE = frozenset({"timeout", "network", "rate_limit", "server_error", "truncated",
                       "bad_response"})


class ProviderError(Exception):
    """A failed provider exchange. ``diagnostic`` (the provider's own text,
    which may echo the prompt) is kept out of ``str`` and ``repr``."""

    def __init__(self, kind: str, *, status: int | None = None, retry_after: str | None = None,
                 error_type: str | None = None, error_code: str | None = None,
                 diagnostic: str | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown provider error kind: {kind!r}")
        super().__init__(kind if status is None else f"{kind} ({status})")
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.error_type = error_type
        self.error_code = error_code
        self.diagnostic = diagnostic

    def __repr__(self) -> str:
        return f"ProviderError({self.kind!r}, status={self.status!r}, code={self.error_code!r})"


class CallRefused(Exception):
    """The adapter refused to issue a call; nothing was sent."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TransportError(Exception):
    """A transport failure in one phase: ``connect`` (nothing sent), ``send`` or ``read``."""

    PHASES = ("connect", "send", "read")

    def __init__(self, kind: str, phase: str) -> None:
        if kind not in KINDS or phase not in self.PHASES:
            raise ValueError(f"bad transport error {kind!r}/{phase!r}")
        super().__init__(f"{kind} during {phase}")
        self.kind = kind
        self.phase = phase
