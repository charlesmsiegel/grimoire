"""Exception hierarchy for the LLM Gateway.

`TransientError`, `RateLimitError`, and `TimeoutError` are retriable.
`PermanentError` and its subclasses are not — the gateway surfaces them
straight to the caller.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base for every gateway-originated error."""

    http_status = 500


class RouteNotFoundError(GatewayError):
    """No route is configured for the requested task."""

    http_status = 503

    def __init__(self, task: str) -> None:
        super().__init__(f"no route configured for task {task!r}")
        self.task = task


class ProviderNotFoundError(GatewayError):
    """A route points at a provider id that is not loaded."""

    http_status = 503

    def __init__(self, provider_id: str, kind: str = "llm") -> None:
        super().__init__(f"{kind} provider {provider_id!r} is not loaded")
        self.provider_id = provider_id
        self.kind = kind


class TransientError(GatewayError):
    """Provider reports a temporary failure; the gateway will retry."""

    http_status = 500


class RateLimitError(TransientError):
    """Provider rejected the request because we are over quota."""

    http_status = 429


class PermanentError(GatewayError):
    """Provider failure that retries cannot resolve."""

    http_status = 500


class AuthenticationError(PermanentError):
    """Bad or missing credentials."""

    http_status = 403


class InvalidRequestError(PermanentError):
    """The request itself is malformed (wrong shape, unsupported field)."""

    http_status = 400


class ContentFilterError(PermanentError):
    """Provider refused on policy grounds."""

    http_status = 400
