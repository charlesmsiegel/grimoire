"""Exception hierarchy for the LLM Gateway.

`TransientError`, `RateLimitError`, and `TimeoutError` are retriable.
`PermanentError` and its subclasses are not — the gateway surfaces them
straight to the caller.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base for every gateway-originated error."""


class RouteNotFoundError(GatewayError):
    """No route is configured for the requested task."""

    def __init__(self, task: str) -> None:
        super().__init__(f"no route configured for task {task!r}")
        self.task = task


class ProviderNotFoundError(GatewayError):
    """A route points at a provider id that is not loaded."""

    def __init__(self, provider_id: str, kind: str = "llm") -> None:
        super().__init__(f"{kind} provider {provider_id!r} is not loaded")
        self.provider_id = provider_id
        self.kind = kind


class TransientError(GatewayError):
    """Provider reports a temporary failure; the gateway will retry."""


class RateLimitError(TransientError):
    """Provider rejected the request because we are over quota."""


class PermanentError(GatewayError):
    """Provider failure that retries cannot resolve."""


class AuthenticationError(PermanentError):
    """Bad or missing credentials."""


class InvalidRequestError(PermanentError):
    """The request itself is malformed (wrong shape, unsupported field)."""


class ContentFilterError(PermanentError):
    """Provider refused on policy grounds."""
